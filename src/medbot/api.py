from collections.abc import Callable, Generator
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from medbot.ai_settings import (
    AIKeyRemove,
    AISettingsResponse,
    AISettingsSave,
    AISettingsService,
    SettingsRevision,
)
from medbot.auth import (
    AUDIT_ROLES,
    READ_ROLES,
    REVIEW_ROLES,
    WRITE_ROLES,
    require_roles,
)
from medbot.document_processing import render_pdf_page
from medbot.domain import ReferralStatus
from medbot.errors import ApplicationError
from medbot.schemas import (
    ActorContext,
    AuditEventResponse,
    DocumentUploadResponse,
    FHIRBundle,
    FHIRWriteResponse,
    FieldApproveRequest,
    FieldCorrectRequest,
    FieldSupplyRequest,
    ProcessingConfigurationResponse,
    ReferralCreate,
    ReferralDetail,
    ReferralListResponse,
    RouteResponse,
)
from medbot.service import ReferralService

SessionDependency = Callable[[], Generator[Session, None, None]]
ActorDependency = Callable[[Request], ActorContext]


def create_api_router(
    service: ReferralService,
    get_session: SessionDependency,
    get_actor: ActorDependency,
    max_upload_bytes: int,
    ai_settings: AISettingsService,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    can_read = require_roles(get_actor, READ_ROLES)
    can_write = require_roles(get_actor, WRITE_ROLES)
    can_review = require_roles(get_actor, REVIEW_ROLES)
    can_audit = require_roles(get_actor, AUDIT_ROLES)
    DatabaseSession = Annotated[Session, Depends(get_session)]
    ReadActor = Annotated[ActorContext, Depends(can_read)]
    WriteActor = Annotated[ActorContext, Depends(can_write)]
    ReviewActor = Annotated[ActorContext, Depends(can_review)]
    AuditActor = Annotated[ActorContext, Depends(can_audit)]

    @router.get("/auth/me", response_model=ActorContext)
    def auth_me(actor: ReadActor) -> ActorContext:
        return actor

    @router.get("/processing-configuration", response_model=ProcessingConfigurationResponse)
    def get_processing_configuration(
        session: DatabaseSession, actor: ReadActor
    ) -> ProcessingConfigurationResponse:
        return ai_settings.configuration(session, actor)

    @router.get("/settings/ai", response_model=AISettingsResponse)
    def get_ai_settings(session: DatabaseSession, actor: ReadActor) -> AISettingsResponse:
        return ai_settings.read(session, actor)

    @router.post("/settings/ai", response_model=AISettingsResponse)
    def save_ai_settings(
        payload: AISettingsSave, request: Request, session: DatabaseSession, actor: ReadActor
    ) -> AISettingsResponse:
        return ai_settings.save(session, actor, payload, request.state.trace_id)

    @router.post("/settings/ai/remove-key", response_model=AISettingsResponse)
    def remove_ai_key(
        payload: AIKeyRemove, request: Request, session: DatabaseSession, actor: ReadActor
    ) -> AISettingsResponse:
        return ai_settings.remove_key(session, actor, payload, request.state.trace_id)

    @router.post("/settings/ai/use-defaults", response_model=AISettingsResponse)
    def use_default_ai_settings(
        payload: SettingsRevision, request: Request, session: DatabaseSession, actor: ReadActor
    ) -> AISettingsResponse:
        return ai_settings.use_defaults(session, actor, payload, request.state.trace_id)

    @router.post("/referrals", response_model=ReferralDetail, status_code=status.HTTP_201_CREATED)
    def create_referral(
        payload: ReferralCreate,
        request: Request,
        session: DatabaseSession,
        actor: WriteActor,
    ) -> ReferralDetail:
        return service.create_referral(session, actor, payload, request.state.trace_id)

    @router.get("/referrals", response_model=ReferralListResponse)
    def list_referrals(
        session: DatabaseSession,
        actor: ReadActor,
        status_filter: Annotated[ReferralStatus | None, Query(alias="status")] = None,
        routing_queue: Annotated[str | None, Query(max_length=120)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> ReferralListResponse:
        return service.list_referrals(
            session,
            actor,
            status=status_filter,
            routing_queue=routing_queue,
            limit=limit,
            offset=offset,
        )

    @router.get("/referrals/{referral_id}", response_model=ReferralDetail)
    def get_referral(
        referral_id: str,
        session: DatabaseSession,
        actor: ReadActor,
    ) -> ReferralDetail:
        return service.get_referral(session, actor, referral_id)

    @router.post(
        "/referrals/{referral_id}/documents",
        response_model=DocumentUploadResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def upload_document(
        referral_id: str,
        request: Request,
        file: Annotated[UploadFile, File()],
        session: DatabaseSession,
        actor: WriteActor,
        classification: Annotated[str, Form(max_length=80)] = "referral_order",
    ) -> DocumentUploadResponse:
        content = await _read_bounded(file, max_upload_bytes)
        return service.upload_document(
            session,
            actor,
            referral_id,
            filename=file.filename,
            content_type=file.content_type,
            content=content,
            classification=classification,
            trace_id=request.state.trace_id,
        )

    @router.get("/referrals/{referral_id}/documents/{document_id}/content")
    def get_document_content(
        referral_id: str,
        document_id: str,
        session: DatabaseSession,
        actor: ReadActor,
    ) -> Response:
        document, content = service.get_document_content(session, actor, referral_id, document_id)
        return Response(
            content=content,
            media_type=document.detected_mime_type,
            headers={
                "Content-Disposition": f'inline; filename="{document.original_filename}"',
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.get("/referrals/{referral_id}/documents/{document_id}/pages/{page}/image")
    def get_source_page(
        referral_id: str,
        document_id: str,
        page: int,
        session: DatabaseSession,
        actor: ReadActor,
    ) -> Response:
        if not 1 <= page <= 20:
            raise ApplicationError("page_not_found", "The source page does not exist", 404)
        _, content = service.get_document_content(session, actor, referral_id, document_id)
        return Response(
            content=render_pdf_page(content, page),
            media_type="image/png",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    @router.post("/referrals/{referral_id}/process", response_model=ReferralDetail)
    def process_referral(
        referral_id: str,
        request: Request,
        session: DatabaseSession,
        actor: WriteActor,
    ) -> ReferralDetail:
        return service.process_referral(session, actor, referral_id, request.state.trace_id)

    @router.post(
        "/referrals/{referral_id}/fields/{field_id}/approve",
        response_model=ReferralDetail,
    )
    def approve_field(
        referral_id: str,
        field_id: str,
        payload: FieldApproveRequest,
        request: Request,
        session: DatabaseSession,
        actor: ReviewActor,
    ) -> ReferralDetail:
        return service.approve_field(
            session, actor, referral_id, field_id, payload, request.state.trace_id
        )

    @router.post(
        "/referrals/{referral_id}/fields/{field_id}/correct",
        response_model=ReferralDetail,
    )
    def correct_field(
        referral_id: str,
        field_id: str,
        payload: FieldCorrectRequest,
        request: Request,
        session: DatabaseSession,
        actor: ReviewActor,
    ) -> ReferralDetail:
        return service.correct_field(
            session, actor, referral_id, field_id, payload, request.state.trace_id
        )

    @router.post("/referrals/{referral_id}/fields", response_model=ReferralDetail)
    def supply_missing_field(
        referral_id: str,
        payload: FieldSupplyRequest,
        request: Request,
        session: DatabaseSession,
        actor: ReviewActor,
    ) -> ReferralDetail:
        return service.supply_missing_field(
            session, actor, referral_id, payload, request.state.trace_id
        )

    @router.post("/referrals/{referral_id}/request-information", response_model=ReferralDetail)
    def request_information(
        referral_id: str,
        request: Request,
        session: DatabaseSession,
        actor: ReviewActor,
    ) -> ReferralDetail:
        return service.request_information(session, actor, referral_id, request.state.trace_id)

    @router.get(
        "/referrals/{referral_id}/fhir-preview",
        response_model=FHIRBundle,
        response_model_exclude_none=True,
    )
    def fhir_preview(
        referral_id: str,
        session: DatabaseSession,
        actor: ReadActor,
    ) -> FHIRBundle:
        return service.fhir_preview(session, actor, referral_id)

    @router.post("/referrals/{referral_id}/fhir", response_model=FHIRWriteResponse)
    def write_fhir(
        referral_id: str,
        request: Request,
        session: DatabaseSession,
        actor: ReviewActor,
    ) -> FHIRWriteResponse:
        return service.write_fhir(session, actor, referral_id, request.state.trace_id)

    @router.post("/referrals/{referral_id}/route", response_model=RouteResponse)
    def route_referral(
        referral_id: str,
        request: Request,
        session: DatabaseSession,
        actor: ReviewActor,
    ) -> RouteResponse:
        return service.route_referral(session, actor, referral_id, request.state.trace_id)

    @router.get("/referrals/{referral_id}/audit", response_model=list[AuditEventResponse])
    def audit_history(
        referral_id: str,
        session: DatabaseSession,
        actor: AuditActor,
    ) -> list[AuditEventResponse]:
        return service.audit_history(session, actor, referral_id)

    return router


async def _read_bounded(upload: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(64 * 1024):
        total += len(chunk)
        if total > max_bytes:
            raise ApplicationError("upload_too_large", "The PDF exceeds the upload limit", 413)
        chunks.append(chunk)
    return b"".join(chunks)
