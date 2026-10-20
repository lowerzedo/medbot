from __future__ import annotations

from collections.abc import Callable

import pytest
from sqlalchemy.orm import Session

from medbot.ai import AIProvider, FixtureAIProvider
from medbot.audit import verify_audit_chain
from medbot.document_processing import ExtractedTextBlock
from medbot.models import AuditRecord
from medbot.schemas import AIExtractedField, AIExtractionEvidence, AIExtractionOutput
from tests.conftest import (
    AUTH_A,
    AUTH_B,
    AUTH_INTAKE,
    AppHarness,
    create_referral,
    synthetic_pdf,
    upload_referral_pdf,
)


def incomplete(harness: AppHarness) -> tuple[str, str]:
    referral_id = create_referral(harness.client)
    upload_referral_pdf(harness.client, referral_id, synthetic_pdf(include_dob=False))
    url = f"/api/v1/referrals/{referral_id}"
    assert harness.client.post(f"{url}/process", headers=AUTH_A).json()["status"] == "NEEDS_REVIEW"
    return referral_id, url


def test_missing_field_recovery_is_audited_idempotent_and_routable(harness: AppHarness) -> None:
    referral_id, url = incomplete(harness)
    for _ in range(2):
        response = harness.client.post(f"{url}/request-information", headers=AUTH_A)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "NEEDS_INFORMATION"
    body = {
        "field_path": "patient.date_of_birth",
        "value": "1970-01-02",
        "reason": "Confirmed with synthetic referrer",
    }
    for _ in range(2):
        response = harness.client.post(f"{url}/fields", json=body, headers=AUTH_A)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "READY_TO_ROUTE"
    dob = next(
        field
        for field in response.json()["fields"]
        if field["field_name"] == "patient.date_of_birth"
    )
    assert dob["ai_value"] is None
    assert dob["reviewed_value"] == "1970-01-02"
    assert dob["reviewer_id"] == "reviewer-a"
    assert dob["review_reason"] == body["reason"]
    assert dob["reviewed_at"] is not None
    assert dob["evidence"] == []
    assert response.json()["validation"]["missing_fields"] == []
    assert harness.client.post(f"{url}/route", headers=AUTH_A).status_code == 200
    events = harness.client.get(f"{url}/audit", headers=AUTH_A).json()
    assert [event["action"] for event in events].count("FIELD_SUPPLIED") == 1
    assert [event["action"] for event in events].count("INFORMATION_REQUESTED") == 1
    assert body["value"] not in str([event["metadata"] for event in events])
    with Session(harness.engine) as session:
        assert verify_audit_chain(
            list(session.query(AuditRecord).filter_by(referral_id=referral_id))
        )


@pytest.mark.parametrize(
    "action,body",
    [
        ("request-information", None),
        (
            "fields",
            {
                "field_path": "patient.date_of_birth",
                "value": "1970-01-02",
                "reason": "Synthetic confirmation",
            },
        ),
    ],
)
def test_recovery_enforces_roles_and_tenant_scope(
    harness: AppHarness, action: str, body: object
) -> None:
    _, url = incomplete(harness)
    assert harness.client.post(f"{url}/{action}", json=body, headers=AUTH_INTAKE).status_code == 403
    assert harness.client.post(f"{url}/{action}", json=body, headers=AUTH_B).status_code == 404
    assert harness.client.post(f"{url}/{action}", json=body).status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {
            "field_path": "patient.date_of_birth",
            "value": "not-a-date",
            "reason": "Synthetic confirmation",
        },
        {"field_path": "patient.date_of_birth", "value": "1970-01-02"},
        {"field_path": "patient.date_of_birth", "value": "1970-01-02", "reason": "  "},
        {"field_path": "workflow.status", "value": "COMPLETED", "reason": "Synthetic confirmation"},
    ],
)
def test_invalid_supply_leaves_referral_unchanged(harness: AppHarness, body: object) -> None:
    _, url = incomplete(harness)
    assert harness.client.post(f"{url}/fields", json=body, headers=AUTH_A).status_code == 422
    detail = harness.client.get(url, headers=AUTH_A).json()
    assert detail["status"] == "NEEDS_REVIEW"
    assert detail["validation"]["missing_fields"] == ["patient.date_of_birth"]


def test_existing_field_and_received_state_cannot_be_supplied(harness: AppHarness) -> None:
    _, url = incomplete(harness)
    body = {
        "field_path": "patient.full_name",
        "value": "Synthetic Override",
        "reason": "Synthetic confirmation",
    }
    assert harness.client.post(f"{url}/fields", json=body, headers=AUTH_A).status_code == 409
    received = create_referral(harness.client)
    body["field_path"] = "patient.date_of_birth"
    body["value"] = "1970-01-02"
    assert (
        harness.client.post(
            f"/api/v1/referrals/{received}/fields", json=body, headers=AUTH_A
        ).status_code
        == 409
    )


