"""Synthetic recovery journeys against the actual local Compose services and OIDC roles."""

from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path

import httpx
import pytest
from reportlab.pdfgen.canvas import Canvas

from medbot.audit import verify_audit_chain
from medbot.models import AuditRecord
from medbot.schemas import AuditEventResponse, FHIRBundle, ReferralDetail
from scripts.evaluate_documents import local_url, token_for

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def checked_detail(response: httpx.Response) -> ReferralDetail:
    assert response.is_success, f"Referral operation failed with HTTP {response.status_code}"
    return ReferralDetail.model_validate(response.json())


def create_incomplete(client: httpx.Client) -> ReferralDetail:
    detail = checked_detail(client.post("referrals", json={"specialty": "orthopedics"}))
    content = (ROOT / "synthetic/fixtures/incomplete-referral.pdf").read_bytes()
    upload(client, detail.id, content, "referral_order")
    detail = checked_detail(client.post(f"referrals/{detail.id}/process"))
    assert detail.status == "NEEDS_REVIEW"
    detail = approve_supported_fields(client, detail)
    assert detail.validation.missing_fields == ["patient.date_of_birth"]
    return detail


def approve_supported_fields(client: httpx.Client, detail: ReferralDetail) -> ReferralDetail:
    for field in detail.fields:
        if field.evidence_status in {"LOW_CONFIDENCE", "REVIEW_REQUIRED"}:
            detail = checked_detail(
                client.post(
                    f"referrals/{detail.id}/fields/{field.id}/approve",
                    json={"reason": "Compared with the synthetic source page"},
                )
            )
    return detail


def upload(client: httpx.Client, referral_id: str, content: bytes, classification: str) -> None:
    response = client.post(
        f"referrals/{referral_id}/documents",
        data={"classification": classification},
        files={"file": ("synthetic-integration.pdf", content, "application/pdf")},
    )
    assert response.status_code == 201, "Synthetic PDF upload failed"


def dob_pdf(value: str) -> bytes:
    data = io.BytesIO()
    canvas = Canvas(data, pageCompression=0, invariant=1)
    canvas.drawString(40, 760, "SYNTHETIC TEST DATA - NOT A REAL PATIENT RECORD")
    canvas.drawString(40, 730, f"DOB: {value}")
    canvas.save()
    return data.getvalue()


def assert_audit(client: httpx.Client, referral_id: str) -> list[AuditEventResponse]:
    response = client.get(f"referrals/{referral_id}/audit")
    assert response.status_code == 200
    events = [AuditEventResponse.model_validate(event) for event in response.json()]
    records = [
        AuditRecord(
            id=event.id,
            sequence=event.sequence,
            timestamp=event.timestamp,
            organization_id=event.organization_id,
            referral_id=event.referral_id,
            actor_id=event.actor_id,
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            trace_id=event.trace_id,
            metadata_json=json.dumps(event.metadata, sort_keys=True, separators=(",", ":")),
            previous_event_hash=event.previous_event_hash,
            event_hash=event.event_hash,
        )
        for event in events
    ]
    assert verify_audit_chain(records), "Persisted audit hash chain failed verification"
    return events


def assert_fhir_persisted(client: httpx.Client, detail: ReferralDetail, dob: str) -> None:
    preview = client.get(f"referrals/{detail.id}/fhir-preview")
    assert preview.status_code == 200
    bundle = FHIRBundle.model_validate(preview.json())
    fhir_url = local_url("HAPI_FHIR_BASE_URL", "http://localhost:8082/fhir")
    with httpx.Client(base_url=f"{fhir_url}/", timeout=20) as fhir:
        for entry in bundle.entry:
            response = fhir.get(entry.request.url)
            assert response.status_code == 200, "FHIR resource was not persisted"
            resource = response.json()
            assert resource["id"] == entry.resource.id
            assert resource["resourceType"] == entry.resource.resourceType
            if resource["resourceType"] == "Patient":
                assert resource["birthDate"] == dob
            if resource["resourceType"] == "DocumentReference":
                document_id = resource["identifier"][0]["value"]
                source = client.get(f"referrals/{detail.id}/documents/{document_id}/content")
                assert source.status_code == 200
                attachment = resource["content"][0]["attachment"]
                assert attachment["size"] == len(source.content)
                # R4 Attachment.hash is SHA-1; the internal storage integrity hash stays SHA-256.
                assert (
                    base64.b64decode(attachment["hash"])
                    == hashlib.sha1(source.content, usedforsecurity=False).digest()
                )


