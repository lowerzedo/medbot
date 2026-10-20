from __future__ import annotations

import hashlib
import logging
import re
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from medbot.ai import AIProvider
from medbot.ai_settings import AISettingsService
from medbot.audit import append_audit_record, decode_metadata
from medbot.document_processing import DocumentExtractor, sha256_hex, validate_pdf
from medbot.domain import (
    EvidenceStatus,
    FieldReviewStatus,
    InvalidTransitionError,
    ReferralStatus,
    ensure_transition,
)
from medbot.errors import ApplicationError, ConflictError, NotFoundError
from medbot.fhir import FHIRProvider, FHIRWriteResult, build_fhir_bundle
from medbot.models import (
    AuditRecord,
    Document,
    ExtractedField,
    Referral,
    RoutingDecision,
)
from medbot.observability import Observability
from medbot.policy import is_valid_field_value, policy_for, route_for
from medbot.presentation import (
    as_utc,
    document_response,
    latest_routing_decision,
    referral_detail,
    referral_summary,
)
from medbot.processing import ReferralProcessor
from medbot.referral_validation import (
    has_review_blockers,
    pending_review_paths,
    refresh_completeness,
)
from medbot.schemas import (
    ActorContext,
    AuditEventResponse,
    DocumentUploadResponse,
    FHIRBundle,
    FHIRWriteResponse,
    FieldApproveRequest,
    FieldCorrectRequest,
    FieldSupplyRequest,
    ReferralCreate,
    ReferralDetail,
    ReferralListResponse,
    RouteResponse,
)
from medbot.storage import (
    MalwareScanner,
    ObjectIntegrityError,
    ObjectStorage,
    ObjectStorageError,
    ObjectStorageUnavailableError,
    get_verified_object,
)
from medbot.workflow import (
    approve_extracted_field,
    correct_extracted_field,
    ensure_reviewable,
    field_by_path,
    transition_referral,
)

logger = logging.getLogger(__name__)
CLASSIFICATION_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,79}")


