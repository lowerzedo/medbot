from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from medbot.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class UserAISettings(Base):
    __tablename__ = "user_ai_settings"

    organization_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    active_provider: Mapped[str | None] = mapped_column(String(20))
    ollama_model: Mapped[str] = mapped_column(String(150), default="qwen3:4b")
    openai_model: Mapped[str] = mapped_column(String(150), default="")
    gemini_model: Mapped[str] = mapped_column(String(150), default="")
    openai_key: Mapped[bytes | None] = mapped_column(LargeBinary)
    gemini_key: Mapped[bytes | None] = mapped_column(LargeBinary)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AISettingsEvent(Base):
    __tablename__ = "ai_settings_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(100), nullable=False)
    user_id: Mapped[str] = mapped_column(String(200), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(20))
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Referral(Base):
    __tablename__ = "referrals"
    __table_args__ = (Index("ix_referrals_org_status", "organization_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(100), nullable=False)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    specialty: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_service: Mapped[str | None] = mapped_column(String(500))
    routing_queue: Mapped[str | None] = mapped_column(String(120))
    complete: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    documents: Mapped[list[Document]] = relationship(
        back_populates="referral", cascade="all, delete-orphan", order_by="Document.uploaded_at"
    )
    extracted_fields: Mapped[list[ExtractedField]] = relationship(
        back_populates="referral", cascade="all, delete-orphan", order_by="ExtractedField.path"
    )
    validation_issues: Mapped[list[ValidationIssue]] = relationship(
        back_populates="referral", cascade="all, delete-orphan"
    )
    audit_records: Mapped[list[AuditRecord]] = relationship(
        back_populates="referral", cascade="all, delete-orphan", order_by="AuditRecord.sequence"
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("referral_id", "sha256", name="uq_documents_referral_sha256"),
        Index("ix_documents_org_referral", "organization_id", "referral_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    referral_id: Mapped[str] = mapped_column(
        ForeignKey("referrals.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[str] = mapped_column(String(100), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    detected_mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # FHIR R4 Attachment.hash requires SHA-1; storage integrity always uses SHA-256.
    fhir_sha1: Mapped[str | None] = mapped_column(String(40))
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    processing_status: Mapped[str] = mapped_column(String(32), nullable=False)
    classification: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    uploaded_by: Mapped[str] = mapped_column(String(200), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    referral: Mapped[Referral] = relationship(back_populates="documents")
    pages: Mapped[list[DocumentPage]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentPage.page_number"
    )
    text_blocks: Mapped[list[TextBlock]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="TextBlock.block_index"
    )


class DocumentPage(Base):
    __tablename__ = "document_pages"
    __table_args__ = (
        UniqueConstraint("document_id", "page_number", name="uq_document_pages_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)

    document: Mapped[Document] = relationship(back_populates="pages")


class TextBlock(Base):
    __tablename__ = "text_blocks"
    __table_args__ = (UniqueConstraint("document_id", "block_index", name="uq_text_blocks_index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    block_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(20), nullable=False, default="fixture")
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    region_x: Mapped[float | None] = mapped_column(Float)
    region_y: Mapped[float | None] = mapped_column(Float)
    region_width: Mapped[float | None] = mapped_column(Float)
    region_height: Mapped[float | None] = mapped_column(Float)

    document: Mapped[Document] = relationship(back_populates="text_blocks")


class ExtractedField(Base):
    __tablename__ = "extracted_fields"
    __table_args__ = (
        UniqueConstraint("referral_id", "path", name="uq_extracted_fields_referral_path"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    referral_id: Mapped[str] = mapped_column(
        ForeignKey("referrals.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(String(160), nullable=False)
    ai_value: Mapped[str | None] = mapped_column(Text)
    human_value: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_status: Mapped[str] = mapped_column(String(32), nullable=False)
    review_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reviewer_id: Mapped[str | None] = mapped_column(String(200))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_reason: Mapped[str | None] = mapped_column(String(500))

    referral: Mapped[Referral] = relationship(back_populates="extracted_fields")
    evidence: Mapped[list[EvidenceReference]] = relationship(
        back_populates="field", cascade="all, delete-orphan"
    )


class EvidenceReference(Base):
    __tablename__ = "evidence_references"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    field_id: Mapped[str] = mapped_column(
        ForeignKey("extracted_fields.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    block_id: Mapped[str] = mapped_column(
        ForeignKey("text_blocks.id", ondelete="CASCADE"), nullable=False
    )
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    field: Mapped[ExtractedField] = relationship(back_populates="evidence")
    block: Mapped[TextBlock] = relationship()


class ValidationIssue(Base):
    __tablename__ = "validation_issues"
    __table_args__ = (Index("ix_validation_issues_referral", "referral_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    referral_id: Mapped[str] = mapped_column(
        ForeignKey("referrals.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    field_path: Mapped[str | None] = mapped_column(String(160))
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    referral: Mapped[Referral] = relationship(back_populates="validation_issues")


class RoutingDecision(Base):
    __tablename__ = "routing_decisions"
    __table_args__ = (
        Index("ix_routing_referral_created", "referral_id", "created_at"),
        UniqueConstraint("referral_id", name="uq_routing_decision_referral"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    referral_id: Mapped[str] = mapped_column(
        ForeignKey("referrals.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[str] = mapped_column(String(100), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(40), nullable=False)
    input_specialty: Mapped[str] = mapped_column(String(80), nullable=False)
    resulting_queue: Mapped[str] = mapped_column(String(120), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuditRecord(Base):
    __tablename__ = "audit_records"
    __table_args__ = (
        Index("ix_audit_referral_sequence", "referral_id", "sequence"),
        UniqueConstraint("referral_id", "event_hash", name="uq_audit_referral_hash"),
        UniqueConstraint("referral_id", "sequence", name="uq_audit_referral_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    organization_id: Mapped[str] = mapped_column(String(100), nullable=False)
    referral_id: Mapped[str] = mapped_column(
        ForeignKey("referrals.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(100), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(36), nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    previous_event_hash: Mapped[str | None] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    referral: Mapped[Referral] = relationship(back_populates="audit_records")
