from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from pydantic import ValidationError

from medbot.ai import FixtureAIProvider
from medbot.document_processing import (
    ExtractedTextBlock,
    FixtureDocumentExtractor,
    PDFDocumentExtractor,
    SourceRegion,
    render_pdf_page,
)
from medbot.errors import ApplicationError
from medbot.evidence import EvidenceVerificationError, verify_field_evidence
from medbot.pdf_worker import parse_tesseract_tsv
from medbot.schemas import AIExtractedField, AIExtractionEvidence
from tests.conftest import (
    AUTH_A,
    AUTH_B,
    AppHarness,
    create_referral,
    synthetic_pdf,
    upload_referral_pdf,
)

ROOT = Path(__file__).resolve().parents[1]


def test_compressed_multipage_pdf_preserves_source_and_regions() -> None:
    content = (ROOT / "synthetic/documents/multipage-text.pdf").read_bytes()
    blocks = PDFDocumentExtractor().extract("synthetic-document", content)
    dob = next(block for block in blocks if block.text == "DOB: 1970-01-02")
    assert dob.page_number == 2
    assert dob.extraction_method == "pdf_text"
    assert dob.ocr_confidence is None
    assert dob.region and 0 < dob.region.x < 1 and 0 < dob.region.y < 1
    assert dob.text_sha256 == hashlib.sha256(dob.text.encode()).hexdigest()
    assert render_pdf_page(content, 2).startswith(b"\x89PNG")


@pytest.mark.parametrize(
    "name,code", [("encrypted", "encrypted_pdf"), ("compressed-text", "page_not_found")]
)
def test_pdf_worker_errors_are_explicit(name: str, code: str) -> None:
    content = (ROOT / f"synthetic/documents/{name}.pdf").read_bytes()
    with pytest.raises(ApplicationError) as error:
        render_pdf_page(content, 99 if name == "compressed-text" else 1)
    assert error.value.code == code


def test_tsv_joins_words_in_line_with_conservative_confidence_and_geometry() -> None:
    header = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
        "left\ttop\twidth\theight\tconf\ttext\n"
    )
    rows = (
        "5\t1\t1\t1\t1\t1\t10\t20\t30\t10\t98\tDOB:\n"
        "5\t1\t1\t1\t1\t2\t50\t20\t40\t10\t71\t1970-01-02\n"
    )
    lines = parse_tesseract_tsv(header + rows, 3, 100, 100)
    assert len(lines) == 1
    assert lines[0].text == "DOB: 1970-01-02"
    assert lines[0].page == 3
    assert lines[0].confidence == 0.71
    assert lines[0].region == SourceRegion(x=0.1, y=0.2, width=0.8, height=0.1)


def test_page_region_must_stay_within_source_page() -> None:
    with pytest.raises(ValidationError):
        SourceRegion(x=0.95, y=0.5, width=0.2, height=0.1)


def test_narrative_evidence_is_review_only_and_cannot_relabel_another_field() -> None:
    text = "The referring provider is SYNTHETIC CLINICIAN."
    block = ExtractedTextBlock(
        "block", "doc", 1, 0, text, hashlib.sha256(text.encode()).hexdigest(), 0, len(text)
    )
    reference = AIExtractionEvidence(
        document_id="doc",
        page_number=1,
        block_id="block",
        text_sha256=block.text_sha256,
        quote=text,
    )
    field = AIExtractedField(
        path="referring_provider.name",
        value="SYNTHETIC CLINICIAN",
        confidence=1,
        evidence=(reference,),
    )
    with pytest.raises(EvidenceVerificationError):
        verify_field_evidence(field, (block,), frozenset({"doc"}))
    verified = verify_field_evidence(field, (block,), frozenset({"doc"}), allow_unlabeled=True)
    assert verified[0].field_grounded is False
    wrong = field.model_copy(update={"value": "CLINIC"})
    with pytest.raises(EvidenceVerificationError):
        verify_field_evidence(wrong, (block,), frozenset({"doc"}), allow_unlabeled=True)


def test_malformed_pdf_errors_do_not_include_source_contents() -> None:
    content = b"%PDF-1.4\nSynthetic-private-document-value\n%%EOF\n"
    with pytest.raises(ApplicationError) as error:
        PDFDocumentExtractor().extract("synthetic", content)
    assert error.value.code == "invalid_pdf"
    assert "Synthetic-private-document-value" not in str(error.value)


class HighConfidenceOCR:
    def extract(self, document_id: str, content: bytes) -> tuple[ExtractedTextBlock, ...]:
        return tuple(
            replace(
                block,
                extraction_method="ocr",
                ocr_confidence=0.99,
                region=SourceRegion(x=0.1, y=0.1, width=0.5, height=0.02),
            )
            for block in FixtureDocumentExtractor().extract(document_id, content)
        )


class ReviewRequiredModel:
    def extract(self, blocks: tuple[ExtractedTextBlock, ...]) -> object:
        return FixtureAIProvider().extract(blocks).model_copy(update={"requires_review": True})


class LatestEvidenceModel(ReviewRequiredModel):
    def extract(self, blocks: tuple[ExtractedTextBlock, ...]) -> object:
        return super().extract(tuple(reversed(blocks)))


