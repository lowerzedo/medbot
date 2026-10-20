from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from medbot.auth import Authenticator
from medbot.config import Settings
from medbot.domain import Role
from medbot.errors import UnauthenticatedError

# Reproduce a token minted just across a second boundary by a slightly faster issuer.
API_NOW = datetime(2026, 10, 20, 12, 0, 0, 950000, tzinfo=UTC)
API_SECOND = int(API_NOW.timestamp())
ISSUER = "https://synthetic-issuer.invalid/realms/medbot"


def test_local_organization_attribute_is_managed_by_identity_administrators_only() -> None:
    realm = json.loads(
        (Path(__file__).resolve().parents[1] / "infra/local/keycloak/medbot-realm.json").read_text()
    )
    component = realm["components"]["org.keycloak.userprofile.UserProfileProvider"][0]
    profile = json.loads(component["config"]["kc.user.profile.config"][0])
    organization = next(item for item in profile["attributes"] if item["name"] == "organization_id")
    assert organization["permissions"] == {"view": ["admin"], "edit": ["admin"]}
    assert profile.get("unmanagedAttributePolicy") in {None, "DISABLED", "ADMIN_EDIT"}


class APIClock(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        return API_NOW.astimezone(tz) if tz else API_NOW.replace(tzinfo=None)


@pytest.fixture(scope="module")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def authenticator(monkeypatch: pytest.MonkeyPatch, signing_key: rsa.RSAPrivateKey) -> Authenticator:
    monkeypatch.setattr("jwt.api_jwt.datetime", APIClock)
    # Only key retrieval is replaced. Real RSA signature and JWT claim validation run below.
    with patch("medbot.auth.PyJWKClient") as client:
        client.return_value.get_signing_key_from_jwt.return_value.key = signing_key.public_key()
        return Authenticator(
            Settings(
                oidc_issuer=ISSUER,
                oidc_audience="medbot-api",
                oidc_jwks_url=f"{ISSUER}/protocol/openid-connect/certs",
            )
        )


def access_token(signing_key: rsa.RSAPrivateKey, **overrides: object) -> str:
    claims: dict[str, object] = {
        "iss": ISSUER,
        "aud": "medbot-api",
        "sub": "synthetic-oidc-user",
        "organization_id": "org-synthetic-alpha",
        "realm_access": {"roles": ["administrator", "offline_access"]},
        "iat": API_SECOND,
        "exp": API_SECOND + 300,
    }
    claims.update(overrides)
    return jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "synthetic-key"})


@pytest.mark.parametrize("claim", ["iat", "nbf"])
def test_fresh_signed_token_tolerates_subsecond_issuer_clock_skew(
    authenticator: Authenticator, signing_key: rsa.RSAPrivateKey, claim: str
) -> None:
    # The issuer is only 50 ms ahead, but its integer NumericDate is the next second.
    token = access_token(signing_key, **{claim: API_SECOND + 1})
    actor = authenticator.authenticate(f"Bearer {token}")
    assert actor.auth_method == "oidc"
    assert actor.user_id == "synthetic-oidc-user"
    assert actor.organization_id == "org-synthetic-alpha"
    assert actor.roles == {Role.ADMINISTRATOR}


@pytest.mark.parametrize("claim", ["iat", "nbf"])
def test_signed_token_beyond_clock_tolerance_is_rejected(
    authenticator: Authenticator, signing_key: rsa.RSAPrivateKey, claim: str
) -> None:
    token = access_token(signing_key, **{claim: API_SECOND + 6})
    with pytest.raises(UnauthenticatedError):
        authenticator.authenticate(f"Bearer {token}")


@pytest.mark.parametrize(
    "overrides",
    [
        {"exp": API_SECOND - 5},
        {"iss": "https://another-synthetic-issuer.invalid"},
        {"aud": "another-synthetic-api"},
        {"organization_id": ""},
        {"iat": "invalid-synthetic-timestamp"},
        {"iat": None},
        {"exp": None},
        {"sub": None},
    ],
)
def test_clock_tolerance_preserves_token_and_tenant_claim_validation(
    authenticator: Authenticator,
    signing_key: rsa.RSAPrivateKey,
    overrides: dict[str, object],
) -> None:
    token = access_token(signing_key, **overrides)
    with pytest.raises(UnauthenticatedError):
        authenticator.authenticate(f"Bearer {token}")


def test_wrong_signature_is_rejected_and_logs_expose_no_token_or_claims(
    authenticator: Authenticator, caplog: pytest.LogCaptureFixture
) -> None:
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = access_token(other_key, sub="synthetic-private-claim-must-not-be-logged")
    with (
        caplog.at_level(logging.INFO, logger="medbot.auth"),
        pytest.raises(UnauthenticatedError) as caught,
    ):
        authenticator.authenticate(f"Bearer {token}")
    assert "InvalidSignatureError" in caplog.text
    assert token not in caplog.text
    assert "synthetic-private-claim-must-not-be-logged" not in caplog.text
    assert "synthetic-private-claim-must-not-be-logged" not in str(caught.value)
