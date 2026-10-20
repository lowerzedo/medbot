from __future__ import annotations

import hashlib
from collections.abc import Sequence
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy.orm import Session

from medbot.ai import AIProvider
from medbot.audit import append_audit_record
from medbot.document_processing import DocumentExtractor, ExtractedTextBlock, SourceRegion
from medbot.domain import (
    DocumentProcessingStatus,
    EvidenceStatus,
    FieldReviewStatus,
    IssueSeverity,
    ReferralStatus,
)
from medbot.errors import ApplicationError
from medbot.evidence import EvidenceVerificationError, verify_field_evidence
from medbot.field_grounding import normalize_field_value, source_candidates
from medbot.models import (
    DocumentPage,
    EvidenceReference,
    ExtractedField,
    Referral,
    TextBlock,
    ValidationIssue,
)
from medbot.observability import Observability
from medbot.referral_validation import (
    current_field_values,
    has_review_blockers,
    refresh_completeness,
)
from medbot.schemas import AIExtractedField, AIExtractionEvidence, AIExtractionOutput
from medbot.storage import (
    ObjectIntegrityError,
    ObjectStorage,
    ObjectStorageError,
    ObjectStorageUnavailableError,
    get_verified_object,
)
from medbot.workflow import field_by_path, transition_referral