def route_and_verify(client: httpx.Client, detail: ReferralDetail, dob: str) -> None:
    first = client.post(f"referrals/{detail.id}/route")
    assert first.status_code == 200, "FHIR-backed routing failed"
    retried = client.post(f"referrals/{detail.id}/route")
    assert retried.status_code == 200
    assert first.json() == retried.json()
    assert first.json()["status"] == "ROUTED"
    assert_fhir_persisted(client, detail, dob)
    events = assert_audit(client, detail.id)
    assert sum(event.action == "REFERRAL_ROUTED" for event in events) == 1


def test_compose_manual_recovery_roles_tenants_and_retry(client: httpx.Client) -> None:
    detail = create_incomplete(client)
    url = f"referrals/{detail.id}"
    body = {
        "field_path": "patient.date_of_birth",
        "value": "1984-02-03",
        "reason": "Confirmed with synthetic referrer",
    }
    for username, expected in (("synthetic-intake-alpha", 403), ("synthetic-reviewer-beta", 404)):
        headers = {"Authorization": f"Bearer {token_for(username)}"}
        assert client.post(f"{url}/request-information", headers=headers).status_code == expected
        assert client.post(f"{url}/fields", json=body, headers=headers).status_code == expected
    for _ in range(2):
        assert (
            checked_detail(client.post(f"{url}/request-information")).status == "NEEDS_INFORMATION"
        )
    for _ in range(2):
        detail = checked_detail(client.post(f"{url}/fields", json=body))
        assert detail.status == "READY_TO_ROUTE"
    field = next(field for field in detail.fields if field.field_name == "patient.date_of_birth")
    assert field.ai_value is None
    assert field.reviewed_value == body["value"]
    assert field.review_reason == body["reason"]
    assert field.reviewer_id and field.reviewed_at
    events = assert_audit(client, detail.id)
    assert sum(event.action == "INFORMATION_REQUESTED" for event in events) == 1
    assert sum(event.action == "FIELD_SUPPLIED" for event in events) == 1
    route_and_verify(client, detail, body["value"])


def test_compose_supplemental_recovery_and_object_integrity(client: httpx.Client) -> None:
    detail = create_incomplete(client)
    checked_detail(client.post(f"referrals/{detail.id}/request-information"))
    content = dob_pdf("1984-02-03")
    for _ in range(2):
        upload(client, detail.id, content, "supplemental")
    detail = checked_detail(client.post(f"referrals/{detail.id}/process"))
    detail = approve_supported_fields(client, detail)
    assert detail.status == "READY_TO_ROUTE"
    assert len(detail.documents) == 2
    document = next(
        document for document in detail.documents if document.classification == "supplemental"
    )
    stored = client.get(f"referrals/{detail.id}/documents/{document.id}/content")
    assert stored.status_code == 200
    assert stored.content == content
    route_and_verify(client, detail, "1984-02-03")


def test_compose_conflicting_sources_require_explained_review(client: httpx.Client) -> None:
    detail = create_incomplete(client)
    upload(client, detail.id, dob_pdf("1984-02-03"), "supplemental")
    upload(client, detail.id, dob_pdf("1992-04-05"), "supplemental")
    detail = checked_detail(client.post(f"referrals/{detail.id}/process"))
    assert detail.status == "NEEDS_REVIEW"
    field = next(field for field in detail.fields if field.field_name == "patient.date_of_birth")
    assert field.evidence_status == "CONFLICTING"
    assert len(field.evidence) == 2
    url = f"referrals/{detail.id}"
    assert client.post(f"{url}/route").status_code == 409
    assert client.post(f"{url}/fields/{field.id}/approve", json={}).status_code == 409
    assert (
        client.post(f"{url}/fields/{field.id}/correct", json={"value": "1984-02-03"}).status_code
        == 422
    )
    detail = checked_detail(
        client.post(
            f"{url}/fields/{field.id}/correct",
            json={
                "value": "1984-02-03",
                "reason": "Confirmed synthetic source discrepancy",
            },
        )
    )
    assert detail.status == "READY_TO_ROUTE"
    route_and_verify(client, detail, "1984-02-03")
