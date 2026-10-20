from __future__ import annotations

from enum import StrEnum


class ReferralStatus(StrEnum):
    RECEIVED = "RECEIVED"
    QUARANTINED = "QUARANTINED"
    PROCESSING = "PROCESSING"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NEEDS_INFORMATION = "NEEDS_INFORMATION"
    READY_TO_ROUTE = "READY_TO_ROUTE"
    ROUTED = "ROUTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class DocumentProcessingStatus(StrEnum):
    STORED = "STORED"
    EXTRACTED = "EXTRACTED"
    FAILED = "FAILED"


class EvidenceStatus(StrEnum):
    VERIFIED = "VERIFIED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    UNSUPPORTED = "UNSUPPORTED"
    CONFLICTING = "CONFLICTING"
    HUMAN_CORRECTED = "HUMAN_CORRECTED"


class FieldReviewStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    CORRECTED = "CORRECTED"
    REJECTED = "REJECTED"


class IssueSeverity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


class Role(StrEnum):
    INTAKE_SPECIALIST = "intake_specialist"
    REVIEWER = "reviewer"
    SUPERVISOR = "supervisor"
    AUDITOR = "auditor"
    ADMINISTRATOR = "administrator"
    SERVICE = "service"


ALLOWED_TRANSITIONS: dict[ReferralStatus, frozenset[ReferralStatus]] = {
    ReferralStatus.RECEIVED: frozenset({ReferralStatus.PROCESSING, ReferralStatus.QUARANTINED}),
    ReferralStatus.QUARANTINED: frozenset({ReferralStatus.RECEIVED, ReferralStatus.FAILED}),
    ReferralStatus.PROCESSING: frozenset(
        {
            ReferralStatus.NEEDS_REVIEW,
            ReferralStatus.NEEDS_INFORMATION,
            ReferralStatus.READY_TO_ROUTE,
            ReferralStatus.FAILED,
        }
    ),
    ReferralStatus.NEEDS_REVIEW: frozenset(
        {
            ReferralStatus.PROCESSING,
            ReferralStatus.NEEDS_INFORMATION,
            ReferralStatus.READY_TO_ROUTE,
            ReferralStatus.QUARANTINED,
            ReferralStatus.FAILED,
        }
    ),
    ReferralStatus.NEEDS_INFORMATION: frozenset(
        {
            ReferralStatus.PROCESSING,
            ReferralStatus.NEEDS_REVIEW,
            ReferralStatus.READY_TO_ROUTE,
            ReferralStatus.QUARANTINED,
            ReferralStatus.FAILED,
        }
    ),
    ReferralStatus.READY_TO_ROUTE: frozenset(
        {ReferralStatus.NEEDS_REVIEW, ReferralStatus.ROUTED, ReferralStatus.FAILED}
    ),
    ReferralStatus.ROUTED: frozenset({ReferralStatus.IN_PROGRESS}),
    ReferralStatus.IN_PROGRESS: frozenset({ReferralStatus.COMPLETED, ReferralStatus.FAILED}),
    ReferralStatus.COMPLETED: frozenset(),
    ReferralStatus.FAILED: frozenset({ReferralStatus.PROCESSING}),
}


class InvalidTransitionError(ValueError):
    def __init__(self, current: ReferralStatus, target: ReferralStatus) -> None:
        super().__init__(f"Transition from {current} to {target} is not allowed")
        self.current = current
        self.target = target


def ensure_transition(current: ReferralStatus, target: ReferralStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransitionError(current, target)
