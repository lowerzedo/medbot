from collections.abc import Callable
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from medbot.ai import FixtureAIProvider
from medbot.ai_settings import CredentialCipher
from medbot.config import Settings
from medbot.errors import ApplicationError
from medbot.models import AISettingsEvent, UserAISettings
from medbot.schemas import ActorContext
from tests.conftest import (
    AUTH_A,
    AUTH_B,
    AUTH_INTAKE,
    AppHarness,
    create_referral,
    synthetic_pdf,
    upload_referral_pdf,
)

URL = "/api/v1/settings/ai"
KEY = "synthetic-personal-key-never-a-real-secret"


def save(harness: AppHarness, provider: str = "openai", revision: int = 0, key: str | None = KEY):
    payload = {"provider": provider, "model": "synthetic-model", "revision": revision}
    if key is not None:
        payload["api_key"] = key
    return harness.client.post(URL, headers=AUTH_A, json=payload)


@pytest.fixture
def secured(app_factory: Callable[..., AppHarness]) -> AppHarness:
    return app_factory(provider_encryption_key=Fernet.generate_key().decode())


def test_personal_keys_are_encrypted_write_only_and_isolated(secured: AppHarness, caplog) -> None:
    assert secured.client.get(URL).status_code == 401
    response = save(secured)
    assert response.status_code == 200
    assert KEY not in response.text and KEY not in caplog.text
    assert response.json()["configured_keys"] == ["openai"]
    assert response.json()["configuration"]["hosted"] is True
    assert secured.client.get(URL, headers=AUTH_A).json() == response.json()
    for headers in (AUTH_B, AUTH_INTAKE):
        other = secured.client.get(URL, headers=headers).json()
        assert other["using_defaults"] and other["configured_keys"] == []
        assert other["revision"] == 0
    with Session(secured.engine) as session:
        row = session.scalar(select(UserAISettings))
        assert row and row.openai_key and KEY.encode() not in row.openai_key
        assert row.organization_id == "org-a" and row.user_id == "reviewer-a"
        events = session.scalars(select(AISettingsEvent)).all()
        assert len(events) == 1 and events[0].action == "SETTINGS_SAVED"
        assert events[0].provider == "openai" and events[0].trace_id


def test_selected_provider_and_personal_key_are_used_only_for_the_processing_actor(
    secured: AppHarness,
) -> None:
    assert save(secured).status_code == 200
    referral_id = create_referral(secured.client)
    upload_referral_pdf(secured.client, referral_id, synthetic_pdf())
    with patch(
        "medbot.ai_settings.StructuredAIProvider", return_value=FixtureAIProvider()
    ) as adapter:
        response = secured.client.post(f"/api/v1/referrals/{referral_id}/process", headers=AUTH_A)
        assert response.status_code == 200
        settings = adapter.call_args.args[0]
        assert settings.ai_provider == "openai" and settings.ai_model == "synthetic-model"
        assert settings.openai_api_key == KEY and settings.gemini_api_key is None
    # Another user in the same organization still uses deployment defaults.
    other = create_referral(secured.client, headers=AUTH_INTAKE)
    upload_referral_pdf(secured.client, other, synthetic_pdf(), headers=AUTH_INTAKE)
    with patch("medbot.ai_settings.StructuredAIProvider") as adapter:
        assert (
            secured.client.post(
                f"/api/v1/referrals/{other}/process", headers=AUTH_INTAKE
            ).status_code
            == 200
        )
        adapter.assert_not_called()


def test_switch_preserves_keys_blank_keeps_key_and_removal_fails_closed(
    secured: AppHarness,
) -> None:
    assert save(secured).status_code == 200
    assert save(secured, revision=1, key=None).status_code == 200
    switched = save(secured, "gemini", revision=2)
    assert switched.json()["configured_keys"] == ["openai", "gemini"]
    removed = secured.client.post(
        f"{URL}/remove-key", headers=AUTH_A, json={"provider": "gemini", "revision": 3}
    )
    assert removed.json()["configured_keys"] == ["openai"]
    referral_id = create_referral(secured.client)
    upload_referral_pdf(secured.client, referral_id, synthetic_pdf())
    failed = secured.client.post(f"/api/v1/referrals/{referral_id}/process", headers=AUTH_A)
    assert failed.status_code == 422 and failed.json()["error"]["code"] == "api_key_required"
    defaults = secured.client.post(f"{URL}/use-defaults", headers=AUTH_A, json={"revision": 4})
    assert defaults.json()["using_defaults"] and defaults.json()["configured_keys"] == ["openai"]
    assert (
        secured.client.post(f"/api/v1/referrals/{referral_id}/process", headers=AUTH_A).status_code
        == 200
    )


def test_stale_saves_and_client_scope_injection_are_rejected(secured: AppHarness) -> None:
    assert save(secured).status_code == 200
    stale = save(secured, key="synthetic-key-that-must-not-replace")
    assert stale.status_code == 409
    injected = secured.client.post(
        URL,
        headers=AUTH_A,
        json={
            "provider": "openai",
            "model": "synthetic-model",
            "api_key": KEY,
            "revision": 1,
            "organization_id": "org-b",
            "user_id": "reviewer-b",
        },
    )
    assert injected.status_code == 422 and KEY not in injected.text


def test_credential_storage_fails_closed_without_master_key(harness: AppHarness) -> None:
    assert harness.client.get(URL, headers=AUTH_A).json()["credential_storage_available"] is False
    assert save(harness).status_code == 503
    assert save(harness, "ollama", key=None).status_code == 200
    assert save(harness, "ollama", revision=1).status_code == 422
    with Session(harness.engine) as session:
        assert session.scalar(select(UserAISettings)).openai_key is None


@pytest.mark.parametrize(
    "key", ["short", "synthetic key with spaces", "synthetic-key\nnewline", "x" * 2049]
)
def test_invalid_keys_never_appear_in_error_responses(secured: AppHarness, key: str) -> None:
    response = save(secured, key=key)
    assert response.status_code == 422
    assert key not in response.text


def test_cipher_authenticates_user_organization_and_provider() -> None:
    from pydantic import SecretStr

    cipher = CredentialCipher(Settings(provider_encryption_key=Fernet.generate_key().decode()))
    actor = ActorContext(
        user_id="user", organization_id="org", roles=frozenset(), auth_method="dev"
    )
    encrypted = cipher.encrypt(actor, "openai", SecretStr(KEY))
    assert cipher.decrypt(actor, "openai", encrypted) == KEY
    for changed, provider in (
        (actor.model_copy(update={"user_id": "another"}), "openai"),
        (actor.model_copy(update={"organization_id": "another"}), "openai"),
        (actor, "gemini"),
    ):
        with pytest.raises(ApplicationError, match="cannot be read"):
            cipher.decrypt(changed, provider, encrypted)
    with pytest.raises(ApplicationError):
        cipher.decrypt(actor, "openai", encrypted[:-4] + b"bad!")
