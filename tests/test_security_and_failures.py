from __future__ import annotations

import logging
from collections.abc import Callable
from typing import cast

import pytest

from medbot.ai import AIProvider
from medbot.auth import OIDCClaims, known_medbot_roles
from medbot.config import Settings
from medbot.document_processing import ExtractedTextBlock
from medbot.domain import Role
from medbot.storage import MalwareScanner, ObjectStorage
from tests.conftest import (
    AUTH_A,
    AppHarness,
    create_referral,
    synthetic_pdf,
    upload_referral_pdf,
)


class MutableStorage(ObjectStorage):
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, content: bytes, content_type: str) -> None:
        self.objects[key] = bytes(content)

    def get(self, key: str) -> bytes:
        return self.objects[key]

    def ready(self) -> bool:
        return True


class UnavailableReadinessStorage(MutableStorage):
    def ready(self) -> bool:
        raise RuntimeError("synthetic provider detail")


class ExplodingProvider(AIProvider):
    def extract(self, blocks: tuple[ExtractedTextBlock, ...]) -> object:
        raise RuntimeError("Synthetic-Sensitive-Model-Value-9281")


class SecondUploadRejectedScanner(MalwareScanner):
    def __init__(self) -> None:
        self.calls = 0

    def is_safe(self, content: bytes) -> bool:
        self.calls += 1
        return self.calls == 1


def test_oidc_ignores_unrelated_keycloak_roles() -> None:
    claims = OIDCClaims.model_validate(
        {
            "sub": "synthetic-user",
            "organization_id": "org-a",
            "roles": ["reviewer", "offline_access"],
            "realm_access": {"roles": ["uma_authorization", "supervisor"]},
        }
    )
    assert known_medbot_roles(claims.roles | claims.realm_access.roles) == {
        Role.REVIEWER,
        Role.SUPERVISOR,
    }


def test_default_configuration_fails_closed_without_oidc_settings() -> None:
    settings = Settings.from_env({})
    assert settings.auth_mode == "oidc"
    with pytest.raises(ValueError, match="OIDC issuer"):
        settings.validate()


def test_replaced_object_bytes_are_neither_served_nor_processed(
    app_factory: Callable[..., AppHarness],
) -> None:
    storage = MutableStorage()
    harness = app_factory(storage=storage)
    referral_id = create_referral(harness.client)
    uploaded = upload_referral_pdf(harness.client, referral_id, synthetic_pdf())
    document = cast(dict[str, object], uploaded["document"])
    document_id = str(document["id"])
    storage_key = next(iter(storage.objects))
    content = storage.objects[storage_key]
    storage.objects[storage_key] = content[:-1] + bytes([content[-1] ^ 1])

    content_response = harness.client.get(
        f"/api/v1/referrals/{referral_id}/documents/{document_id}/content",
        headers=AUTH_A,
    )
    assert content_response.status_code == 409
    assert content_response.json()["error"]["code"] == "document_integrity_failed"

    processed = harness.client.post(f"/api/v1/referrals/{referral_id}/process", headers=AUTH_A)
    assert processed.status_code == 409
    assert processed.json()["error"]["code"] == "document_integrity_failed"
    detail = harness.client.get(f"/api/v1/referrals/{referral_id}", headers=AUTH_A)
    assert detail.json()["status"] == "FAILED"


def test_provider_failure_is_safe_and_marks_processing_failed(
    app_factory: Callable[..., AppHarness], caplog: pytest.LogCaptureFixture
) -> None:
    harness = app_factory(ai_provider=ExplodingProvider())
    referral_id = create_referral(harness.client)
    upload_referral_pdf(harness.client, referral_id, synthetic_pdf())

    with caplog.at_level(logging.INFO):
        response = harness.client.post(f"/api/v1/referrals/{referral_id}/process", headers=AUTH_A)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ai_provider_unavailable"
    assert "Synthetic-Sensitive-Model-Value-9281" not in response.text
    assert "Synthetic-Sensitive-Model-Value-9281" not in caplog.text
    detail = harness.client.get(f"/api/v1/referrals/{referral_id}", headers=AUTH_A)
    assert detail.json()["status"] == "FAILED"


def test_readiness_normalizes_provider_exceptions(
    app_factory: Callable[..., AppHarness],
) -> None:
    harness = app_factory(storage=UnavailableReadinessStorage())
    response = harness.client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_unsafe_supplemental_upload_quarantines_and_audits(
    app_factory: Callable[..., AppHarness],
) -> None:
    harness = app_factory(scanner=SecondUploadRejectedScanner())
    referral_id = create_referral(harness.client)
    upload_referral_pdf(harness.client, referral_id, synthetic_pdf(include_dob=False))
    processed = harness.client.post(f"/api/v1/referrals/{referral_id}/process", headers=AUTH_A)
    assert processed.json()["status"] == "NEEDS_REVIEW"

    rejected = harness.client.post(
        f"/api/v1/referrals/{referral_id}/documents",
        headers=AUTH_A,
        files={
            "file": (
                "synthetic-supplement.pdf",
                synthetic_pdf(prompt_injection=True),
                "application/pdf",
            )
        },
        data={"classification": "supporting_document"},
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "unsafe_document"
    detail = harness.client.get(f"/api/v1/referrals/{referral_id}", headers=AUTH_A)
    assert detail.json()["status"] == "QUARANTINED"
    audit = harness.client.get(f"/api/v1/referrals/{referral_id}/audit", headers=AUTH_A)
    assert any(event["action"] == "DOCUMENT_QUARANTINED" for event in audit.json())
