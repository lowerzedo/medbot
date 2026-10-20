from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from medbot.audit import verify_audit_chain
from medbot.models import AuditRecord
from tests.conftest import (
    AUTH_A,
    AUTH_B,
    AUTH_INTAKE,
    AppHarness,
    create_referral,
    synthetic_pdf,
    upload_referral_pdf,
)


def _complete_processed_referral(client: TestClient) -> str:
    referral_id = create_referral(client)
    upload_referral_pdf(client, referral_id, synthetic_pdf())
    response = client.post(f"/api/v1/referrals/{referral_id}/process", headers=AUTH_A)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "READY_TO_ROUTE"
    return referral_id


def test_complete_workflow_has_evidence_fhir_routing_and_audit(
    harness: AppHarness,
) -> None:
    referral_id = _complete_processed_referral(harness.client)
    detail = harness.client.get(f"/api/v1/referrals/{referral_id}", headers=AUTH_A).json()
    assert detail["validation"] == {
        "complete": True,
        "missing_fields": [],
        "pending_review_fields": [],
        "missing_documents": [],
        "warnings": [],
    }
    assert {field["field_name"] for field in detail["fields"]} >= {
        "patient.full_name",
        "patient.date_of_birth",
        "referring_provider.name",
        "requested_service",
    }
    assert all(evidence["verified"] for field in detail["fields"] for evidence in field["evidence"])

    preview = harness.client.get(f"/api/v1/referrals/{referral_id}/fhir-preview", headers=AUTH_A)
    assert preview.status_code == 200
    resource_types = {entry["resource"]["resourceType"] for entry in preview.json()["entry"]}
    assert resource_types == {
        "Patient",
        "Practitioner",
        "Organization",
        "ServiceRequest",
        "DocumentReference",
        "Task",
        "Provenance",
    }

    routed = harness.client.post(f"/api/v1/referrals/{referral_id}/route", headers=AUTH_A)
    assert routed.status_code == 200, routed.text
    assert routed.json()["status"] == "ROUTED"
    assert routed.json()["queue"] == "cardiology_intake"
    repeated_route = harness.client.post(f"/api/v1/referrals/{referral_id}/route", headers=AUTH_A)
    assert repeated_route.status_code == 200
    assert repeated_route.json() == routed.json()

    audit = harness.client.get(f"/api/v1/referrals/{referral_id}/audit", headers=AUTH_A)
    assert audit.status_code == 200
    actions = {event["action"] for event in audit.json()}
    assert {"REFERRAL_CREATED", "DOCUMENT_UPLOADED", "FHIR_WRITTEN", "REFERRAL_ROUTED"} <= actions
    assert [event["action"] for event in audit.json()].count("REFERRAL_ROUTED") == 1


def test_incomplete_referral_requires_review(harness: AppHarness) -> None:
    referral_id = create_referral(harness.client)
    upload_referral_pdf(harness.client, referral_id, synthetic_pdf(include_dob=False))
    response = harness.client.post(f"/api/v1/referrals/{referral_id}/process", headers=AUTH_A)
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["status"] == "NEEDS_REVIEW"
    assert detail["complete"] is False
    assert detail["validation"]["missing_fields"] == ["patient.date_of_birth"]


def test_prompt_injection_is_inert_and_routing_remains_deterministic(
    harness: AppHarness,
) -> None:
    referral_id = create_referral(harness.client)
    upload_referral_pdf(harness.client, referral_id, synthetic_pdf(prompt_injection=True))
    processed = harness.client.post(f"/api/v1/referrals/{referral_id}/process", headers=AUTH_A)
    assert processed.status_code == 200
    assert processed.json()["status"] == "READY_TO_ROUTE"
    assert processed.json()["routing_queue"] is None
    assert all(field["ai_value"] != "attacker_queue" for field in processed.json()["fields"])
    routed = harness.client.post(f"/api/v1/referrals/{referral_id}/route", headers=AUTH_A)
    assert routed.json()["queue"] == "cardiology_intake"


