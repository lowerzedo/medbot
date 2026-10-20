"""Isolated native PDF parser/OCR worker. Stdout is the result; stderr is never exposed."""

from __future__ import annotations

import csv
import io
import math
import os
import resource
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

import pypdfium2 as pdfium  # type: ignore[import-untyped]

from medbot.document_processing import PageTextLine, PDFTextResult, SourceRegion

MAX_PAGES = 20
MAX_PAGE_PIXELS = 8_000_000
MAX_LINES = 2000
MAX_CHARACTERS = 80_000


class PDFRejected(Exception):
    def __init__(self, code: int) -> None:
        self.code = code


def region(
    left: float, top: float, right: float, bottom: float, width: float, height: float
) -> SourceRegion:
    x = max(0.0, min(1.0, left / width))
    y = max(0.0, min(1.0, top / height))
    return SourceRegion(
        x=x,
        y=y,
        width=max(0.00001, min(1 - x, (right - left) / width)),
        height=max(0.00001, min(1 - y, (bottom - top) / height)),
    )


def parse_tesseract_tsv(tsv: str, page: int, width: int, height: int) -> list[PageTextLine]:
    groups: dict[tuple[str, str, str], list[tuple[str, float, int, int, int, int]]] = {}
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = row.get("text", "").strip()
        if row.get("level") != "5" or not text:
            continue
        confidence = float(row["conf"])
        if not math.isfinite(confidence) or confidence < 0:
            continue
        key = (row["block_num"], row["par_num"], row["line_num"])
        groups.setdefault(key, []).append(
            (
                text,
                confidence,
                int(row["left"]),
                int(row["top"]),
                int(row["width"]),
                int(row["height"]),
            )
        )
    lines = []
    for words in groups.values():
        text = " ".join(word[0] for word in words)
        if len(text) > 2000:
            raise PDFRejected(4)
        left = min(word[2] for word in words)
        top = min(word[3] for word in words)
        right = max(word[2] + word[4] for word in words)
        bottom = max(word[3] + word[5] for word in words)
        lines.append(
            PageTextLine(
                page=page,
                text=text,
                method="ocr",
                confidence=min(word[1] for word in words) / 100,
                region=region(left, top, right, bottom, width, height),
            )
        )
    return lines


def text_lines(page: object, number: int) -> list[PageTextLine]:
    # PDFium is an untyped native boundary; values are validated into PageTextLine immediately.
    text_page = page.get_textpage()  # type: ignore[attr-defined]
    width, height = page.get_size()  # type: ignore[attr-defined]
    if text_page.count_chars() > MAX_CHARACTERS:
        raise PDFRejected(4)
    lines: list[PageTextLine] = []
    letters: list[str] = []
    boxes: list[tuple[float, float, float, float]] = []

    def flush() -> None:
        value = "".join(letters).strip()
        if len(value) > 2000:
            raise PDFRejected(4)
        if value and boxes:
            left = min(box[0] for box in boxes)
            bottom = min(box[1] for box in boxes)
            right = max(box[2] for box in boxes)
            top = max(box[3] for box in boxes)
            lines.append(
                PageTextLine(
                    page=number,
                    text=value,
                    method="pdf_text",
                    region=region(left, height - top, right, height - bottom, width, height),
                )
            )
        letters.clear()
        boxes.clear()

    try:
        for index in range(text_page.count_chars()):
            char = text_page.get_text_range(index, 1)
            if char in {"\r", "\n"}:
                flush()
            else:
                letters.append(char)
                if char.strip():
                    boxes.append(tuple(text_page.get_charbox(index)))
        flush()
    finally:
        text_page.close()
    return lines


def extract(content: bytes, render_page: int | None = None) -> bytes:
    try:
        document = pdfium.PdfDocument(content)
    except pdfium.PdfiumError as error:
        raise PDFRejected(3 if error.err_code == 4 else 2) from None
    with closing(document):
        if not 0 < len(document) <= MAX_PAGES:
            raise PDFRejected(4)
        if render_page is not None and not 1 <= render_page <= len(document):
            raise PDFRejected(7)
        lines = []
        for index in [render_page - 1] if render_page is not None else range(len(document)):
            with closing(document[index]) as page:
                width, height = page.get_size()
                if (
                    width <= 0
                    or height <= 0
                    or max(width, height) * (200 / 72) > 8000
                    or width * height * (200 / 72) ** 2 > MAX_PAGE_PIXELS
                ):
                    raise PDFRejected(4)
                if render_page is not None:
                    with closing(page.render(scale=150 / 72)) as bitmap:
                        data = io.BytesIO()
                        bitmap.to_pil().save(data, format="PNG")
                        return data.getvalue()
                native = text_lines(page, index + 1)
                has_image = any(
                    obj.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE for obj in page.get_objects()
                )
                if native and not has_image and page.get_rotation() == 0:
                    lines.extend(native)
                else:
                    with (
                        closing(page.render(scale=200 / 72)) as bitmap,
                        tempfile.TemporaryDirectory(prefix="medbot-ocr-") as directory,
                    ):
                        image = bitmap.to_pil()
                        filename = Path(directory) / "page.png"
                        image.save(filename)
                        try:
                            result = subprocess.run(
                                [
                                    "tesseract",
                                    str(filename),
                                    "stdout",
                                    "-l",
                                    "eng",
                                    "--psm",
                                    "6",
                                    "tsv",
                                ],
                                capture_output=True,
                                timeout=20,
                                check=False,
                                env={**os.environ, "OMP_THREAD_LIMIT": "1"},
                            )
                        except FileNotFoundError:
                            raise PDFRejected(6) from None
                        if result.returncode != 0:
                            raise PDFRejected(6)
                        ocr_lines = parse_tesseract_tsv(
                            result.stdout.decode("utf-8"), index + 1, image.width, image.height
                        )
                        # Never silently accept a page for which OCR found no readable content.
                        if not ocr_lines:
                            raise PDFRejected(5)
                        lines.extend(ocr_lines)
                if not native and not has_image and not lines:
                    raise PDFRejected(5)
                if len(lines) > MAX_LINES or sum(len(line.text) for line in lines) > MAX_CHARACTERS:
                    raise PDFRejected(4)
        if not lines:
            raise PDFRejected(5)
        return PDFTextResult(lines=lines).model_dump_json().encode()


def main() -> None:
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (1536 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (45, 45))
        resource.setrlimit(resource.RLIMIT_FSIZE, (20 * 1024 * 1024,) * 2)
    try:
        content = sys.stdin.buffer.read(10 * 1024 * 1024 + 1)
        if len(content) > 10 * 1024 * 1024:
            raise PDFRejected(4)
        output = extract(content, int(sys.argv[2]) if sys.argv[1] == "render" else None)
        sys.stdout.buffer.write(output)
    except PDFRejected as error:
        sys.exit(error.code)
    except Exception:
        # Native errors can contain source content or filenames. Never print them.
        sys.exit(2)


if __name__ == "__main__":
    main()
