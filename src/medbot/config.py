from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

DEFAULT_DATABASE_URL = "postgresql+psycopg://medbot:medbot@postgres:5432/medbot"
DEFAULT_DEV_TOKENS_JSON = "{}"
DEFAULT_CORS_ORIGINS = ("http://localhost:3000",)
LOCAL_OLLAMA_HOSTS = {"localhost", "127.0.0.1", "::1", "ollama", "host.docker.internal"}


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str = DEFAULT_DATABASE_URL
    auth_mode: str = "oidc"
    dev_tokens_json: str = DEFAULT_DEV_TOKENS_JSON
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    object_storage: str = "s3"
    s3_endpoint_url: str = "http://minio:9000"
    s3_access_key_id: str = "medbot-local"
    s3_secret_access_key: str = "replace-for-nonlocal-use"
    s3_bucket: str = "medbot-documents"
    s3_region: str = "us-east-1"
    fhir_base_url: str = "http://hapi-fhir:8080/fhir"
    max_upload_bytes: int = 10 * 1024 * 1024
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    document_extractor: str = "fixture"
    ai_provider: str = "fixture"
    ai_model: str = "qwen3:4b"
    ollama_base_url: str = "http://ollama:11434/v1"
    openai_api_key: str | None = field(default=None, repr=False)
    gemini_api_key: str | None = field(default=None, repr=False)
    provider_encryption_key: str | None = field(default=None, repr=False)
    provider_encryption_key_file: str | None = None

    @property
    def ai_hosted(self) -> bool:
        return self.ai_provider in {"openai", "gemini"} or (
            self.ai_provider == "ollama"
            and urlsplit(self.ollama_base_url).hostname not in LOCAL_OLLAMA_HOSTS
        )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if environ is None else environ
        return cls(
            document_extractor=env.get("MEDBOT_DOCUMENT_EXTRACTOR", "fixture"),
            ai_provider=env.get("MEDBOT_AI_PROVIDER", "fixture"),
            ai_model=env.get("MEDBOT_AI_MODEL", "qwen3:4b"),
            ollama_base_url=env.get("MEDBOT_OLLAMA_BASE_URL", "http://ollama:11434/v1"),
            openai_api_key=env.get("MEDBOT_OPENAI_API_KEY"),
            gemini_api_key=env.get("MEDBOT_GEMINI_API_KEY"),
            provider_encryption_key=env.get("MEDBOT_PROVIDER_ENCRYPTION_KEY") or None,
            provider_encryption_key_file=env.get("MEDBOT_PROVIDER_ENCRYPTION_KEY_FILE") or None,
            database_url=env.get("MEDBOT_DATABASE_URL", DEFAULT_DATABASE_URL),
            auth_mode=env.get("MEDBOT_AUTH_MODE", "oidc").lower(),
            dev_tokens_json=env.get("MEDBOT_DEV_TOKENS_JSON", DEFAULT_DEV_TOKENS_JSON),
            oidc_issuer=env.get("MEDBOT_OIDC_ISSUER"),
            oidc_audience=env.get("MEDBOT_OIDC_AUDIENCE"),
            oidc_jwks_url=env.get("MEDBOT_OIDC_JWKS_URL"),
            object_storage=env.get("MEDBOT_OBJECT_STORAGE", "s3").lower(),
            s3_endpoint_url=env.get("MEDBOT_S3_ENDPOINT_URL", "http://minio:9000"),
            s3_access_key_id=env.get("MEDBOT_S3_ACCESS_KEY_ID", "medbot-local"),
            s3_secret_access_key=env.get("MEDBOT_S3_SECRET_ACCESS_KEY", "replace-for-nonlocal-use"),
            s3_bucket=env.get("MEDBOT_S3_BUCKET", "medbot-documents"),
            s3_region=env.get("MEDBOT_S3_REGION", "us-east-1"),
            fhir_base_url=env.get("MEDBOT_FHIR_BASE_URL", "http://hapi-fhir:8080/fhir"),
            max_upload_bytes=int(env.get("MEDBOT_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024))),
            cors_origins=tuple(
                origin.strip()
                for origin in env.get("MEDBOT_CORS_ORIGINS", ",".join(DEFAULT_CORS_ORIGINS)).split(
                    ","
                )
                if origin.strip()
            ),
        )

    def validate(self) -> None:
        if self.document_extractor not in {"fixture", "pdfium-tesseract"}:
            raise ValueError("Unsupported document extractor")
        if self.ai_provider not in {"fixture", "ollama", "openai", "gemini"}:
            raise ValueError("Unsupported AI provider")
        if self.ai_provider in {"openai", "gemini"} and not (
            self.openai_api_key if self.ai_provider == "openai" else self.gemini_api_key
        ):
            raise ValueError("The selected hosted AI provider requires an explicit MedBot API key")
        if not self.ai_model.strip() or len(self.ai_model) > 150:
            raise ValueError("A bounded AI model name is required")
        if self.ai_provider == "ollama":
            url = urlsplit(self.ollama_base_url)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
            ):
                raise ValueError("Invalid Ollama base URL")
            if url.scheme == "http" and url.hostname not in LOCAL_OLLAMA_HOSTS:
                raise ValueError("Remote Ollama requires HTTPS")
            if self.ai_model.endswith("-cloud") or url.hostname.endswith("ollama.com"):
                raise ValueError("The Ollama option requires a local model, not Ollama Cloud")
        if self.auth_mode not in {"dev", "oidc"}:
            raise ValueError("MEDBOT_AUTH_MODE must be 'dev' or 'oidc'")
        if self.object_storage not in {"memory", "s3"}:
            raise ValueError("MEDBOT_OBJECT_STORAGE must be 'memory' or 's3'")
        if self.max_upload_bytes <= 0:
            raise ValueError("MEDBOT_MAX_UPLOAD_BYTES must be positive")
        if self.auth_mode == "oidc" and not all(
            (self.oidc_issuer, self.oidc_audience, self.oidc_jwks_url)
        ):
            raise ValueError("OIDC issuer, audience, and JWKS URL are required in OIDC mode")
        if self.auth_mode == "dev":
            parsed = json.loads(self.dev_tokens_json)
            if not isinstance(parsed, dict) or not parsed:
                raise ValueError("MEDBOT_DEV_TOKENS_JSON must be a non-empty object")
