from __future__ import annotations

import hashlib
import os
import re
import signal
import subprocess
import sys
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import PurePath
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from medbot.errors import ApplicationError

PDF_MIME_TYPE = "application/pdf"
SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ ()-]{0,249}\.pdf", re.IGNORECASE)
PDF_LITERAL = re.compile(rb"\(((?:\\.|[^\\)])*)\)\s*Tj")
PAGE_MARKER = re.compile(rb"/Type\s*/Page(?:\s|/)")


class SourceRegion(BaseModel):
    """Normalized coordinates in the rendered page's top-left coordinate system."""

    model_config = ConfigDict(extra="forbid")
    x: float = Field(ge=0, le=1, allow_inf_nan=False)
    y: float = Field(ge=0, le=1, allow_inf_nan=False)
    width: float = Field(gt=0, le=1, allow_inf_nan=False)
    height: float = Field(gt=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def within_page(self) -> SourceRegion:
        if self.x + self.width > 1.00001 or self.y + self.height > 1.00001:
            raise ValueError("Region lies outside the page")
        return self


@dataclass(frozen=True, slots=True)
class ExtractedTextBlock:
    id: str
    document_id: str
    page_number: int
    block_index: int
    text: str
    text_sha256: str
    start_offset: int
    end_offset: int
    extraction_method: str = "fixture"
    ocr_confidence: float | None = None
    region: SourceRegion | None = None


class DocumentExtractor(Protocol):
    def extract(self, document_id: str, content: bytes) -> tuple[ExtractedTextBlock, ...]: ...


class PageTextLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(ge=1, le=20)
    text: str = Field(min_length=1, max_length=2000)
    method: str = Field(pattern=r"^(pdf_text|ocr)$")
    confidence: float | None = Field(default=None, ge=0, le=1)
    region: SourceRegion


class PDFTextResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lines: list[PageTextLine] = Field(max_length=2000)


class PDFDocumentExtractor:
    """Bounded PDFium/Tesseract subprocess; native parsing never runs in API threads."""

    def extract(self, document_id: str, content: bytes) -> tuple[ExtractedTextBlock, ...]:
        result = PDFTextResult.model_validate_json(run_pdf_worker(content, "extract"))
        return tuple(
            ExtractedTextBlock(
                id=str(uuid4()),
                document_id=document_id,
                page_number=line.page,
                block_index=index,
                text=line.text,
                text_sha256=sha256_hex(line.text.encode()),
                start_offset=0,
                end_offset=len(line.text),
                extraction_method=line.method,
                ocr_confidence=line.confidence,
                region=line.region,
            )
            for index, line in enumerate(result.lines)
        )


def render_pdf_page(content: bytes, page: int) -> bytes:
    return run_pdf_worker(content, "render", page)


def run_pdf_worker(content: bytes, operation: str, page: int = 1) -> bytes:
    try:
        with tempfile.TemporaryDirectory(prefix="medbot-document-") as directory:
            process = subprocess.Popen(
                [sys.executable, "-m", "medbot.pdf_worker", operation, str(page)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env={
                    "PATH": os.environ.get("PATH", os.defpath),
                    "LANG": "C.UTF-8",
                    "OMP_THREAD_LIMIT": "1",
                    "PYTHONIOENCODING": "utf-8",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "TMPDIR": directory,
                },
            )
            try:
                stdout, _ = process.communicate(content, timeout=60)
            except subprocess.TimeoutExpired:
                # The worker may exit between the timeout and cancellation.
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise
    except (subprocess.TimeoutExpired, OSError):
        raise ApplicationError(
            "document_processing_unavailable",
            "Document processing timed out or is unavailable",
            503,
        ) from None
    if process.returncode != 0:
        codes = {
            2: ("invalid_pdf", "This PDF is unreadable or malformed", 422),
            3: ("encrypted_pdf", "Upload an unencrypted PDF", 422),
            4: ("document_limit_exceeded", "The PDF exceeds page, image, or text limits", 422),
            5: (
                "document_text_unavailable",
                "No readable text was found; upload a clearer scan",
                422,
            ),
            6: ("ocr_unavailable", "The local OCR engine is unavailable", 503),
            7: ("page_not_found", "The source page does not exist", 404),
        }
        code, message, status = codes.get(
            process.returncode or 1,
            ("document_processing_unavailable", "Document processing is unavailable", 503),
        )
        raise ApplicationError(code, message, status)
    if len(stdout) > 12 * 1024 * 1024:
        raise ApplicationError("document_limit_exceeded", "The extracted result is too large", 422)
    return stdout


def validate_pdf(filename: str | None, content_type: str | None, content: bytes) -> str:
    if filename is None or not filename:
        raise ApplicationError("invalid_filename", "A safe PDF filename is required", 422)
    if (
        PurePath(filename).name != filename
        or "/" in filename
        or "\\" in filename
        or ".." in filename
        or not SAFE_FILENAME.fullmatch(filename)
    ):
        raise ApplicationError("invalid_filename", "The PDF filename is not allowed", 422)
    if content_type != PDF_MIME_TYPE:
        raise ApplicationError("invalid_content_type", "Only PDF uploads are accepted", 415)
    if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-2048:]:
        raise ApplicationError("invalid_pdf", "The uploaded file is not a valid PDF", 422)
    return filename


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class FixtureDocumentExtractor:
    """Extracts text operators from deterministic, uncompressed synthetic PDFs."""

    def extract(self, document_id: str, content: bytes) -> tuple[ExtractedTextBlock, ...]:
        page_starts = [match.start() for match in PAGE_MARKER.finditer(content)]
        blocks: list[ExtractedTextBlock] = []
        for index, match in enumerate(PDF_LITERAL.finditer(content)):
            decoded = _decode_pdf_literal(match.group(1)).strip()
            if not decoded:
                continue
            page = max(1, sum(start < match.start() for start in page_starts))
            blocks.append(
                ExtractedTextBlock(
                    id=str(uuid4()),
                    document_id=document_id,
                    page_number=page,
                    block_index=index,
                    text=decoded,
                    text_sha256=sha256_hex(decoded.encode("utf-8")),
                    start_offset=0,
                    end_offset=len(decoded),
                )
            )
        if not blocks:
            raise ApplicationError(
                "document_text_unavailable",
                "No fixture text could be extracted from this PDF",
                422,
            )
        return tuple(blocks)


def _decode_pdf_literal(raw: bytes) -> str:
    text = raw.decode("latin-1")
    replacements = {
        r"\(": "(",
        r"\)": ")",
        r"\\": "\\",
        r"\n": "\n",
        r"\r": "\r",
        r"\t": "\t",
    }
    for escaped, value in replacements.items():
        text = text.replace(escaped, value)
    return re.sub(r"\\([0-7]{1,3})", lambda match: chr(int(match.group(1), 8)), text)