def test_supplemental_document_recovers_same_referral(harness: AppHarness) -> None:
    referral_id, url = incomplete(harness)
    assert harness.client.post(f"{url}/request-information", headers=AUTH_A).status_code == 200
    upload_referral_pdf(harness.client, referral_id, synthetic_pdf())
    processed = harness.client.post(f"{url}/process", headers=AUTH_A)
    assert processed.status_code == 200, processed.text
    assert processed.json()["status"] == "READY_TO_ROUTE"
    assert len(processed.json()["documents"]) == 2
    assert harness.client.post(f"{url}/route", headers=AUTH_A).status_code == 200


def test_conflicting_demographics_require_explained_correction(harness: AppHarness) -> None:
    referral_id = create_referral(harness.client)
    url = f"/api/v1/referrals/{referral_id}"
    # Leave another required field absent so the packet remains reviewable on retry.
    content = synthetic_pdf().replace(b"Referring Provider:", b"Unknown:")
    upload_referral_pdf(harness.client, referral_id, content)
    upload_referral_pdf(harness.client, referral_id, content.replace(b"1970-01-02", b"1982-03-04"))
    detail = harness.client.post(f"{url}/process", headers=AUTH_A).json()
    assert detail["status"] == "NEEDS_REVIEW"
    field = next(
        field for field in detail["fields"] if field["field_name"] == "patient.date_of_birth"
    )
    assert field["evidence_status"] == "CONFLICTING"
    assert len(field["evidence"]) == 2
    assert harness.client.post(f"{url}/route", headers=AUTH_A).status_code == 409
    assert (
        harness.client.post(
            f"{url}/fields/{field['id']}/approve", json={}, headers=AUTH_A
        ).status_code
        == 409
    )
    assert (
        harness.client.post(
            f"{url}/fields/{field['id']}/correct", json={"value": "1970-01-02"}, headers=AUTH_A
        ).status_code
        == 422
    )
    correction = {"value": "1970-01-02", "reason": "Confirmed synthetic source discrepancy"}
    corrected = harness.client.post(
        f"{url}/fields/{field['id']}/correct", json=correction, headers=AUTH_A
    )
    assert corrected.status_code == 200, corrected.text
    retry = harness.client.post(f"{url}/process", headers=AUTH_A).json()
    assert retry["status"] == "NEEDS_REVIEW"
    assert not any(
        issue["code"] == "conflicting_values" and not issue["resolved"]
        for issue in retry["validation_issues"]
    )
    reviewed = next(
        field for field in retry["fields"] if field["field_name"] == "patient.date_of_birth"
    )
    assert reviewed["evidence_status"] == "HUMAN_CORRECTED"
    assert reviewed["review_reason"] == correction["reason"]
    # New conflicting evidence invalidates the previous resolution, even at 99% confidence.
    upload_referral_pdf(
        harness.client, referral_id, synthetic_pdf().replace(b"1970-01-02", b"1993-05-06")
    )
    detail = harness.client.post(f"{url}/process", headers=AUTH_A).json()
    assert detail["status"] == "NEEDS_REVIEW"
    field = next(
        field for field in detail["fields"] if field["field_name"] == "patient.date_of_birth"
    )
    assert field["evidence_status"] == "CONFLICTING"
    assert len(field["evidence"]) == 3
    corrected = harness.client.post(
        f"{url}/fields/{field['id']}/correct", json=correction, headers=AUTH_A
    )
    assert corrected.json()["status"] == "READY_TO_ROUTE"
    preview = harness.client.get(f"{url}/fhir-preview", headers=AUTH_A).json()
    assert (
        next(
            entry["resource"]
            for entry in preview["entry"]
            if entry["resource"]["resourceType"] == "Patient"
        )["birthDate"]
        == "1970-01-02"
    )


class WrongFieldProvider(AIProvider):
    def extract(self, blocks: tuple[ExtractedTextBlock, ...]) -> object:
        output = AIExtractionOutput.model_validate(FixtureAIProvider().extract(blocks))
        source = next(field for field in output.fields if field.path == "requested_service")
        return output.model_copy(
            update={
                "fields": (
                    *output.fields,
                    AIExtractedField(
                        path="patient.date_of_birth",
                        value=source.value,
                        confidence=0.99,
                        evidence=source.evidence,
                    ),
                )
            }
        )


def test_correct_quote_for_wrong_field_is_rejected(app_factory: Callable[..., AppHarness]) -> None:
    harness = app_factory(ai_provider=WrongFieldProvider())
    referral_id = create_referral(harness.client)
    content = synthetic_pdf(include_dob=False).replace(
        b"Synthetic cardiac consultation", b"1970-01-02"
    )
    upload_referral_pdf(harness.client, referral_id, content)
    detail = harness.client.post(f"/api/v1/referrals/{referral_id}/process", headers=AUTH_A).json()
    assert detail["status"] == "NEEDS_REVIEW"
    assert "patient.date_of_birth" in detail["validation"]["missing_fields"]
    assert not any(field["field_name"] == "patient.date_of_birth" for field in detail["fields"])