class ReferralProcessor:
    def __init__(
        self,
        *,
        storage: ObjectStorage,
        extractor: DocumentExtractor,
        ai_provider: AIProvider,
        observability: Observability,
    ) -> None:
        self._storage = storage
        self._extractor = extractor
        self._ai_provider = ai_provider
        self._observability = observability

    def process(
        self, session: Session, referral: Referral, actor_id: str, trace_id: str
    ) -> ReferralStatus:
        transition_referral(referral, ReferralStatus.PROCESSING)
        append_audit_record(
            session,
            referral=referral,
            actor_id=actor_id,
            action="STATUS_CHANGED",
            resource_type="Referral",
            resource_id=referral.id,
            trace_id=trace_id,
            metadata={"status": ReferralStatus.PROCESSING},
        )
        referral.validation_issues.clear()
        with (
            self._observability.duration(self._observability.referral_processing_duration),
            self._observability.tracer.start_as_current_span(
                "referral.processing",
                record_exception=False,
                set_status_on_exception=False,
            ) as span,
        ):
            span.set_attribute("referral.id", referral.id)
            span.set_attribute("organization.id", referral.organization_id)
            blocks = self._extract_documents(session, referral, actor_id, trace_id)
            output = self._extract_structured_output(blocks)
            self._validate_unique_field_paths(output)
            append_audit_record(
                session,
                referral=referral,
                actor_id="medbot-extraction",
                action="AI_EXTRACTION_COMPLETED",
                resource_type="Referral",
                resource_id=referral.id,
                trace_id=trace_id,
                metadata={
                    "schema_version": output.schema_version,
                    "provider": type(self._ai_provider).__name__,
                },
            )
            evidence_failures = self._store_verified_fields(session, referral, output, blocks)
            self._record_conflicts(referral, blocks)
            completeness = refresh_completeness(referral)
            review_required = has_review_blockers(referral)
            target = (
                ReferralStatus.READY_TO_ROUTE
                if completeness.complete and not review_required
                else ReferralStatus.NEEDS_REVIEW
            )
            transition_referral(referral, target)
            referral.complete = completeness.complete
            referral.requested_service = current_field_values(referral).get("requested_service")
            append_audit_record(
                session,
                referral=referral,
                actor_id="medbot-workflow",
                action="EVIDENCE_VERIFIED",
                resource_type="Referral",
                resource_id=referral.id,
                trace_id=trace_id,
                metadata={
                    "failure_count": str(evidence_failures),
                    "verified_field_count": str(
                        sum(
                            field.evidence_status
                            in {
                                EvidenceStatus.VERIFIED,
                                EvidenceStatus.LOW_CONFIDENCE,
                                EvidenceStatus.REVIEW_REQUIRED,
                            }
                            for field in referral.extracted_fields
                        )
                    ),
                },
            )
            append_audit_record(
                session,
                referral=referral,
                actor_id="medbot-workflow",
                action="STATUS_CHANGED",
                resource_type="Referral",
                resource_id=referral.id,
                trace_id=trace_id,
                metadata={"status": target},
            )
        return target

    def fail(self, session: Session, referral: Referral, actor_id: str, trace_id: str) -> None:
        transition_referral(referral, ReferralStatus.FAILED)
        append_audit_record(
            session,
            referral=referral,
            actor_id=actor_id,
            action="STATUS_CHANGED",
            resource_type="Referral",
            resource_id=referral.id,
            trace_id=trace_id,
            metadata={"status": ReferralStatus.FAILED},
        )
        self._observability.referrals_failed.inc()

    @staticmethod
    def normalize_error(error: Exception) -> ApplicationError:
        if isinstance(error, ApplicationError):
            return error
        if isinstance(error, KeyError):
            return ApplicationError(
                "document_content_unavailable", "Document content is unavailable", 503
            )
        if isinstance(error, ObjectIntegrityError):
            return ApplicationError(
                "document_integrity_failed",
                "Stored document content failed its integrity check",
                409,
            )
        if isinstance(error, ObjectStorageUnavailableError):
            return ApplicationError(
                "document_content_unavailable", "Document content is unavailable", 503
            )
        if isinstance(error, ObjectStorageError):
            return ApplicationError(
                "object_storage_unavailable", "Object storage is temporarily unavailable", 503
            )
        if isinstance(error, (ValidationError, ValueError)):
            return ApplicationError(
                "invalid_ai_output", "Structured extraction output was rejected", 422
            )
        return ApplicationError("processing_failed", "Referral processing failed", 500)

    def _extract_documents(
        self, session: Session, referral: Referral, actor_id: str, trace_id: str
    ) -> tuple[ExtractedTextBlock, ...]:
        all_blocks: list[ExtractedTextBlock] = []
        append_audit_record(
            session,
            referral=referral,
            actor_id="medbot-document-extractor",
            action="DOCUMENT_EXTRACTION_STARTED",
            resource_type="Referral",
            resource_id=referral.id,
            trace_id=trace_id,
        )
        for document in referral.documents:
            content: bytes | None = None
            if not document.text_blocks or document.fhir_sha1 is None:
                content = get_verified_object(
                    self._storage,
                    document.storage_key,
                    expected_sha256=document.sha256,
                    expected_size=document.size_bytes,
                )
                document.fhir_sha1 = hashlib.sha1(content, usedforsecurity=False).hexdigest()
            if not document.text_blocks:
                assert content is not None
                with (
                    self._observability.duration(self._observability.document_processing_duration),
                    self._observability.tracer.start_as_current_span(
                        "document.extraction",
                        record_exception=False,
                        set_status_on_exception=False,
                    ) as span,
                ):
                    span.set_attribute("referral.id", referral.id)
                    span.set_attribute("document.id", document.id)
                    try:
                        extracted = self._extractor.extract(document.id, content)
                    except ApplicationError:
                        document.processing_status = DocumentProcessingStatus.FAILED
                        raise
                    except Exception:
                        raise ApplicationError(
                            "document_processing_unavailable",
                            "Document processing is temporarily unavailable",
                            503,
                        ) from None
                page_numbers = sorted({block.page_number for block in extracted})
                for page_number in page_numbers:
                    document.pages.append(
                        DocumentPage(
                            id=str(uuid4()),
                            document_id=document.id,
                            page_number=page_number,
                        )
                    )
                for block in extracted:
                    document.text_blocks.append(
                        TextBlock(
                            id=block.id,
                            document_id=document.id,
                            page_number=block.page_number,
                            block_index=block.block_index,
                            text=block.text,
                            text_sha256=block.text_sha256,
                            start_offset=block.start_offset,
                            end_offset=block.end_offset,
                            extraction_method=block.extraction_method,
                            ocr_confidence=block.ocr_confidence,
                            region_x=block.region.x if block.region else None,
                            region_y=block.region.y if block.region else None,
                            region_width=block.region.width if block.region else None,
                            region_height=block.region.height if block.region else None,
                        )
                    )
                document.processing_status = DocumentProcessingStatus.EXTRACTED
                all_blocks.extend(extracted)
            else:
                all_blocks.extend(self._block_models(document.text_blocks))
        if not all_blocks:
            raise ApplicationError(
                "document_required", "At least one processable PDF is required", 422
            )
        session.flush()
        append_audit_record(
            session,
            referral=referral,
            actor_id=actor_id,
            action="DOCUMENT_EXTRACTION_COMPLETED",
            resource_type="Referral",
            resource_id=referral.id,
            trace_id=trace_id,
            metadata={"document_count": str(len(referral.documents))},
        )
        return tuple(all_blocks)

    def _store_verified_fields(
        self,
        session: Session,
        referral: Referral,
        output: AIExtractionOutput,
        blocks: tuple[ExtractedTextBlock, ...],
    ) -> int:
        existing = {field.path: field for field in referral.extracted_fields}
        allowed_documents = frozenset(document.id for document in referral.documents)
        failure_count = 0
        proposed_paths = {proposed.path for proposed in output.fields}
        for stale_field in referral.extracted_fields:
            if stale_field.path not in proposed_paths and stale_field.human_value is None:
                stale_field.evidence_status = EvidenceStatus.UNSUPPORTED
                stale_field.review_status = FieldReviewStatus.PENDING
                stale_field.evidence.clear()
        for proposed in output.fields:
            field = existing.get(proposed.path)
            try:
                verified = verify_field_evidence(
                    proposed, blocks, allowed_documents, allow_unlabeled=output.requires_review
                )
            except EvidenceVerificationError:
                failure_count += 1
                self._observability.evidence_failures.inc()
                if field is not None and field.human_value is None:
                    field.evidence_status = EvidenceStatus.UNSUPPORTED
                    field.evidence.clear()
                referral.validation_issues.append(
                    ValidationIssue(
                        id=str(uuid4()),
                        referral_id=referral.id,
                        code="unsupported_evidence",
                        severity=IssueSeverity.ERROR,
                        message="An extracted field did not have verifiable source evidence",
                        field_path=proposed.path,
                        resolved=field is not None and field.human_value is not None,
                    )
                )
                continue
            requires_review = (
                output.requires_review
                or any(
                    block.extraction_method == "ocr"
                    and block.id in {item.source.block_id for item in verified}
                    for block in blocks
                )
                or any(not item.field_grounded for item in verified)
            )
            if field is not None and field.human_value is not None:
                # Keep the original proposal and explicit human provenance on retries.
                continue
            if (
                field is not None
                and field.review_status == FieldReviewStatus.APPROVED
                and field.ai_value == proposed.value
                and {(item.document_id, item.block_id, item.quote) for item in field.evidence}
                == {
                    (item.source.document_id, item.source.block_id, item.source.quote)
                    for item in verified
                }
            ):
                # Re-running a model does not undo a review of the same value and exact sources.
                # New conflicts are still checked independently after this pass.
                continue
            if field is None:
                field = ExtractedField(
                    id=str(uuid4()),
                    referral_id=referral.id,
                    path=proposed.path,
                    ai_value=proposed.value,
                    confidence=proposed.confidence,
                    evidence_status=(
                        EvidenceStatus.LOW_CONFIDENCE
                        if proposed.confidence < 0.8
                        else EvidenceStatus.REVIEW_REQUIRED
                        if requires_review
                        else EvidenceStatus.VERIFIED
                    ),
                    review_status=FieldReviewStatus.PENDING,
                )
                referral.extracted_fields.append(field)
                existing[field.path] = field
            else:
                # An unchanged value with different evidence still needs a new source review.
                field.review_status = FieldReviewStatus.PENDING
                field.reviewer_id = None
                field.reviewed_at = None
                field.review_reason = None
                field.ai_value = proposed.value
                field.confidence = proposed.confidence
                if field.human_value is None:
                    field.evidence_status = (
                        EvidenceStatus.LOW_CONFIDENCE
                        if proposed.confidence < 0.8
                        else EvidenceStatus.REVIEW_REQUIRED
                        if requires_review
                        else EvidenceStatus.VERIFIED
                    )
                field.evidence.clear()
            for reference in verified:
                field.evidence.append(
                    EvidenceReference(
                        id=str(uuid4()),
                        field_id=field.id,
                        document_id=reference.source.document_id,
                        page_number=reference.source.page_number,
                        block_id=reference.source.block_id,
                        text_sha256=reference.source.text_sha256,
                        quote=reference.source.quote,
                        start_offset=reference.start_offset,
                        end_offset=reference.end_offset,
                        status=EvidenceStatus.VERIFIED,
                    )
                )
            if proposed.confidence < 0.8 or requires_review:
                referral.validation_issues.append(
                    ValidationIssue(
                        id=str(uuid4()),
                        referral_id=referral.id,
                        code="review_required" if requires_review else "low_confidence",
                        severity=IssueSeverity.WARNING,
                        message="An extracted field requires reviewer confirmation",
                        field_path=proposed.path,
                        resolved=False,
                    )
                )
        session.flush()
        return failure_count

    @staticmethod
    def _record_conflicts(referral: Referral, blocks: tuple[ExtractedTextBlock, ...]) -> None:
        for path, candidates in source_candidates(blocks).items():
            if len({normalize_field_value(path, candidate.value) for candidate in candidates}) < 2:
                continue
            field = field_by_path(referral, path)
            if field is None:
                field = ExtractedField(
                    id=str(uuid4()),
                    referral_id=referral.id,
                    path=path,
                    ai_value=candidates[0].value,
                    confidence=0.0,
                    evidence_status=EvidenceStatus.CONFLICTING,
                    review_status=FieldReviewStatus.PENDING,
                )
                referral.extracted_fields.append(field)
            signatures = {
                (item.block.document_id, item.block.id, item.quote) for item in candidates
            }
            previous = {(item.document_id, item.block_id, item.quote) for item in field.evidence}
            if (
                field.human_value is not None
                and field.evidence_status == EvidenceStatus.HUMAN_CORRECTED
                and signatures == previous
            ):
                continue
            field.evidence_status = EvidenceStatus.CONFLICTING
            field.review_status = FieldReviewStatus.PENDING
            field.evidence.clear()
            for candidate in candidates:
                reference = AIExtractionEvidence(
                    document_id=candidate.block.document_id,
                    page_number=candidate.block.page_number,
                    block_id=candidate.block.id,
                    text_sha256=candidate.block.text_sha256,
                    quote=candidate.quote,
                )
                verified = verify_field_evidence(
                    AIExtractedField(
                        path=path, value=candidate.value, confidence=0.0, evidence=(reference,)
                    ),
                    blocks,
                    frozenset(document.id for document in referral.documents),
                )[0]
                field.evidence.append(
                    EvidenceReference(
                        id=str(uuid4()),
                        field_id=field.id,
                        document_id=reference.document_id,
                        page_number=reference.page_number,
                        block_id=reference.block_id,
                        text_sha256=reference.text_sha256,
                        quote=reference.quote,
                        start_offset=verified.start_offset,
                        end_offset=verified.end_offset,
                        status=EvidenceStatus.VERIFIED,
                    )
                )
            referral.validation_issues.append(
                ValidationIssue(
                    id=str(uuid4()),
                    referral_id=referral.id,
                    code="conflicting_values",
                    severity=IssueSeverity.ERROR,
                    message="Source documents disagree; a reviewer must resolve the field",
                    field_path=path,
                    resolved=False,
                )
            )

    @staticmethod
    def _validate_unique_field_paths(output: AIExtractionOutput) -> None:
        paths = [field.path for field in output.fields]
        if len(paths) != len(set(paths)):
            raise ValueError("AI extraction contains duplicate field paths")

    def _extract_structured_output(
        self, blocks: tuple[ExtractedTextBlock, ...]
    ) -> AIExtractionOutput:
        try:
            raw_output = self._ai_provider.extract(blocks)
        except ApplicationError:
            raise
        except Exception:
            raise ApplicationError(
                "ai_provider_unavailable",
                "The extraction provider is temporarily unavailable",
                503,
            ) from None
        return AIExtractionOutput.model_validate(raw_output)

    @staticmethod
    def _block_models(blocks: Sequence[TextBlock]) -> list[ExtractedTextBlock]:
        return [
            ExtractedTextBlock(
                id=block.id,
                document_id=block.document_id,
                page_number=block.page_number,
                block_index=block.block_index,
                text=block.text,
                text_sha256=block.text_sha256,
                start_offset=block.start_offset,
                end_offset=block.end_offset,
                extraction_method=block.extraction_method,
                ocr_confidence=block.ocr_confidence,
                region=(
                    SourceRegion(
                        x=block.region_x,
                        y=block.region_y,
                        width=block.region_width,
                        height=block.region_height,
                    )
                    if block.region_x is not None
                    and block.region_y is not None
                    and block.region_width is not None
                    and block.region_height is not None
                    else None
                ),
            )
            for block in blocks
        ]
