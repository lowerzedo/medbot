from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from medbot.document_processing import SourceRegion
from medbot.domain import (
    DocumentProcessingStatus,
    EvidenceStatus,
    FieldReviewStatus,
    IssueSeverity,
    ReferralStatus,
)
from medbot.models import Document, ExtractedField, Referral, RoutingDecision
from medbot.referral_validation import pending_review_paths
from medbot.schemas import (
    DocumentResponse,
    EvidenceReferenceResponse,
    ExtractedFieldResponse,
    ReferralDetail,
    ReferralSummary,
    ReferralValidationResponse,
    RoutingDecisionResponse,
    ValidationIssueResponse,
)


def referral_summary(referral: Referral) -> ReferralSummary:
    return ReferralSummary(
        id=referral.id,
        status=ReferralStatus(referral.status),
        requested_service=referral.requested_service,
        routing_queue=referral.routing_queue,
        complete=referral.complete,
        created_at=as_utc(referral.created_at),
        updated_at=as_utc(referral.updated_at),
    )


def referral_detail(session: Session, referral: Referral) -> ReferralDetail:
    summary = referral_summary(referral)
    pending = pending_review_paths(referral)
    missing_fields = sorted(set(_unresolved_issue_paths(referral, "missing_field")) - pending)
    missing_documents = _unresolved_issue_paths(referral, "missing_document")
    warnings = sorted(
        {
            issue.code
            for issue in referral.validation_issues
            if not issue.resolved and issue.severity == IssueSeverity.WARNING
        }
    )
    decision = latest_routing_decision(session, referral.id)
    return ReferralDetail(
        **summary.model_dump(),
        documents=[document_response(document) for document in referral.documents],
        fields=[field_response(field) for field in referral.extracted_fields],
        validation=ReferralValidationResponse(
            complete=referral.complete,
            missing_fields=missing_fields,
            pending_review_fields=sorted(pending),
            missing_documents=missing_documents,
            warnings=warnings,
        ),
        validation_issues=[
            ValidationIssueResponse(
                id=issue.id,
                code=issue.code,
                severity=IssueSeverity(issue.severity),
                message=issue.message,
                field_path=issue.field_path,
                resolved=issue.resolved,
            )
            for issue in referral.validation_issues
            if not (issue.code == "missing_field" and issue.field_path in pending)
        ],
        routing_decision=(
            None
            if decision is None
            else RoutingDecisionResponse(
                queue=decision.resulting_queue,
                rule_version=decision.rule_version,
                created_at=as_utc(decision.created_at),
            )
        ),
    )


def document_response(document: Document) -> DocumentResponse:
    return DocumentResponse(
        id=document.id,
        original_filename=document.original_filename,
        media_type=document.detected_mime_type,
        classification=document.classification,
        processing_state=DocumentProcessingStatus(document.processing_status),
        page_count=len(document.pages),
        created_at=as_utc(document.uploaded_at),
    )


def field_response(field: ExtractedField) -> ExtractedFieldResponse:
    return ExtractedFieldResponse(
        id=field.id,
        field_name=field.path,
        ai_value=field.ai_value,
        reviewed_value=field.human_value,
        confidence=field.confidence,
        evidence_status=EvidenceStatus(field.evidence_status),
        review_status=FieldReviewStatus(field.review_status),
        reviewer_id=field.reviewer_id,
        reviewed_at=as_utc(field.reviewed_at) if field.reviewed_at is not None else None,
        review_reason=field.review_reason,
        evidence=[
            EvidenceReferenceResponse(
                document_id=reference.document_id,
                page=reference.page_number,
                block_id=reference.block_id,
                quote=reference.quote,
                verified=reference.status == EvidenceStatus.VERIFIED,
                extraction_method=reference.block.extraction_method,
                ocr_confidence=reference.block.ocr_confidence,
                region=(
                    SourceRegion(
                        x=reference.block.region_x,
                        y=reference.block.region_y,
                        width=reference.block.region_width,
                        height=reference.block.region_height,
                    )
                    if reference.block.region_x is not None
                    and reference.block.region_y is not None
                    and reference.block.region_width is not None
                    and reference.block.region_height is not None
                    else None
                ),
            )
            for reference in field.evidence
        ],
    )


def latest_routing_decision(session: Session, referral_id: str) -> RoutingDecision | None:
    return session.scalar(
        select(RoutingDecision)
        .where(RoutingDecision.referral_id == referral_id)
        .order_by(RoutingDecision.created_at.desc())
        .limit(1)
    )


def _unresolved_issue_paths(referral: Referral, code: str) -> list[str]:
    return sorted(
        {
            issue.field_path
            for issue in referral.validation_issues
            if not issue.resolved and issue.code == code and issue.field_path is not None
        }
    )


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
