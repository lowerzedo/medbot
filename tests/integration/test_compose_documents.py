"""Actual PDFium/Tesseract, OIDC, storage, and FHIR boundaries on the Compose stack."""

from pathlib import Path

import httpx
import pytest

from tests.integration.test_compose_recovery import (
    approve_supported_fields,
    checked_detail,
    route_and_verify,
    token_for,
    upload,
)

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def test_compose_scan_evidence_is_located_and_needs_review(client: httpx.Client) -> None:
    configuration = client.get("processing-configuration")
    assert configuration.status_code == 200
    assert set(configuration.json()) == {"document_extractor", "ai_provider", "ai_model", "hosted"}
    assert configuration.json()["document_extractor"] == "pdfium-tesseract"
    detail = checked_detail(client.post("referrals", json={"specialty": "cardiology"}))
    upload(
        client,
        detail.id,
        (ROOT / "synthetic/documents/clean-scan.pdf").read_bytes(),
        "referral_order",
    )
    detail = checked_detail(client.post(f"referrals/{detail.id}/process"))
    assert detail.status == "NEEDS_REVIEW"
    assert len(detail.fields) == 4
    assert all(field.evidence_status == "REVIEW_REQUIRED" for field in detail.fields)
    for field in detail.fields:
        assert field.evidence
        assert all(
            ref.extraction_method == "ocr" and ref.region and ref.ocr_confidence is not None
            for ref in field.evidence
        )
    assert client.post(f"referrals/{detail.id}/route").status_code == 409
    image_url = f"referrals/{detail.id}/documents/{detail.documents[0].id}/pages/1/image"
    page = client.get(image_url)
    assert page.status_code == 200 and page.content.startswith(b"\x89PNG")
    assert (
        client.get(
            image_url, headers={"Authorization": f"Bearer {token_for('synthetic-reviewer-beta')}"}
        ).status_code
        == 404
    )
    detail = approve_supported_fields(client, detail)
    route_and_verify(client, detail, "1970-01-02")


@pytest.mark.parametrize(
    "name,code", [("encrypted", "encrypted_pdf"), ("unreadable-scan", "document_text_unavailable")]
)
def test_compose_unreadable_documents_fail_explicitly(
    client: httpx.Client, name: str, code: str
) -> None:
    detail = checked_detail(client.post("referrals", json={"specialty": "cardiology"}))
    upload(
        client, detail.id, (ROOT / f"synthetic/documents/{name}.pdf").read_bytes(), "referral_order"
    )
    processed = client.post(f"referrals/{detail.id}/process")
    assert processed.status_code == 422
    assert processed.json()["error"]["code"] == code
    failed = checked_detail(client.get(f"referrals/{detail.id}"))
    assert failed.status == "FAILED"
    assert failed.documents[0].processing_state == "FAILED"
    assert failed.fields == []
    assert client.post(f"referrals/{detail.id}/route").status_code == 409