def test_cross_organization_access_is_hidden_and_headers_cannot_override_scope(
    harness: AppHarness,
) -> None:
    referral_id = create_referral(harness.client)
    denied = harness.client.get(f"/api/v1/referrals/{referral_id}", headers=AUTH_B)
    assert denied.status_code == 404
    scoped = harness.client.get(
        f"/api/v1/referrals/{referral_id}",
        headers={**AUTH_A, "X-Organization-ID": "org-b"},
    )
    assert scoped.status_code == 200


def test_invalid_transition_and_role_are_rejected(harness: AppHarness) -> None:
    referral_id = create_referral(harness.client)
    invalid = harness.client.post(f"/api/v1/referrals/{referral_id}/route", headers=AUTH_A)
    assert invalid.status_code == 409
    assert invalid.json()["error"]["code"] == "invalid_state_transition"
    forbidden = harness.client.post(f"/api/v1/referrals/{referral_id}/route", headers=AUTH_INTAKE)
    assert forbidden.status_code == 403


def test_duplicate_upload_is_idempotent(harness: AppHarness) -> None:
    referral_id = create_referral(harness.client)
    content = synthetic_pdf()
    first = upload_referral_pdf(harness.client, referral_id, content)
    second = upload_referral_pdf(harness.client, referral_id, content)
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert first["document"] == second["document"]
    detail = harness.client.get(f"/api/v1/referrals/{referral_id}", headers=AUTH_A).json()
    assert len(detail["documents"]) == 1


def test_upload_validation_rejects_unsafe_inputs(harness: AppHarness) -> None:
    referral_id = create_referral(harness.client)
    wrong_type = harness.client.post(
        f"/api/v1/referrals/{referral_id}/documents",
        headers=AUTH_A,
        files={"file": ("referral.pdf", synthetic_pdf(), "text/plain")},
    )
    assert wrong_type.status_code == 415
    bad_name = harness.client.post(
        f"/api/v1/referrals/{referral_id}/documents",
        headers=AUTH_A,
        files={"file": ("../referral.pdf", synthetic_pdf(), "application/pdf")},
    )
    assert bad_name.status_code == 422
    malformed = harness.client.post(
        f"/api/v1/referrals/{referral_id}/documents",
        headers=AUTH_A,
        files={"file": ("referral.pdf", b"not a pdf", "application/pdf")},
    )
    assert malformed.status_code == 422
    oversized = harness.client.post(
        f"/api/v1/referrals/{referral_id}/documents",
        headers=AUTH_A,
        files={
            "file": (
                "referral.pdf",
                b"%PDF-1.4\n" + (b"x" * 4096) + b"%%EOF",
                "application/pdf",
            )
        },
    )
    assert oversized.status_code == 413


def test_field_correction_preserves_ai_value(harness: AppHarness) -> None:
    referral_id = _complete_processed_referral(harness.client)
    detail = harness.client.get(f"/api/v1/referrals/{referral_id}", headers=AUTH_A).json()
    requested = next(
        field for field in detail["fields"] if field["field_name"] == "requested_service"
    )
    corrected = harness.client.post(
        f"/api/v1/referrals/{referral_id}/fields/{requested['id']}/correct",
        headers=AUTH_A,
        json={"value": "Synthetic reviewed consultation", "reason": "Synthetic correction"},
    )
    assert corrected.status_code == 200, corrected.text
    field = next(item for item in corrected.json()["fields"] if item["id"] == requested["id"])
    assert field["ai_value"] == "Synthetic cardiac consultation"
    assert field["reviewed_value"] == "Synthetic reviewed consultation"
    assert field["evidence_status"] == "HUMAN_CORRECTED"


def test_field_correction_reason_is_optional(harness: AppHarness) -> None:
    referral_id = _complete_processed_referral(harness.client)
    detail = harness.client.get(f"/api/v1/referrals/{referral_id}", headers=AUTH_A).json()
    requested = next(
        field for field in detail["fields"] if field["field_name"] == "requested_service"
    )
    corrected = harness.client.post(
        f"/api/v1/referrals/{referral_id}/fields/{requested['id']}/correct",
        headers=AUTH_A,
        json={"value": "Synthetic reviewed service"},
    )
    assert corrected.status_code == 200, corrected.text
    changed = next(item for item in corrected.json()["fields"] if item["id"] == requested["id"])
    assert changed["review_reason"] is None