@pytest.mark.parametrize(
    "quote", ["1970-01-02", "DOB: 1970", "DOB: 1970-01-02\nRequested Service: synthetic"]
)
def test_partial_or_spliced_quotes_are_rejected(quote: str) -> None:
    import hashlib

    from medbot.evidence import EvidenceVerificationError, verify_field_evidence

    text = "DOB: 1970-01-02\nRequested Service: synthetic"
    block = ExtractedTextBlock(
        "block", "doc", 1, 0, text, hashlib.sha256(text.encode()).hexdigest(), 0, len(text)
    )
    field = AIExtractedField(
        path="patient.date_of_birth",
        value="1970-01-02",
        confidence=0.99,
        evidence=(
            AIExtractionEvidence(
                document_id="doc",
                page_number=1,
                block_id="block",
                text_sha256=block.text_sha256,
                quote=quote,
            ),
        ),
    )
    with pytest.raises(EvidenceVerificationError):
        verify_field_evidence(field, (block,), frozenset({"doc"}))


def test_manual_value_does_not_bypass_unprocessed_supplement(harness: AppHarness) -> None:
    referral_id, url = incomplete(harness)
    harness.client.post(f"{url}/request-information", headers=AUTH_A)
    upload_referral_pdf(harness.client, referral_id, synthetic_pdf())
    supplied = harness.client.post(
        f"{url}/fields",
        headers=AUTH_A,
        json={
            "field_path": "patient.date_of_birth",
            "value": "1970-01-02",
            "reason": "Synthetic confirmation",
        },
    )
    assert supplied.status_code == 200, supplied.text
    assert supplied.json()["status"] == "NEEDS_INFORMATION"
    assert harness.client.post(f"{url}/route", headers=AUTH_A).status_code == 409
    assert (
        harness.client.post(f"{url}/process", headers=AUTH_A).json()["status"] == "READY_TO_ROUTE"
    )


def test_manual_provenance_survives_later_extraction(harness: AppHarness) -> None:
    referral_id = create_referral(harness.client)
    url = f"/api/v1/referrals/{referral_id}"
    content = synthetic_pdf(include_dob=False).replace(b"Referring Provider:", b"Unknown:")
    upload_referral_pdf(harness.client, referral_id, content)
    harness.client.post(f"{url}/process", headers=AUTH_A)
    body = {
        "field_path": "patient.date_of_birth",
        "value": "1970-01-02",
        "reason": "Synthetic phone confirmation",
    }
    supplied = harness.client.post(f"{url}/fields", headers=AUTH_A, json=body)
    assert supplied.json()["status"] == "NEEDS_REVIEW"
    upload_referral_pdf(harness.client, referral_id, synthetic_pdf())
    detail = harness.client.post(f"{url}/process", headers=AUTH_A).json()
    assert detail["status"] == "READY_TO_ROUTE"
    field = next(
        field for field in detail["fields"] if field["field_name"] == "patient.date_of_birth"
    )
    assert field["ai_value"] is None
    assert field["reviewed_value"] == body["value"]
    assert field["review_reason"] == body["reason"]
    assert field["reviewer_id"] == "reviewer-a"


class OmittingProvider(FixtureAIProvider):
    omit = False
    omitted_path = "patient.date_of_birth"

    def extract(self, blocks: tuple[ExtractedTextBlock, ...]) -> object:
        output = AIExtractionOutput.model_validate(super().extract(blocks))
        if self.omit:
            return output.model_copy(
                update={
                    "fields": tuple(
                        field for field in output.fields if field.path != self.omitted_path
                    )
                }
            )
        return output


@pytest.mark.parametrize("omitted_path", ["patient.date_of_birth", "requested_service"])
def test_reprocessing_cannot_reuse_unsupported_stale_fields(
    app_factory: Callable[..., AppHarness],
    omitted_path: str,
) -> None:
    provider = OmittingProvider()
    provider.omitted_path = omitted_path
    harness = app_factory(ai_provider=provider)
    referral_id = create_referral(harness.client)
    url = f"/api/v1/referrals/{referral_id}"
    content = synthetic_pdf().replace(b"Referring Provider:", b"Unknown:")
    upload_referral_pdf(harness.client, referral_id, content)
    assert harness.client.post(f"{url}/process", headers=AUTH_A).json()["status"] == "NEEDS_REVIEW"
    provider.omit = True
    upload_referral_pdf(harness.client, referral_id, synthetic_pdf())
    detail = harness.client.post(f"{url}/process", headers=AUTH_A).json()
    assert detail["status"] == "NEEDS_REVIEW"
    assert detail["validation"]["missing_fields"] == [omitted_path]
    field = next(field for field in detail["fields"] if field["field_name"] == omitted_path)
    assert field["evidence_status"] == "UNSUPPORTED"
    preview = harness.client.get(f"{url}/fhir-preview", headers=AUTH_A).json()
    if omitted_path == "patient.date_of_birth":
        assert "birthDate" not in next(
            entry["resource"]
            for entry in preview["entry"]
            if entry["resource"]["resourceType"] == "Patient"
        )
    else:
        assert detail["requested_service"] is None
        assert "description" not in next(
            entry["resource"]
            for entry in preview["entry"]
            if entry["resource"]["resourceType"] == "ServiceRequest"
        )
