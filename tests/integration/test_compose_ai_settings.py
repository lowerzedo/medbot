"""Personal credentials and provider selection on the actual identity/database/OCR stack."""

import json
import subprocess
from pathlib import Path

import httpx
import pytest

from scripts.evaluate_documents import token_for
from tests.integration.test_compose_recovery import (
    approve_supported_fields,
    checked_detail,
    route_and_verify,
    upload,
)

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
KEY = "synthetic-compose-key-not-valid-for-any-provider"


@pytest.fixture
def settings_client(client: httpx.Client):
    with httpx.Client(
        base_url=client.base_url,
        timeout=190,
        headers={"Authorization": f"Bearer {token_for('synthetic-settings-alpha')}"},
    ) as personal:
        yield personal
        state = personal.get("settings/ai").json()
        for provider in state["configured_keys"]:
            response = personal.post(
                "settings/ai/remove-key", json={"provider": provider, "revision": state["revision"]}
            )
            assert response.status_code == 200
            state = response.json()
        assert (
            personal.post(
                "settings/ai/use-defaults", json={"revision": state["revision"]}
            ).status_code
            == 200
        )


def test_compose_personal_key_lifecycle_is_encrypted_and_scoped(
    settings_client: httpx.Client,
    client: httpx.Client,
) -> None:
    before = client.get("settings/ai").json()
    current = settings_client.get("settings/ai").json()
    assert current["credential_storage_available"]
    for provider in ("openai", "gemini"):
        saved = settings_client.post(
            "settings/ai",
            json={
                "provider": provider,
                "model": "synthetic-contract-model",
                "api_key": KEY,
                "revision": current["revision"],
            },
        )
        assert saved.status_code == 200
        assert KEY not in saved.text
        current = saved.json()
        assert provider in current["configured_keys"]
        assert current["configuration"]["hosted"]
    assert settings_client.get("settings/ai").json() == current
    assert client.get("settings/ai").json() == before
    actor = settings_client.get("auth/me").json()
    probe = """import json,sys
from medbot.database import create_database_engine
from medbot.config import Settings
from medbot.models import UserAISettings
from sqlalchemy.orm import Session
with Session(create_database_engine(Settings.from_env().database_url)) as session:
 data=json.loads(sys.stdin.buffer.read())
 row=session.get(UserAISettings,(data['organization_id'],data['user_id']))
 key=data['key'].encode()
 paired=bool(row and row.openai_key and row.gemini_key)
 encrypted=paired and key not in row.openai_key and key not in row.gemini_key
 print(json.dumps({'encrypted_pair_present':bool(encrypted)}))
"""
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "api", "python", "-c", probe],
        input=json.dumps(
            {"key": KEY, "organization_id": actor["organization_id"], "user_id": actor["user_id"]}
        ).encode(),
        capture_output=True,
        timeout=20,
        check=True,
        cwd=ROOT,
    )
    assert json.loads(result.stdout)["encrypted_pair_present"]
    stale = settings_client.post(
        "settings/ai",
        json={
            "provider": "ollama",
            "model": "synthetic-model",
            "revision": current["revision"] - 1,
        },
    )
    assert stale.status_code == 409
    removed = settings_client.post(
        "settings/ai/remove-key", json={"provider": "gemini", "revision": current["revision"]}
    )
    assert removed.status_code == 200 and "gemini" not in removed.json()["configured_keys"]


def test_compose_personal_ollama_settings_drive_reviewed_fhir_workflow(
    settings_client: httpx.Client,
) -> None:
    current = settings_client.get("settings/ai").json()
    # Defaults were restored by the credential-lifecycle fixture.
    config = current["configuration"]
    if config["ai_provider"] != "ollama":
        pytest.skip("Run with make dev-real for actual personal Ollama extraction")
    saved = settings_client.post(
        "settings/ai",
        json={"provider": "ollama", "model": config["ai_model"], "revision": current["revision"]},
    )
    assert saved.status_code == 200 and saved.json()["using_defaults"] is False
    assert settings_client.get("processing-configuration").json()["ai_model"] == config["ai_model"]
    referral = checked_detail(settings_client.post("referrals", json={"specialty": "cardiology"}))
    upload(
        settings_client,
        referral.id,
        (ROOT / "synthetic/documents/clean-scan.pdf").read_bytes(),
        "referral_order",
    )
    referral = checked_detail(settings_client.post(f"referrals/{referral.id}/process"))
    assert len(referral.fields) == 4 and referral.status == "NEEDS_REVIEW"
    assert settings_client.post(f"referrals/{referral.id}/route").status_code == 409
    referral = approve_supported_fields(settings_client, referral)
    route_and_verify(settings_client, referral, "1970-01-02")