def test_invalid_dob_requires_valid_correction_and_audits_readiness(
    harness: AppHarness,
) -> None:
    referral_id = create_referral(harness.client)
    content = synthetic_pdf().replace(b"DOB: 1970-01-02", b"DOB: not-a-date")
    upload_referral_pdf(harness.client, referral_id, content)
    processed = harness.client.post(f"/api/v1/referrals/{referral_id}/process", headers=AUTH_A)
    assert processed.status_code == 200
    detail = processed.json()
    assert detail["status"] == "NEEDS_REVIEW"
    assert detail["validation"]["missing_fields"] == ["patient.date_of_birth"]
    dob_field = next(
        field for field in detail["fields"] if field["field_name"] == "patient.date_of_birth"
    )

    preview = harness.client.get(
        f"/api/v1/referrals/{referral_id}/fhir-preview", headers=AUTH_A
    ).json()
    patient = next(
        entry["resource"]
        for entry in preview["entry"]
        if entry["resource"]["resourceType"] == "Patient"
    )
    assert "birthDate" not in patient

    corrected = harness.client.post(
        f"/api/v1/referrals/{referral_id}/fields/{dob_field['id']}/correct",
        headers=AUTH_A,
        json={"value": "1970-01-02", "reason": "Synthetic format correction"},
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["status"] == "READY_TO_ROUTE"
    assert corrected.json()["validation"]["missing_fields"] == []

    audit = harness.client.get(f"/api/v1/referrals/{referral_id}/audit", headers=AUTH_A)
    status_events = [
        event
        for event in audit.json()
        if event["action"] == "STATUS_CHANGED"
        and event["metadata"].get("status") == "READY_TO_ROUTE"
    ]
    assert status_events


def test_audit_chain_verifies_and_detects_tampering(harness: AppHarness) -> None:
    referral_id = _complete_processed_referral(harness.client)
    with Session(harness.engine) as session:
        records = list(
            session.scalars(
                select(AuditRecord)
                .where(AuditRecord.referral_id == referral_id)
                .order_by(AuditRecord.sequence)
            )
        )
        assert verify_audit_chain(records)
        original = records[1].action
        records[1].action = "TAMPERED"
        assert not verify_audit_chain(records)
        records[1].action = original


def test_sensitive_values_are_absent_from_logs(
    harness: AppHarness, caplog: pytest.LogCaptureFixture
) -> None:
    sensitive_name = "Synthetic-Sensitive-Name-7429"
    content = synthetic_pdf().replace(b"Synthetic Example", sensitive_name.encode("ascii"))
    with caplog.at_level(logging.INFO):
        referral_id = create_referral(harness.client)
        upload_referral_pdf(harness.client, referral_id, content)
        response = harness.client.post(f"/api/v1/referrals/{referral_id}/process", headers=AUTH_A)
    assert response.status_code == 200
    assert sensitive_name not in caplog.text


def test_health_auth_and_metrics(harness: AppHarness) -> None:
    assert harness.client.get("/health/live").json() == {"status": "ok"}
    assert harness.client.get("/health/ready").json() == {"status": "ready"}
    unauthenticated = harness.client.get("/api/v1/auth/me")
    assert unauthenticated.status_code == 401
    me = harness.client.get("/api/v1/auth/me", headers=AUTH_A)
    assert me.status_code == 200
    assert me.json()["organization_id"] == "org-a"
    metrics = harness.client.get("/metrics")
    assert metrics.status_code == 200
    assert "referrals_received_total" in metrics.text


def test_routing_same_policy_is_reproducible(harness: AppHarness) -> None:
    results = []
    for _ in range(2):
        referral_id = _complete_processed_referral(harness.client)
        response = harness.client.post(f"/api/v1/referrals/{referral_id}/route", headers=AUTH_A)
        results.append((response.json()["queue"], response.json()["rule_version"]))
    assert results[0] == results[1] == ("cardiology_intake", "2026-08-01")
