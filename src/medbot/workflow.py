from __future__ import annotations

from datetime import UTC, datetime

from medbot.domain import (
    EvidenceStatus,
    FieldReviewStatus,
    ReferralStatus,
    ensure_transition,
)
from medbot.errors import ApplicationError, ConflictError
from medbot.models import ExtractedField, Referral
from medbot.policy import is_valid_field_value
from medbot.referral_validation import has_review_blockers, refresh_completeness
from medbot.schemas import ActorContext, FieldApproveRequest, FieldCorrectRequest


def transition_referral(referral: Referral, target: ReferralStatus) -> None:
    ensure_transition(ReferralStatus(referral.status), target)
    referral.status = target
    referral.updated_at = datetime.now(UTC)


def approve_extracted_field(
    referral: Referral,
    field: ExtractedField,
    actor: ActorContext,
    request: FieldApproveRequest,
) -> None:
    ensure_reviewable(referral)
    if EvidenceStatus(field.evidence_status) not in {
        EvidenceStatus.VERIFIED,
        EvidenceStatus.LOW_CONFIDENCE,
        EvidenceStatus.REVIEW_REQUIRED,
    }:
        raise ConflictError("field_not_approvable", "Only source-supported fields can be approved")
    field.review_status = FieldReviewStatus.APPROVED
    field.evidence_status = EvidenceStatus.VERIFIED
    _record_review(field, actor, request.reason)
    if is_valid_field_value(field.path, current_field_value(field)):
        resolve_field_issues(referral, field.path)
    reevaluate_after_review(referral)


def correct_extracted_field(
    referral: Referral,
    field: ExtractedField,
    actor: ActorContext,
    request: FieldCorrectRequest,
) -> None:
    ensure_reviewable(referral)
    if not is_valid_field_value(field.path, request.value):
        raise ApplicationError("invalid_field_value", "The supplied field value is invalid", 422)
    if field.evidence_status == EvidenceStatus.CONFLICTING and not request.reason:
        raise ApplicationError(
            "review_reason_required", "Explain how the conflicting sources were resolved", 422
        )
    field.human_value = request.value
    field.review_status = FieldReviewStatus.CORRECTED
    field.evidence_status = EvidenceStatus.HUMAN_CORRECTED
    _record_review(field, actor, request.reason)
    if field.path == "requested_service":
        referral.requested_service = request.value
    if is_valid_field_value(field.path, current_field_value(field)):
        resolve_field_issues(referral, field.path)
    reevaluate_after_review(referral)


def reevaluate_after_review(referral: Referral) -> None:
    result = refresh_completeness(referral)
    ready = result.complete and not has_review_blockers(referral)
    status = ReferralStatus(referral.status)
    if ready and status in {ReferralStatus.NEEDS_REVIEW, ReferralStatus.NEEDS_INFORMATION}:
        transition_referral(referral, ReferralStatus.READY_TO_ROUTE)
    elif not ready and status == ReferralStatus.READY_TO_ROUTE:
        transition_referral(referral, ReferralStatus.NEEDS_REVIEW)


def ensure_reviewable(referral: Referral) -> None:
    if ReferralStatus(referral.status) not in {
        ReferralStatus.NEEDS_REVIEW,
        ReferralStatus.NEEDS_INFORMATION,
        ReferralStatus.READY_TO_ROUTE,
    }:
        raise ConflictError(
            "field_review_not_allowed",
            "Fields cannot be reviewed in the referral's current state",
        )


def resolve_field_issues(referral: Referral, field_path: str) -> None:
    for issue in referral.validation_issues:
        if issue.field_path == field_path:
            issue.resolved = True


def field_by_path(referral: Referral, path: str) -> ExtractedField | None:
    return next((field for field in referral.extracted_fields if field.path == path), None)


def current_field_value(field: ExtractedField) -> str:
    return field.human_value if field.human_value is not None else (field.ai_value or "")


def _record_review(field: ExtractedField, actor: ActorContext, reason: str | None) -> None:
    field.reviewer_id = actor.user_id
    field.reviewed_at = datetime.now(UTC)
    field.review_reason = reason
