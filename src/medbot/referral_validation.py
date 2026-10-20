from __future__ import annotations

from uuid import uuid4

from medbot.domain import DocumentProcessingStatus, EvidenceStatus, IssueSeverity
from medbot.models import Referral, ValidationIssue
from medbot.policy import evaluate_completeness
from medbot.schemas import CompletenessResult

TRUSTED_FIELD_STATUSES = {EvidenceStatus.VERIFIED, EvidenceStatus.HUMAN_CORRECTED}


def pending_review_paths(referral: Referral) -> set[str]:
    return {
        field.path
        for field in referral.extracted_fields
        if field.evidence_status in {EvidenceStatus.REVIEW_REQUIRED, EvidenceStatus.LOW_CONFIDENCE}
    }


def current_field_values(referral: Referral) -> dict[str, str]:
    return {
        field.path: value
        for field in referral.extracted_fields
        if field.evidence_status in TRUSTED_FIELD_STATUSES
        and (value := field.human_value if field.human_value is not None else field.ai_value)
    }


def refresh_completeness(referral: Referral) -> CompletenessResult:
    result = evaluate_completeness(
        referral.specialty,
        current_field_values(referral),
        {
            document.classification
            for document in referral.documents
            if document.processing_status == DocumentProcessingStatus.EXTRACTED
        },
    )
    referral.complete = result.complete
    referral.validation_issues[:] = [
        issue
        for issue in referral.validation_issues
        if issue.code not in {"missing_field", "missing_document"}
    ]
    for code, paths, message in (
        ("missing_field", result.missing_fields, "A required referral field is missing"),
        ("missing_document", result.missing_documents, "A required referral document is missing"),
    ):
        for path in paths:
            referral.validation_issues.append(
                ValidationIssue(
                    id=str(uuid4()),
                    referral_id=referral.id,
                    code=code,
                    severity=IssueSeverity.ERROR,
                    message=message,
                    field_path=path,
                    resolved=False,
                )
            )
    return result


def has_review_blockers(referral: Referral) -> bool:
    return (
        any(
            not issue.resolved and issue.severity == IssueSeverity.ERROR
            for issue in referral.validation_issues
        )
        or any(
            field.evidence_status not in TRUSTED_FIELD_STATUSES
            for field in referral.extracted_fields
        )
        or any(
            document.processing_status != DocumentProcessingStatus.EXTRACTED
            for document in referral.documents
        )
    )
