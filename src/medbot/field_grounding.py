"""Ground the supported labeled referral fields in source text, independently of AI output."""

from __future__ import annotations

from dataclasses import dataclass

from medbot.document_processing import ExtractedTextBlock
from medbot.policy import parse_referral_date

FIELD_LABELS = {
    "patient name": "patient.full_name",
    "date of birth": "patient.date_of_birth",
    "dob": "patient.date_of_birth",
    "referring provider": "referring_provider.name",
    "requested service": "requested_service",
    "specialty": "specialty",
}


def normalize_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def normalize_field_value(path: str, value: str) -> str:
    if path == "patient.date_of_birth":
        parsed = parse_referral_date(value)
        if parsed is not None:
            return parsed.isoformat()
    return normalize_text(value)


def parse_labeled_field(line: str) -> tuple[str, str] | None:
    label, separator, value = line.partition(":")
    path = FIELD_LABELS.get(normalize_text(label))
    if separator and path is not None and value.strip():
        return path, value.strip()
    return None


@dataclass(frozen=True, slots=True)
class SourceCandidate:
    path: str
    value: str
    quote: str
    block: ExtractedTextBlock


def source_candidates(blocks: tuple[ExtractedTextBlock, ...]) -> dict[str, list[SourceCandidate]]:
    candidates: dict[str, list[SourceCandidate]] = {}
    for block in blocks:
        for line in block.text.splitlines():
            parsed = parse_labeled_field(line)
            if parsed is not None:
                path, value = parsed
                candidates.setdefault(path, []).append(SourceCandidate(path, value, line, block))
    return candidates
