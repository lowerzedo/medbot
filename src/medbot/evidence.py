from __future__ import annotations

import re
from dataclasses import dataclass

from medbot.document_processing import ExtractedTextBlock
from medbot.field_grounding import (
    FIELD_LABELS,
    normalize_field_value,
    normalize_text,
    parse_labeled_field,
)
from medbot.schemas import AIExtractedField, AIExtractionEvidence


class EvidenceVerificationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class VerifiedEvidence:
    source: AIExtractionEvidence
    start_offset: int
    end_offset: int
    field_grounded: bool = True


def verify_field_evidence(
    field: AIExtractedField,
    blocks: tuple[ExtractedTextBlock, ...],
    allowed_document_ids: frozenset[str],
    *,
    allow_unlabeled: bool = False,
) -> tuple[VerifiedEvidence, ...]:
    block_by_id = {block.id: block for block in blocks}
    verified: list[VerifiedEvidence] = []
    for reference in field.evidence:
        if reference.document_id not in allowed_document_ids:
            raise EvidenceVerificationError("Evidence document does not belong to this referral")
        block = block_by_id.get(reference.block_id)
        if block is None:
            raise EvidenceVerificationError("Evidence block does not exist")
        if block.document_id != reference.document_id or block.page_number != reference.page_number:
            raise EvidenceVerificationError("Evidence location does not match the source block")
        if block.text_sha256 != reference.text_sha256:
            raise EvidenceVerificationError("Evidence block hash does not match")
        start = block.text.find(reference.quote)
        if start < 0:
            raise EvidenceVerificationError("Evidence quote is not present in the source block")
        # A substring can omit the real label or splice part of a longer value. Require a
        # complete source line with a recognized label matching this field's meaning.
        lines = [
            line for line in block.text.splitlines() if line.strip() == reference.quote.strip()
        ]
        grounded = bool(lines) and _quote_supports_field(field, lines[0])
        narrative_match = (
            allow_unlabeled
            and bool(lines)
            and parse_labeled_field(lines[0]) is None
            and field.path in FIELD_LABELS.values()
            and re.search(
                r"(?<!\w)" + re.escape(normalize_text(field.value)) + r"(?!\w)",
                normalize_text(reference.quote),
            )
            is not None
        )
        if not grounded and not narrative_match:
            raise EvidenceVerificationError("Evidence does not support this field and value")
        verified.append(
            VerifiedEvidence(
                source=reference,
                start_offset=start,
                end_offset=start + len(reference.quote),
                field_grounded=grounded,
            )
        )
    if not verified:
        raise EvidenceVerificationError("At least one evidence reference is required")
    return tuple(verified)


def _quote_supports_field(field: AIExtractedField, quote: str) -> bool:
    parsed = parse_labeled_field(quote)
    return (
        parsed is not None
        and parsed[0] == field.path
        and normalize_field_value(field.path, parsed[1])
        == normalize_field_value(field.path, field.value)
    )
