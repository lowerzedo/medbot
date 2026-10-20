from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable, Generator
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse

from medbot.ai import AIProvider, FixtureAIProvider
from medbot.ai_settings import AISettingsService
from medbot.api import create_api_router
from medbot.auth import Authenticator, actor_dependency
from medbot.config import Settings
from medbot.database import create_database_engine, create_session_factory
from medbot.document_processing import (
    DocumentExtractor,
    FixtureDocumentExtractor,
    PDFDocumentExtractor,
)
from medbot.errors import ApplicationError
from medbot.fhir import FHIRProvider, HAPIFHIRProvider
from medbot.model_extraction import StructuredAIProvider
from medbot.observability import Observability, configure_tracing
from medbot.schemas import ErrorBody, ErrorResponse
from medbot.service import ReferralService
from medbot.storage import (
    FixtureMalwareScanner,
    InMemoryObjectStorage,
    MalwareScanner,
    ObjectStorage,
    S3ObjectStorage,
)

logger = logging.getLogger(__name__)


class TraceIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[StarletteResponse]],
    ) -> StarletteResponse:
        request.state.trace_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Trace-ID"] = request.state.trace_id
        return response


def create_app(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
    storage: ObjectStorage | None = None,
    scanner: MalwareScanner | None = None,
    extractor: DocumentExtractor | None = None,
    ai_provider: AIProvider | None = None,
    fhir_provider: FHIRProvider | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    resolved_settings.validate()
    resolved_engine = engine or create_database_engine(resolved_settings.database_url)
    session_factory = create_session_factory(resolved_engine)
    resolved_storage = storage or _build_storage(resolved_settings)
    resolved_fhir = fhir_provider or HAPIFHIRProvider(resolved_settings.fhir_base_url)
    observability = Observability()
    configure_tracing(
        os.getenv("OTEL_SERVICE_NAME", "medbot-api"),
        os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"),
    )
    resolved_ai_provider = ai_provider or (
        FixtureAIProvider()
        if resolved_settings.ai_provider == "fixture"
        else StructuredAIProvider(resolved_settings)
    )
    ai_settings_service = AISettingsService(resolved_settings, resolved_ai_provider)
    service = ReferralService(
        storage=resolved_storage,
        scanner=scanner or FixtureMalwareScanner(),
        extractor=extractor
        or (
            PDFDocumentExtractor()
            if resolved_settings.document_extractor == "pdfium-tesseract"
            else FixtureDocumentExtractor()
        ),
        ai_provider=resolved_ai_provider,
        ai_settings=ai_settings_service,
        fhir_provider=resolved_fhir,
        observability=observability,
        max_upload_bytes=resolved_settings.max_upload_bytes,
    )
    authenticator = Authenticator(resolved_settings)

    def get_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app = FastAPI(
        title="MedBot API",
        version="0.1.0",
        description=(
            "Administrative referral workflow API. MedBot does not diagnose, recommend "
            "treatment, or make autonomous clinical decisions."
        ),
    )
    app.state.engine = resolved_engine
    app.state.session_factory = session_factory
    app.state.storage = resolved_storage
    app.state.fhir_provider = resolved_fhir
    app.state.observability = observability
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["X-Trace-ID"],
    )
    app.add_middleware(TraceIDMiddleware)
    app.include_router(
        create_api_router(
            service,
            get_session,
            actor_dependency(authenticator),
            resolved_settings.max_upload_bytes,
            ai_settings_service,
        )
    )

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready() -> Response:
        database_ready = _database_ready(session_factory)
        dependencies_ready = (
            database_ready
            and _dependency_ready(resolved_storage.ready)
            and _dependency_ready(resolved_fhir.ready)
        )
        status_code = 200 if dependencies_ready else 503
        body = b'{"status":"ready"}' if dependencies_ready else b'{"status":"not_ready"}'
        return Response(content=body, status_code=status_code, media_type="application/json")

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(
            content=observability.render_metrics(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.exception_handler(ApplicationError)
    async def application_error_handler(request: Request, error: ApplicationError) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", str(uuid4()))
        response = ErrorResponse(
            error=ErrorBody(code=error.code, message=error.message, trace_id=trace_id)
        )
        headers = {"WWW-Authenticate": "Bearer"} if error.status_code == 401 else None
        return JSONResponse(
            status_code=error.status_code,
            content=response.model_dump(mode="json"),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", str(uuid4()))
        fields = sorted(
            {
                ".".join(str(part) for part in issue["loc"] if part not in {"body", "query"})
                for issue in error.errors()
            }
        )
        response = ErrorResponse(
            error=ErrorBody(
                code="validation_error",
                message="The request did not match the expected schema",
                trace_id=trace_id,
                fields=fields,
            )
        )
        return JSONResponse(status_code=422, content=response.model_dump(mode="json"))

    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        FastAPIInstrumentor.instrument_app(app)
    return app


def _build_storage(settings: Settings) -> ObjectStorage:
    if settings.object_storage == "memory":
        return InMemoryObjectStorage()
    return S3ObjectStorage(
        endpoint_url=settings.s3_endpoint_url,
        access_key_id=settings.s3_access_key_id,
        secret_access_key=settings.s3_secret_access_key,
        bucket=settings.s3_bucket,
        region=settings.s3_region,
    )


def _database_ready(factory: sessionmaker[Session]) -> bool:
    try:
        with factory() as session:
            session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return False
    return True


def _dependency_ready(check: Callable[[], bool]) -> bool:
    try:
        return check()
    except Exception:
        # Readiness endpoints are safe operational signals, not provider error surfaces.
        return False


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(levelname)s %(message)s")
