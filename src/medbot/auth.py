import json
import logging
from collections.abc import Callable, Iterable
from typing import Annotated

import jwt
from fastapi import Depends, Request
from jwt import PyJWKClient
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from medbot.config import Settings
from medbot.domain import Role
from medbot.errors import ForbiddenError, UnauthenticatedError
from medbot.schemas import ActorContext

logger = logging.getLogger(__name__)

# Issuer and API clocks can straddle a NumericDate second during an immediate login request.
# Keep tolerance bounded; all signature, issuer, audience, and temporal checks still run.
OIDC_CLOCK_SKEW_SECONDS = 5


class DevIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    organization_id: str
    roles: frozenset[Role]


class RealmAccess(BaseModel):
    model_config = ConfigDict(extra="ignore")

    roles: frozenset[str] = frozenset()


class OIDCClaims(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sub: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    organization_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    roles: frozenset[str] = frozenset()
    realm_access: RealmAccess = RealmAccess()


class Authenticator:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._dev_identities: dict[str, DevIdentity] = {}
        self._jwks_client: PyJWKClient | None = None
        if settings.auth_mode == "dev":
            raw: object = json.loads(settings.dev_tokens_json)
            if not isinstance(raw, dict):
                raise ValueError("Dev token configuration must be an object")
            for token, identity in raw.items():
                if not isinstance(token, str):
                    raise ValueError("Dev tokens must be strings")
                self._dev_identities[token] = DevIdentity.model_validate(identity)
        else:
            if settings.oidc_jwks_url is None:
                raise ValueError("OIDC JWKS URL is required")
            self._jwks_client = PyJWKClient(settings.oidc_jwks_url)

    def authenticate(self, authorization: str | None) -> ActorContext:
        token = self._bearer_token(authorization)
        if self._settings.auth_mode == "dev":
            identity = self._dev_identities.get(token)
            if identity is None:
                raise UnauthenticatedError()
            return ActorContext(
                user_id=identity.user_id,
                organization_id=identity.organization_id,
                roles=identity.roles,
                auth_method="dev",
            )
        return self._authenticate_oidc(token)

    @staticmethod
    def _bearer_token(authorization: str | None) -> str:
        if authorization is None:
            raise UnauthenticatedError()
        scheme, separator, token = authorization.partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not token.strip():
            raise UnauthenticatedError()
        return token.strip()

    def _authenticate_oidc(self, token: str) -> ActorContext:
        if self._jwks_client is None:
            raise UnauthenticatedError()
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            decoded: object = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "ES256"],
                audience=self._settings.oidc_audience,
                issuer=self._settings.oidc_issuer,
                leeway=OIDC_CLOCK_SKEW_SECONDS,
                options={"require": ["exp", "iat", "sub"]},
            )
            claims = OIDCClaims.model_validate(decoded)
        except (jwt.PyJWTError, ValidationError, ValueError) as error:
            # Error text can contain claims or provider details; log only the error class.
            logger.info("OIDC token rejected: %s", type(error).__name__)
            raise UnauthenticatedError() from None
        roles = known_medbot_roles(claims.roles | claims.realm_access.roles)
        return ActorContext(
            user_id=claims.sub,
            organization_id=claims.organization_id,
            roles=roles,
            auth_method="oidc",
        )


def known_medbot_roles(values: Iterable[str]) -> frozenset[Role]:
    return frozenset(Role(value) for value in values if value in Role._value2member_map_)


def actor_dependency(authenticator: Authenticator) -> Callable[[Request], ActorContext]:
    def get_actor(request: Request) -> ActorContext:
        return authenticator.authenticate(request.headers.get("authorization"))

    return get_actor


def require_roles(
    get_actor: Callable[[Request], ActorContext], allowed: frozenset[Role]
) -> Callable[[ActorContext], ActorContext]:
    def authorize(actor: Annotated[ActorContext, Depends(get_actor)]) -> ActorContext:
        if not actor.roles.intersection(allowed):
            raise ForbiddenError()
        return actor

    return authorize


READ_ROLES = frozenset(Role)
WRITE_ROLES = frozenset({Role.INTAKE_SPECIALIST, Role.ADMINISTRATOR, Role.SERVICE})
REVIEW_ROLES = frozenset({Role.REVIEWER, Role.SUPERVISOR, Role.ADMINISTRATOR})
AUDIT_ROLES = frozenset({Role.REVIEWER, Role.SUPERVISOR, Role.AUDITOR, Role.ADMINISTRATOR})
