from __future__ import annotations

from typing import Protocol

from medbot.document_processing import ExtractedTextBlock
from medbot.field_grounding import source_candidates
from medbot.schemas import (
    AIExtractedField,
    AIExtractionEvidence,
    AIExtractionOutput,
)


class AIProvider(Protocol):
    def extract(self, blocks: tuple[ExtractedTextBlock, ...]) -> object: ...


class FixtureAIProvider:
    """Deterministic label extractor with no tools or workflow permissions."""

    def extract(self, blocks: tuple[ExtractedTextBlock, ...]) -> object:
        fields = []
        for path, candidates in source_candidates(blocks).items():
            candidate = candidates[0]
            fields.append(
                AIExtractedField(
                    path=path,
                    value=candidate.value,
                    confidence=0.99,
                    evidence=(
                        AIExtractionEvidence(
                            document_id=candidate.block.document_id,
                            page_number=candidate.block.page_number,
                            block_id=candidate.block.id,
                            text_sha256=candidate.block.text_sha256,
                            quote=candidate.quote,
                        ),
                    ),
                )
            )
        return AIExtractionOutput(schema_version="1.0", fields=tuple(fields))
