"""Personal provider settings; credentials never leave the backend as response data."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Literal
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from medbot.ai import AIProvider
from medbot.config import Settings
from medbot.errors import ApplicationError, ConflictError
from medbot.model_extraction import StructuredAIProvider
from medbot.models import AISettingsEvent, UserAISettings, utc_now
from medbot.schemas import ActorContext, ProcessingConfigurationResponse, StrictSchema

Provider = Literal["ollama", "openai", "gemini"]
HostedProvider = Literal["openai", "gemini"]


class ProviderModels(StrictSchema):
    ollama: str
    openai: str
    gemini: str


class AISettingsResponse(StrictSchema):
    configuration: ProcessingConfigurationResponse
    using_defaults: bool
    revision: int
    models: ProviderModels
    configured_keys: list[HostedProvider]
    credential_storage_available: bool


class SettingsRevision(StrictSchema):
    revision: int = Field(ge=0)


class AISettingsSave(SettingsRevision):
    provider: Provider
    model: str = Field(min_length=1, max_length=150, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
    api_key: SecretStr | None = Field(default=None, repr=False)

    @field_validator("api_key")
    @classmethod
    def validate_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            key = value.get_secret_value()
            if not 8 <= len(key) <= 2048 or any(ord(char) < 33 or ord(char) > 126 for char in key):
                raise ValueError("A bounded API key without whitespace is required")
        return value


class AIKeyRemove(SettingsRevision):
    provider: HostedProvider


class CredentialPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    organization_id: str
    user_id: str
    provider: HostedProvider
    key: SecretStr


class CredentialCipher:
    def __init__(self, settings: Settings) -> None:
        key = settings.provider_encryption_key
        if settings.provider_encryption_key_file:
            if key:
                raise ValueError("Configure one provider encryption key source")
            try:
                key = Path(settings.provider_encryption_key_file).read_text().strip()
            except OSError:
                raise ValueError("Provider encryption key file is unavailable") from None
        try:
            self._fernet = Fernet(key) if key else None
        except (ValueError, TypeError):
            raise ValueError("Provider encryption key must be a valid Fernet key") from None

    @property
    def available(self) -> bool:
        return self._fernet is not None

    def encrypt(self, actor: ActorContext, provider: HostedProvider, key: SecretStr) -> bytes:
        if self._fernet is None:
            raise ApplicationError(
                "credential_storage_unavailable", "Secure key storage is unavailable", 503
            )
        # Authenticate the principal and provider as well as the key to prevent ciphertext swapping.
        payload = CredentialPayload(
            organization_id=actor.organization_id, user_id=actor.user_id, provider=provider, key=key
        )
        data = payload.model_dump(exclude={"key"}) | {"key": key.get_secret_value()}
        return self._fernet.encrypt(json.dumps(data).encode())

    def decrypt(self, actor: ActorContext, provider: HostedProvider, value: bytes) -> str:
        try:
            if self._fernet is None:
                raise InvalidToken
            payload = CredentialPayload.model_validate_json(self._fernet.decrypt(value))
            if (payload.organization_id, payload.user_id, payload.provider) != (
                actor.organization_id,
                actor.user_id,
                provider,
            ):
                raise InvalidToken
            return payload.key.get_secret_value()
        except (InvalidToken, ValidationError):
            raise ApplicationError(
                "credential_unavailable",
                "The saved key cannot be read; replace it in settings",
                503,
            ) from None


class AISettingsService:
    def __init__(self, settings: Settings, default_provider: AIProvider) -> None:
        self._settings = settings
        self._default_provider = default_provider
        self._cipher = CredentialCipher(settings)

    @staticmethod
    def _row(session: Session, actor: ActorContext, *, lock: bool = False) -> UserAISettings | None:
        statement = select(UserAISettings).where(
            UserAISettings.organization_id == actor.organization_id,
            UserAISettings.user_id == actor.user_id,
        )
        return session.scalar(statement.with_for_update() if lock else statement)

    def _models(self, row: UserAISettings | None) -> ProviderModels:
        return ProviderModels(
            ollama=row.ollama_model
            if row
            else (
                self._settings.ai_model if self._settings.ai_provider == "ollama" else "qwen3:4b"
            ),
            openai=row.openai_model if row else "",
            gemini=row.gemini_model if row else "",
        )

    def configuration(
        self, session: Session, actor: ActorContext
    ) -> ProcessingConfigurationResponse:
        row = self._row(session, actor)
        settings = self._settings
        if row and row.active_provider:
            settings = replace(
                settings,
                ai_provider=row.active_provider,
                ai_model=getattr(row, f"{row.active_provider}_model"),
            )
        return ProcessingConfigurationResponse.model_validate(
            {
                "document_extractor": settings.document_extractor,
                "ai_provider": settings.ai_provider,
                "ai_model": None if settings.ai_provider == "fixture" else settings.ai_model,
                "hosted": settings.ai_hosted,
            }
        )

    def read(self, session: Session, actor: ActorContext) -> AISettingsResponse:
        row = self._row(session, actor)
        configured: list[HostedProvider] = []
        if row and row.openai_key:
            configured.append("openai")
        if row and row.gemini_key:
            configured.append("gemini")
        return AISettingsResponse(
            configuration=self.configuration(session, actor),
            using_defaults=row is None or row.active_provider is None,
            revision=row.revision if row else 0,
            models=self._models(row),
            configured_keys=configured,
            credential_storage_available=self._cipher.available,
        )

    def _editable(self, session: Session, actor: ActorContext, revision: int) -> UserAISettings:
        row = self._row(session, actor, lock=True)
        if (row.revision if row else 0) != revision:
            raise ConflictError(
                "settings_changed", "Settings changed in another session; reload before saving"
            )
        if row is None:
            row = UserAISettings(
                organization_id=actor.organization_id,
                user_id=actor.user_id,
                ollama_model=self._models(None).ollama,
                openai_model="",
                gemini_model="",
                revision=0,
            )
            session.add(row)
        return row

    def _commit(
        self,
        session: Session,
        actor: ActorContext,
        row: UserAISettings,
        action: str,
        provider: str | None,
        trace_id: str,
    ) -> AISettingsResponse:
        row.revision += 1
        row.updated_at = utc_now()
        session.add(
            AISettingsEvent(
                id=str(uuid4()),
                organization_id=actor.organization_id,
                user_id=actor.user_id,
                action=action,
                provider=provider,
                trace_id=trace_id,
            )
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise ConflictError(
                "settings_changed", "Settings changed in another session; reload before saving"
            ) from None
        return self.read(session, actor)

    def save(
        self, session: Session, actor: ActorContext, request: AISettingsSave, trace_id: str
    ) -> AISettingsResponse:
        row = self._editable(session, actor, request.revision)
        if request.provider == "ollama":
            if request.api_key is not None:
                raise ApplicationError(
                    "unexpected_api_key", "Ollama does not use a personal API key", 422
                )
            try:
                replace(self._settings, ai_provider="ollama", ai_model=request.model).validate()
            except ValueError:
                raise ApplicationError(
                    "invalid_local_model", "Choose a local Ollama model", 422
                ) from None
            row.ollama_model = request.model
        else:
            if request.api_key is not None:
                encrypted = self._cipher.encrypt(actor, request.provider, request.api_key)
                if request.provider == "openai":
                    row.openai_key = encrypted
                else:
                    row.gemini_key = encrypted
            saved_key = row.openai_key if request.provider == "openai" else row.gemini_key
            if not saved_key:
                raise ApplicationError(
                    "api_key_required", "Add your own API key for this provider", 422
                )
            if request.provider == "openai":
                row.openai_model = request.model
            else:
                row.gemini_model = request.model
        row.active_provider = request.provider
        return self._commit(session, actor, row, "SETTINGS_SAVED", request.provider, trace_id)

    def remove_key(
        self, session: Session, actor: ActorContext, request: AIKeyRemove, trace_id: str
    ) -> AISettingsResponse:
        row = self._editable(session, actor, request.revision)
        if request.provider == "openai":
            row.openai_key = None
        else:
            row.gemini_key = None
        return self._commit(session, actor, row, "KEY_REMOVED", request.provider, trace_id)

    def use_defaults(
        self, session: Session, actor: ActorContext, request: SettingsRevision, trace_id: str
    ) -> AISettingsResponse:
        row = self._editable(session, actor, request.revision)
        row.active_provider = None
        return self._commit(session, actor, row, "DEFAULTS_SELECTED", None, trace_id)

    def provider_for(self, session: Session, actor: ActorContext) -> AIProvider:
        row = self._row(session, actor)
        if row is None or row.active_provider is None:
            return self._default_provider
        provider = row.active_provider
        key: str | None = None
        if provider in {"openai", "gemini"}:
            encrypted = row.openai_key if provider == "openai" else row.gemini_key
            if not encrypted:
                raise ApplicationError(
                    "api_key_required", "Add your own API key in settings before processing", 422
                )
            key = self._cipher.decrypt(
                actor, "openai" if provider == "openai" else "gemini", encrypted
            )
        settings = replace(
            self._settings,
            ai_provider=provider,
            ai_model=getattr(row, f"{provider}_model"),
            openai_api_key=key if provider == "openai" else None,
            gemini_api_key=key if provider == "gemini" else None,
        )
        settings.validate()
        return StructuredAIProvider(settings)