def test_same_value_from_new_evidence_requires_a_new_review(
    app_factory: Callable[..., AppHarness],
) -> None:
    harness = app_factory(ai_provider=LatestEvidenceModel())
    referral_id = create_referral(harness.client)
    url = f"/api/v1/referrals/{referral_id}"
    upload_referral_pdf(harness.client, referral_id, synthetic_pdf())
    initial = harness.client.post(f"{url}/process", headers=AUTH_A).json()
    for field in initial["fields"][:1]:
        response = harness.client.post(
            f"{url}/fields/{field['id']}/approve", headers=AUTH_A, json={}
        )
        assert response.status_code == 200
    assert response.json()["status"] == "NEEDS_REVIEW"
    upload_referral_pdf(harness.client, referral_id, synthetic_pdf(prompt_injection=True))
    processed = harness.client.post(f"{url}/process", headers=AUTH_A).json()
    assert processed["status"] == "NEEDS_REVIEW"
    assert all(field["evidence_status"] == "REVIEW_REQUIRED" for field in processed["fields"])
    assert all(field["review_status"] == "PENDING" for field in processed["fields"])
    assert all(field["reviewed_at"] is None for field in processed["fields"])
    for field in processed["fields"]:
        assert (
            harness.client.post(
                f"{url}/fields/{field['id']}/approve", headers=AUTH_A, json={}
            ).status_code
            == 200
        )


@pytest.mark.parametrize("ocr", [False, True])
def test_model_and_ocr_proposals_need_review_and_same_source_review_survives_retry(
    app_factory: Callable[..., AppHarness],
    ocr: bool,
) -> None:
    harness = app_factory(
        ai_provider=None if ocr else ReviewRequiredModel(),
        extractor=HighConfidenceOCR() if ocr else None,
    )
    referral_id = create_referral(harness.client)
    upload_referral_pdf(harness.client, referral_id, synthetic_pdf())
    processed = harness.client.post(f"/api/v1/referrals/{referral_id}/process", headers=AUTH_A)
    assert processed.status_code == 200
    data = processed.json()
    assert data["status"] == "NEEDS_REVIEW"
    assert all(field["evidence_status"] == "REVIEW_REQUIRED" for field in data["fields"])
    assert data["validation"]["missing_fields"] == []
    assert sorted(data["validation"]["pending_review_fields"]) == sorted(
        field["field_name"] for field in data["fields"]
    )
    assert (
        harness.client.post(
            f"/api/v1/referrals/{referral_id}/request-information", headers=AUTH_A
        ).status_code
        == 409
    )
    assert (
        harness.client.post(f"/api/v1/referrals/{referral_id}/route", headers=AUTH_A).status_code
        == 409
    )
    field = data["fields"][0]
    approved = harness.client.post(
        f"/api/v1/referrals/{referral_id}/fields/{field['id']}/approve",
        headers=AUTH_A,
        json={"reason": "Compared synthetic source region"},
    )
    reviewed = next(item for item in approved.json()["fields"] if item["id"] == field["id"])
    retried = harness.client.post(f"/api/v1/referrals/{referral_id}/process", headers=AUTH_A)
    retained = next(item for item in retried.json()["fields"] if item["id"] == field["id"])
    assert retained["evidence_status"] == "VERIFIED"
    assert retained["review_status"] == "APPROVED"
    assert retained["reviewed_at"] == reviewed["reviewed_at"]
    if ocr:
        assert retained["evidence"][0]["extraction_method"] == "ocr"
        assert retained["evidence"][0]["ocr_confidence"] == 0.99
        assert retained["evidence"][0]["region"] is not None


def test_source_image_is_authenticated_scoped_and_matches_original_document(
    harness: AppHarness,
) -> None:
    referral_id = create_referral(harness.client)
    content = (ROOT / "synthetic/documents/compressed-text.pdf").read_bytes()
    uploaded = upload_referral_pdf(harness.client, referral_id, content)
    document_id = uploaded["document"]["id"]
    url = f"/api/v1/referrals/{referral_id}/documents/{document_id}/pages/1/image"
    assert harness.client.get(url).status_code == 401
    assert harness.client.get(url, headers=AUTH_B).status_code == 404
    response = harness.client.get(url, headers=AUTH_A)
    assert response.status_code == 200
    assert response.content.startswith(b"\x89PNG")
    assert response.headers["cache-control"] == "private, no-store"


def test_worker_timeout_removes_temporary_images_and_does_not_inherit_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from medbot.document_processing import run_pdf_worker

    monkeypatch.setenv("MEDBOT_OPENAI_API_KEY", "synthetic-secret-not-for-parser")
    process = Mock(pid=999999)
    process.communicate.side_effect = [
        subprocess.TimeoutExpired("synthetic-worker", 60),
        (b"", None),
    ]
    with (
        patch("medbot.document_processing.subprocess.Popen", return_value=process) as factory,
        patch("medbot.document_processing.os.killpg") as cancel,
        pytest.raises(ApplicationError) as error,
    ):
        run_pdf_worker(b"synthetic content", "extract")
    assert error.value.status_code == 503
    environment = factory.call_args.kwargs["env"]
    assert "MEDBOT_OPENAI_API_KEY" not in environment
    assert not Path(environment["TMPDIR"]).exists()
    cancel.assert_called_once()