class ReferralService:
    """Application-level transaction and authorization-scoping coordinator."""

    def __init__(
        self,
        *,
        storage: ObjectStorage,
        scanner: MalwareScanner,
        extractor: DocumentExtractor,
        ai_provider: AIProvider,
        fhir_provider: FHIRProvider,
        observability: Observability,
        max_upload_bytes: int,
        ai_settings: AISettingsService | None = None,
    ) -> None:
        self._storage = storage
        self._scanner = scanner
        self._fhir_provider = fhir_provider
        self._observability = observability
        self._max_upload_bytes = max_upload_bytes
        self._ai_settings = ai_settings
        self._extractor = extractor
        self._processor = ReferralProcessor(
            storage=storage,
            extractor=extractor,
            ai_provider=ai_provider,
            observability=observability,
        )

    def create_referral(
        self,
        session: Session,
        actor: ActorContext,
        request: ReferralCreate,
        trace_id: str,
    ) -> ReferralDetail:
        referral = Referral(
            id=str(uuid4()),
            organization_id=actor.organization_id,
            created_by=actor.user_id,
            specialty=request.specialty,
            status=ReferralStatus.RECEIVED,
        )
        session.add(referral)
        session.flush()
        append_audit_record(
            session,
            referral=referral,
            actor_id=actor.user_id,
            action="REFERRAL_CREATED",
            resource_type="Referral",
            resource_id=referral.id,
            trace_id=trace_id,
            metadata={"specialty": referral.specialty},
        )
        session.commit()
        self._observability.referrals_received.inc()
        logger.info(
            "referral_created",
            extra={
                "referral_id": referral.id,
                "organization_id": referral.organization_id,
                "trace_id": trace_id,
            },
        )
        return referral_detail(session, self._load_referral(session, actor, referral.id))

    def list_referrals(
        self,
        session: Session,
        actor: ActorContext,
        *,
        status: ReferralStatus | None,
        routing_queue: str | None,
        limit: int,
        offset: int,
    ) -> ReferralListResponse:
        filters = [Referral.organization_id == actor.organization_id]
        if status is not None:
            filters.append(Referral.status == status)
        if routing_queue is not None:
            filters.append(Referral.routing_queue == routing_queue)
        total = session.scalar(select(func.count(Referral.id)).where(*filters)) or 0
        referrals = session.scalars(
            select(Referral)
            .where(*filters)
            .order_by(Referral.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return ReferralListResponse(
            items=[referral_summary(referral) for referral in referrals], total=total
        )

    def get_referral(
        self, session: Session, actor: ActorContext, referral_id: str
    ) -> ReferralDetail:
        return referral_detail(session, self._load_referral(session, actor, referral_id))

    def upload_document(
        self,
        session: Session,
        actor: ActorContext,
        referral_id: str,
        *,
        filename: str | None,
        content_type: str | None,
        content: bytes,
        classification: str,
        trace_id: str,
    ) -> DocumentUploadResponse:
        referral = self._load_referral(session, actor, referral_id, for_update=True)
        if ReferralStatus(referral.status) not in {
            ReferralStatus.RECEIVED,
            ReferralStatus.NEEDS_REVIEW,
            ReferralStatus.NEEDS_INFORMATION,
        }:
            raise ConflictError(
                "document_upload_not_allowed",
                "Documents cannot be uploaded in the referral's current state",
            )
        if len(content) > self._max_upload_bytes:
            raise ApplicationError("upload_too_large", "The PDF exceeds the upload limit", 413)
        if not CLASSIFICATION_PATTERN.fullmatch(classification):
            raise ApplicationError(
                "invalid_document_classification", "Document classification is invalid", 422
            )
        safe_filename = validate_pdf(filename, content_type, content)
        try:
            content_is_safe = self._scanner.is_safe(content)
        except Exception as error:
            logger.warning(
                "document_scanner_failed",
                extra={
                    "referral_id": referral.id,
                    "organization_id": referral.organization_id,
                    "trace_id": trace_id,
                    "error_type": type(error).__name__,
                },
            )
            raise ApplicationError(
                "document_scanner_unavailable",
                "Document security scanning is temporarily unavailable",
                503,
            ) from None
        if not content_is_safe:
            transition_referral(referral, ReferralStatus.QUARANTINED)
            append_audit_record(
                session,
                referral=referral,
                actor_id=actor.user_id,
                action="DOCUMENT_QUARANTINED",
                resource_type="Referral",
                resource_id=referral.id,
                trace_id=trace_id,
            )
            session.commit()
            raise ApplicationError("unsafe_document", "The PDF failed security checks", 422)
        digest = sha256_hex(content)
        duplicate = session.scalar(
            select(Document).where(
                Document.referral_id == referral.id,
                Document.organization_id == actor.organization_id,
                Document.sha256 == digest,
            )
        )
        if duplicate is not None:
            append_audit_record(
                session,
                referral=referral,
                actor_id=actor.user_id,
                action="DOCUMENT_DUPLICATE_DETECTED",
                resource_type="Document",
                resource_id=duplicate.id,
                trace_id=trace_id,
            )
            session.commit()
            return DocumentUploadResponse(document=document_response(duplicate), duplicate=True)

        document_id = str(uuid4())
        storage_key = f"{actor.organization_id}/{referral.id}/{document_id}.pdf"
        try:
            self._storage.put(storage_key, content, "application/pdf")
        except ObjectStorageError:
            raise ApplicationError(
                "object_storage_unavailable",
                "Document storage is temporarily unavailable",
                503,
            ) from None
        previous_versions = [
            document.version
            for document in referral.documents
            if document.classification == classification
        ]
        document = Document(
            id=document_id,
            referral_id=referral.id,
            organization_id=actor.organization_id,
            original_filename=safe_filename,
            detected_mime_type="application/pdf",
            sha256=digest,
            fhir_sha1=hashlib.sha1(content, usedforsecurity=False).hexdigest(),
            size_bytes=len(content),
            storage_key=storage_key,
            processing_status="STORED",
            classification=classification,
            version=max(previous_versions, default=0) + 1,
            uploaded_by=actor.user_id,
        )
        referral.documents.append(document)
        session.flush()
        append_audit_record(
            session,
            referral=referral,
            actor_id=actor.user_id,
            action="DOCUMENT_UPLOADED",
            resource_type="Document",
            resource_id=document.id,
            trace_id=trace_id,
            metadata={
                "classification": classification,
                "sha256": digest,
                "size_bytes": str(len(content)),
            },
        )
        session.commit()
        logger.info(
            "document_uploaded",
            extra={
                "referral_id": referral.id,
                "document_id": document.id,
                "organization_id": referral.organization_id,
                "trace_id": trace_id,
            },
        )
        return DocumentUploadResponse(document=document_response(document), duplicate=False)

    def get_document_content(
        self,
        session: Session,
        actor: ActorContext,
        referral_id: str,
        document_id: str,
    ) -> tuple[Document, bytes]:
        document = session.scalar(
            select(Document)
            .join(Referral)
            .where(
                Document.id == document_id,
                Document.referral_id == referral_id,
                Document.organization_id == actor.organization_id,
                Referral.organization_id == actor.organization_id,
            )
        )
        if document is None:
            raise NotFoundError("document")
        try:
            content = get_verified_object(
                self._storage,
                document.storage_key,
                expected_sha256=document.sha256,
                expected_size=document.size_bytes,
            )
        except ObjectIntegrityError:
            raise ApplicationError(
                "document_integrity_failed",
                "Stored document content failed its integrity check",
                409,
            ) from None
        except ObjectStorageUnavailableError:
            raise ApplicationError(
                "document_content_unavailable", "Document content is unavailable", 503
            ) from None
        return document, content

    def process_referral(
        self,
        session: Session,
        actor: ActorContext,
        referral_id: str,
        trace_id: str,
    ) -> ReferralDetail:
        referral = self._load_referral(session, actor, referral_id, for_update=True)
        processor = self._processor
        if self._ai_settings is not None:
            processor = ReferralProcessor(
                storage=self._storage,
                extractor=self._extractor,
                ai_provider=self._ai_settings.provider_for(session, actor),
                observability=self._observability,
            )
        try:
            target = processor.process(session, referral, actor.user_id, trace_id)
        except InvalidTransitionError:
            session.rollback()
            raise ConflictError(
                "invalid_state_transition", "Referral cannot be processed from its current state"
            ) from None
        except (
            ApplicationError,
            ValidationError,
            ValueError,
            KeyError,
            ObjectStorageError,
        ) as error:
            if ReferralStatus(referral.status) == ReferralStatus.PROCESSING:
                self._processor.fail(session, referral, actor.user_id, trace_id)
                session.commit()
            raise self._processor.normalize_error(error) from None
        session.commit()
        self._observability.referrals_processed.inc()
        if target == ReferralStatus.NEEDS_REVIEW:
            self._observability.referrals_review_required.inc()
        logger.info(
            "referral_processed",
            extra={
                "referral_id": referral.id,
                "organization_id": referral.organization_id,
                "workflow_step": target,
                "trace_id": trace_id,
            },
        )
        return referral_detail(session, self._load_referral(session, actor, referral.id))

    def approve_field(
        self,
        session: Session,
        actor: ActorContext,
        referral_id: str,
        field_id: str,
        request: FieldApproveRequest,
        trace_id: str,
    ) -> ReferralDetail:
        referral, field = self._load_field(session, actor, referral_id, field_id, for_update=True)
        previous_status = ReferralStatus(referral.status)
        approve_extracted_field(referral, field, actor, request)
        append_audit_record(
            session,
            referral=referral,
            actor_id=actor.user_id,
            action="FIELD_APPROVED",
            resource_type="ExtractedField",
            resource_id=field.id,
            trace_id=trace_id,
            metadata={"field_name": field.path},
        )
        self._audit_status_change(
            session,
            referral=referral,
            previous_status=previous_status,
            trace_id=trace_id,
        )
        session.commit()
        return referral_detail(session, self._load_referral(session, actor, referral.id))

    def correct_field(
        self,
        session: Session,
        actor: ActorContext,
        referral_id: str,
        field_id: str,
        request: FieldCorrectRequest,
        trace_id: str,
    ) -> ReferralDetail:
        referral, field = self._load_field(session, actor, referral_id, field_id, for_update=True)
        previous_status = ReferralStatus(referral.status)
        correct_extracted_field(referral, field, actor, request)
        append_audit_record(
            session,
            referral=referral,
            actor_id=actor.user_id,
            action="FIELD_CORRECTED",
            resource_type="ExtractedField",
            resource_id=field.id,
            trace_id=trace_id,
            metadata={"field_name": field.path},
        )
        self._audit_status_change(
            session,
            referral=referral,
            previous_status=previous_status,
            trace_id=trace_id,
        )
        session.commit()
        return referral_detail(session, self._load_referral(session, actor, referral.id))

    def supply_missing_field(
        self,
        session: Session,
        actor: ActorContext,
        referral_id: str,
        request: FieldSupplyRequest,
        trace_id: str,
    ) -> ReferralDetail:
        referral = self._load_referral(session, actor, referral_id, for_update=True)
        field = field_by_path(referral, request.field_path)
        if field is not None:
            if (
                field.ai_value is None
                and field.human_value == request.value
                and field.review_reason == request.reason
                and field.reviewer_id == actor.user_id
            ):
                return referral_detail(session, referral)
            raise ConflictError(
                "field_already_exists", "Use the field correction action for an existing field"
            )
        ensure_reviewable(referral)
        if request.field_path not in policy_for(referral.specialty).required_fields:
            raise ApplicationError(
                "field_not_required", "Only a missing required field can be supplied", 422
            )
        if not is_valid_field_value(request.field_path, request.value):
            raise ApplicationError(
                "invalid_field_value", "The supplied field value is invalid", 422
            )
        previous_status = ReferralStatus(referral.status)
        field = ExtractedField(
            id=str(uuid4()),
            referral_id=referral.id,
            path=request.field_path,
            ai_value=None,
            confidence=0.0,
            evidence_status=EvidenceStatus.HUMAN_CORRECTED,
            review_status=FieldReviewStatus.PENDING,
        )
        referral.extracted_fields.append(field)
        correct_extracted_field(
            referral, field, actor, FieldCorrectRequest(value=request.value, reason=request.reason)
        )
        session.flush()
        append_audit_record(
            session,
            referral=referral,
            actor_id=actor.user_id,
            action="FIELD_SUPPLIED",
            resource_type="ExtractedField",
            resource_id=field.id,
            trace_id=trace_id,
            metadata={"field_name": field.path, "value_origin": "reviewer"},
        )
        self._audit_status_change(
            session, referral=referral, previous_status=previous_status, trace_id=trace_id
        )
        session.commit()
        return referral_detail(session, self._load_referral(session, actor, referral.id))

    def request_information(
        self,
        session: Session,
        actor: ActorContext,
        referral_id: str,
        trace_id: str,
    ) -> ReferralDetail:
        referral = self._load_referral(session, actor, referral_id, for_update=True)
        if ReferralStatus(referral.status) == ReferralStatus.NEEDS_INFORMATION:
            return referral_detail(session, referral)
        ensure_reviewable(referral)
        missing = refresh_completeness(referral)
        missing_fields = sorted(set(missing.missing_fields) - pending_review_paths(referral))
        if not missing_fields and not missing.missing_documents:
            raise ConflictError("no_missing_information", "No required information is missing")
        previous_status = ReferralStatus(referral.status)
        transition_referral(referral, ReferralStatus.NEEDS_INFORMATION)
        append_audit_record(
            session,
            referral=referral,
            actor_id=actor.user_id,
            action="INFORMATION_REQUESTED",
            resource_type="Referral",
            resource_id=referral.id,
            trace_id=trace_id,
            metadata={
                "missing_fields": ",".join(missing_fields),
                "missing_documents": ",".join(missing.missing_documents),
            },
        )
        self._audit_status_change(
            session, referral=referral, previous_status=previous_status, trace_id=trace_id
        )
        session.commit()
        return referral_detail(session, self._load_referral(session, actor, referral.id))

    def fhir_preview(self, session: Session, actor: ActorContext, referral_id: str) -> FHIRBundle:
        return build_fhir_bundle(self._load_referral(session, actor, referral_id))

    def write_fhir(
        self,
        session: Session,
        actor: ActorContext,
        referral_id: str,
        trace_id: str,
    ) -> FHIRWriteResponse:
        referral = self._load_referral(session, actor, referral_id, for_update=True)
        if ReferralStatus(referral.status) not in {
            ReferralStatus.READY_TO_ROUTE,
            ReferralStatus.ROUTED,
        }:
            raise ConflictError(
                "fhir_write_not_allowed", "FHIR can only be written for a validated referral"
            )
        if not refresh_completeness(referral).complete or has_review_blockers(referral):
            raise ConflictError(
                "referral_not_validated", "Resolve referral validation issues before writing FHIR"
            )
        result = self._persist_fhir(referral, trace_id)
        self._audit_fhir_write(session, referral, actor.user_id, trace_id, result)
        session.commit()
        return FHIRWriteResponse(
            referral_id=referral.id,
            resource_ids=list(result.resource_ids),
            idempotent=result.idempotent,
        )

    def route_referral(
        self,
        session: Session,
        actor: ActorContext,
        referral_id: str,
        trace_id: str,
    ) -> RouteResponse:
        referral = self._load_referral(session, actor, referral_id, for_update=True)
        if ReferralStatus(referral.status) == ReferralStatus.ROUTED:
            existing = latest_routing_decision(session, referral.id)
            if existing is None:
                raise ConflictError("routing_record_missing", "Routing record is unavailable")
            return RouteResponse(
                referral_id=referral.id,
                status=ReferralStatus.ROUTED,
                queue=existing.resulting_queue,
                rule_version=existing.rule_version,
                fhir_resource_count=len(build_fhir_bundle(referral).entry),
            )
        try:
            ensure_transition(ReferralStatus(referral.status), ReferralStatus.ROUTED)
        except InvalidTransitionError:
            raise ConflictError(
                "invalid_state_transition", "Referral is not ready to route"
            ) from None
        # Persist first; deterministic PUTs make a retry safe if the database commit later fails.
        if not refresh_completeness(referral).complete or has_review_blockers(referral):
            raise ConflictError(
                "referral_not_validated", "Resolve referral validation issues before routing"
            )
        fhir_result = self._persist_fhir(referral, trace_id)
        self._audit_fhir_write(session, referral, actor.user_id, trace_id, fhir_result)
        queue, rule_version = route_for(referral.specialty)
        referral.routing_queue = queue
        transition_referral(referral, ReferralStatus.ROUTED)
        decision = RoutingDecision(
            id=str(uuid4()),
            referral_id=referral.id,
            organization_id=referral.organization_id,
            rule_version=rule_version,
            input_specialty=referral.specialty,
            resulting_queue=queue,
            actor_id=actor.user_id,
            trace_id=trace_id,
        )
        session.add(decision)
        append_audit_record(
            session,
            referral=referral,
            actor_id=actor.user_id,
            action="REFERRAL_ROUTED",
            resource_type="Referral",
            resource_id=referral.id,
            trace_id=trace_id,
            metadata={"queue": queue, "rule_version": rule_version},
        )
        append_audit_record(
            session,
            referral=referral,
            actor_id="medbot-workflow",
            action="STATUS_CHANGED",
            resource_type="Referral",
            resource_id=referral.id,
            trace_id=trace_id,
            metadata={"status": ReferralStatus.ROUTED},
        )
        session.commit()
        return RouteResponse(
            referral_id=referral.id,
            status=ReferralStatus.ROUTED,
            queue=queue,
            rule_version=rule_version,
            fhir_resource_count=len(fhir_result.resource_ids),
        )

    def audit_history(
        self, session: Session, actor: ActorContext, referral_id: str
    ) -> list[AuditEventResponse]:
        self._load_referral(session, actor, referral_id)
        records = session.scalars(
            select(AuditRecord)
            .where(
                AuditRecord.referral_id == referral_id,
                AuditRecord.organization_id == actor.organization_id,
            )
            .order_by(AuditRecord.sequence)
        ).all()
        return [
            AuditEventResponse(
                id=record.id,
                sequence=record.sequence,
                timestamp=as_utc(record.timestamp),
                organization_id=record.organization_id,
                referral_id=record.referral_id,
                actor_id=record.actor_id,
                action=record.action,
                resource_type=record.resource_type,
                resource_id=record.resource_id,
                trace_id=record.trace_id,
                metadata=decode_metadata(record.metadata_json),
                previous_event_hash=record.previous_event_hash,
                event_hash=record.event_hash,
            )
            for record in records
        ]

    def _load_field(
        self,
        session: Session,
        actor: ActorContext,
        referral_id: str,
        field_id: str,
        *,
        for_update: bool = False,
    ) -> tuple[Referral, ExtractedField]:
        referral = self._load_referral(session, actor, referral_id, for_update=for_update)
        field = next((item for item in referral.extracted_fields if item.id == field_id), None)
        if field is None:
            raise NotFoundError("field")
        return referral, field

    def _persist_fhir(self, referral: Referral, trace_id: str) -> FHIRWriteResult:
        bundle = build_fhir_bundle(referral)
        with self._observability.tracer.start_as_current_span("fhir.persistence") as span:
            span.set_attribute("referral.id", referral.id)
            span.set_attribute("organization.id", referral.organization_id)
            try:
                return self._fhir_provider.write(bundle)
            except Exception as error:
                # Replace provider details so responses, logs, and traces never expose payload data.
                logger.warning(
                    "fhir_write_failed",
                    extra={
                        "referral_id": referral.id,
                        "organization_id": referral.organization_id,
                        "trace_id": trace_id,
                        "error_type": type(error).__name__,
                    },
                )
                raise ApplicationError(
                    "fhir_unavailable", "The FHIR service is temporarily unavailable", 503
                ) from None

    @staticmethod
    def _audit_fhir_write(
        session: Session,
        referral: Referral,
        actor_id: str,
        trace_id: str,
        result: FHIRWriteResult,
    ) -> None:
        append_audit_record(
            session,
            referral=referral,
            actor_id=actor_id,
            action="FHIR_WRITTEN",
            resource_type="Referral",
            resource_id=referral.id,
            trace_id=trace_id,
            metadata={"resource_count": str(len(result.resource_ids))},
        )

    @staticmethod
    def _audit_status_change(
        session: Session,
        *,
        referral: Referral,
        previous_status: ReferralStatus,
        trace_id: str,
    ) -> None:
        current_status = ReferralStatus(referral.status)
        if current_status == previous_status:
            return
        append_audit_record(
            session,
            referral=referral,
            actor_id="medbot-workflow",
            action="STATUS_CHANGED",
            resource_type="Referral",
            resource_id=referral.id,
            trace_id=trace_id,
            metadata={"status": current_status},
        )

    @staticmethod
    def _load_referral(
        session: Session,
        actor: ActorContext,
        referral_id: str,
        *,
        for_update: bool = False,
    ) -> Referral:
        statement = (
            select(Referral)
            .where(
                Referral.id == referral_id,
                Referral.organization_id == actor.organization_id,
            )
            .options(
                selectinload(Referral.documents).selectinload(Document.pages),
                selectinload(Referral.documents).selectinload(Document.text_blocks),
                selectinload(Referral.extracted_fields).selectinload(ExtractedField.evidence),
                selectinload(Referral.validation_issues),
            )
        )
        if for_update:
            statement = statement.with_for_update()
        referral = session.scalar(statement)
        if referral is None:
            raise NotFoundError("referral")
        return referral
