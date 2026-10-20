from __future__ import annotations

from collections.abc import Callable

import httpx
from sqlalchemy.orm import Session

from medbot.ai import AIProvider
from medbot.document_processing import ExtractedTextBlock
from medbot.fhir import HAPIFHIRProvider, build_fhir_bundle
from medbot.models import Referral
from medbot.schemas import AIExtractionOutput
from tests.conftest import (
    AUTH_A,
    AppHarness,
    create_referral,
    synthetic_pdf,
    upload_referral_pdf,
)


class FabricatingProvider(AIProvider):
    def extract(self, blocks: tuple[ExtractedTextBlock, ...]) -> object:
        source = blocks[0]
        return {
            "schema_version": "1.0",
            "fields": [
                {
                    "path": "patient.full_name",
                    "value": "Fabricated Synthetic Name",
                    "confidence": 0.99,
                    "evidence": [
                        {
                            "document_id": source.document_id,
                            "page_number": source.page_number,
                            "block_id": "fabricated-block",
                            "text_sha256": source.text_sha256,
                            "quote": "Fabricated Synthetic Name",
                        }
                    ],
                }
            ],
        }


class MisrepresentingProvider(AIProvider):
    def extract(self, blocks: tuple[ExtractedTextBlock, ...]) -> object:
        source = next(block for block in blocks if block.text.startswith("Patient Name:"))
        return {
            "schema_version": "1.0",
            "fields": [
                {
                    "path": "patient.full_name",
                    "value": "Invented Synthetic Name",
                    "confidence": 0.99,
                    "evidence": [
                        {
                            "document_id": source.document_id,
                            "page_number": source.page_number,
                            "block_id": source.id,
                            "text_sha256": source.text_sha256,
                            "quote": source.text,
                        }
                    ],
                }
            ],
        }


def test_fabricated_model_evidence_is_rejected(
    app_factory: Callable[..., AppHarness],
) -> None:
    harness = app_factory(ai_provider=FabricatingProvider())
    referral_id = create_referral(harness.client)
    upload_referral_pdf(harness.client, referral_id, synthetic_pdf())
    response = harness.client.post(f"/api/v1/referrals/{referral_id}/process", headers=AUTH_A)
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["status"] == "NEEDS_REVIEW"
    assert not detail["fields"]
    assert any(issue["code"] == "unsupported_evidence" for issue in detail["validation_issues"])


def test_real_citation_cannot_support_an_invented_value(
    app_factory: Callable[..., AppHarness],
) -> None:
    harness = app_factory(ai_provider=MisrepresentingProvider())
    referral_id = create_referral(harness.client)
    upload_referral_pdf(harness.client, referral_id, synthetic_pdf())
    response = harness.client.post(f"/api/v1/referrals/{referral_id}/process", headers=AUTH_A)
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["status"] == "NEEDS_REVIEW"
    assert not detail["fields"]
    assert any(issue["code"] == "unsupported_evidence" for issue in detail["validation_issues"])


def test_structured_ai_output_forbids_unknown_fields() -> None:
    invalid = {
        "schema_version": "1.0",
        "fields": [],
        "workflow_state": "ROUTED",
    }
    try:
        AIExtractionOutput.model_validate(invalid)
    except ValueError:
        return
    raise AssertionError("Unknown model-output fields must be rejected")


def test_hapi_adapter_uses_deterministic_puts_for_retries(harness: AppHarness) -> None:
    referral_id = create_referral(harness.client)
    upload_referral_pdf(harness.client, referral_id, synthetic_pdf())
    processed = harness.client.post(f"/api/v1/referrals/{referral_id}/process", headers=AUTH_A)
    assert processed.status_code == 200
    with Session(harness.engine) as session:
        referral = session.get(Referral, referral_id)
        assert referral is not None
        bundle = build_fhir_bundle(referral)
        task = next(
            entry.resource for entry in bundle.entry if entry.resource.resourceType == "Task"
        )
        provenance = next(
            entry.resource for entry in bundle.entry if entry.resource.resourceType == "Provenance"
        )
        assert any(reference.reference == f"Task/{task.id}" for reference in provenance.target)

    calls: list[str] = []
    seen: set[str] = set()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        status = 200 if str(request.url) in seen else 201
        seen.add(str(request.url))
        return httpx.Response(status, request=request)

    provider = HAPIFHIRProvider(
        "http://fhir.test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        backoff=lambda _: None,
    )
    first = provider.write(bundle)
    second = provider.write(bundle)
    assert first.idempotent is False
    assert second.idempotent is True
    half = len(calls) // 2
    assert calls[:half] == calls[half:]
    assert all("/Patient/" in url or "/" in url for url in calls)


def test_fhir_attachment_digest_is_distinct_from_storage_integrity(harness: AppHarness) -> None:
    import base64
    import hashlib

    from sqlalchemy import select

    from medbot.models import Document

    referral_id = create_referral(harness.client)
    content = synthetic_pdf(include_dob=False)
    upload_referral_pdf(harness.client, referral_id, content)
    with Session(harness.engine) as session:
        document = session.scalar(select(Document).where(Document.referral_id == referral_id))
        assert document is not None
        assert document.sha256 == hashlib.sha256(content).hexdigest()
        assert document.fhir_sha1 == hashlib.sha1(content, usedforsecurity=False).hexdigest()
        # Simulate a row migrated from the previous schema, without altering workflow state.
        document.fhir_sha1 = None
        session.commit()
    preview = harness.client.get(
        f"/api/v1/referrals/{referral_id}/fhir-preview", headers=AUTH_A
    ).json()
    reference = next(
        entry["resource"]
        for entry in preview["entry"]
        if entry["resource"]["resourceType"] == "DocumentReference"
    )
    assert "hash" not in reference["content"][0]["attachment"]
    assert (
        harness.client.post(f"/api/v1/referrals/{referral_id}/process", headers=AUTH_A).status_code
        == 200
    )
    preview = harness.client.get(
        f"/api/v1/referrals/{referral_id}/fhir-preview", headers=AUTH_A
    ).json()
    reference = next(
        entry["resource"]
        for entry in preview["entry"]
        if entry["resource"]["resourceType"] == "DocumentReference"
    )
    assert (
        base64.b64decode(reference["content"][0]["attachment"]["hash"])
        == hashlib.sha1(content, usedforsecurity=False).digest()
    )
