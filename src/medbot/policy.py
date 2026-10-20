from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from medbot.schemas import CompletenessResult


@dataclass(frozen=True, slots=True)
class ReferralPolicy:
    required_fields: frozenset[str]
    required_documents: frozenset[str]
    routing_queue: str


POLICY_VERSION = "2026-08-01"
DEFAULT_POLICY = ReferralPolicy(
    required_fields=frozenset(
        {
            "patient.full_name",
            "patient.date_of_birth",
            "referring_provider.name",
            "requested_service",
        }
    ),
    required_documents=frozenset({"referral_order"}),
    routing_queue="general_referral_intake",
)
POLICIES: dict[str, ReferralPolicy] = {
    "cardiology": ReferralPolicy(
        required_fields=DEFAULT_POLICY.required_fields,
        required_documents=DEFAULT_POLICY.required_documents,
        routing_queue="cardiology_intake",
    ),
    "orthopedics": ReferralPolicy(
        required_fields=DEFAULT_POLICY.required_fields,
        required_documents=DEFAULT_POLICY.required_documents,
        routing_queue="musculoskeletal_intake",
    ),
}


def policy_for(specialty: str) -> ReferralPolicy:
    return POLICIES.get(specialty.lower(), DEFAULT_POLICY)


def evaluate_completeness(
    specialty: str, field_values: Mapping[str, str], document_types: set[str]
) -> CompletenessResult:
    policy = policy_for(specialty)
    missing_fields = sorted(
        field_path
        for field_path in policy.required_fields
        if not is_valid_field_value(field_path, field_values.get(field_path))
    )
    missing_documents = sorted(policy.required_documents - document_types)
    return CompletenessResult(
        complete=not missing_fields and not missing_documents,
        missing_fields=missing_fields,
        missing_documents=missing_documents,
        warnings=[],
    )


def is_valid_field_value(field_path: str, value: str | None) -> bool:
    if value is None or not value.strip():
        return False
    if field_path == "patient.date_of_birth":
        return parse_referral_date(value) is not None
    return True


def parse_referral_date(value: str | None) -> date | None:
    if value is None:
        return None
    normalized = value.strip()
    for candidate in (normalized, _american_to_iso(normalized)):
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def _american_to_iso(value: str) -> str:
    parts = value.split("/")
    if len(parts) != 3:
        return value
    month, day, year = parts
    return f"{year.zfill(4)}-{month.zfill(2)}-{day.zfill(2)}"


def route_for(specialty: str) -> tuple[str, str]:
    return policy_for(specialty).routing_queue, POLICY_VERSION
