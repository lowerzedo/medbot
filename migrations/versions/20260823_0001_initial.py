"""Create the Phase 1 referral workflow schema."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260823_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "referrals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=100), nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=False),
        sa.Column("specialty", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_service", sa.String(length=500), nullable=True),
        sa.Column("routing_queue", sa.String(length=120), nullable=True),
        sa.Column("complete", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_referrals_org_status", "referrals", ["organization_id", "status"])
    op.create_table(
        "documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("referral_id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=100), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("detected_mime_type", sa.String(length=100), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("processing_status", sa.String(length=32), nullable=False),
        sa.Column("classification", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("uploaded_by", sa.String(length=200), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["referral_id"], ["referrals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("referral_id", "sha256", name="uq_documents_referral_sha256"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_documents_org_referral", "documents", ["organization_id", "referral_id"])
    op.create_table(
        "document_pages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "page_number", name="uq_document_pages_number"),
    )
    op.create_table(
        "text_blocks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("block_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.String(length=64), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "block_index", name="uq_text_blocks_index"),
    )
    op.create_table(
        "extracted_fields",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("referral_id", sa.String(length=36), nullable=False),
        sa.Column("path", sa.String(length=160), nullable=False),
        sa.Column("ai_value", sa.Text(), nullable=False),
        sa.Column("human_value", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_status", sa.String(length=32), nullable=False),
        sa.Column("review_status", sa.String(length=32), nullable=False),
        sa.Column("reviewer_id", sa.String(length=200), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_reason", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(["referral_id"], ["referrals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("referral_id", "path", name="uq_extracted_fields_referral_path"),
    )
    op.create_table(
        "evidence_references",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("field_id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("block_id", sa.String(length=36), nullable=False),
        sa.Column("text_sha256", sa.String(length=64), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["block_id"], ["text_blocks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["field_id"], ["extracted_fields.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "validation_issues",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("referral_id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("field_path", sa.String(length=160), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["referral_id"], ["referrals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_validation_issues_referral", "validation_issues", ["referral_id"])
    op.create_table(
        "routing_decisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("referral_id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=100), nullable=False),
        sa.Column("rule_version", sa.String(length=40), nullable=False),
        sa.Column("input_specialty", sa.String(length=80), nullable=False),
        sa.Column("resulting_queue", sa.String(length=120), nullable=False),
        sa.Column("actor_id", sa.String(length=200), nullable=False),
        sa.Column("trace_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["referral_id"], ["referrals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("referral_id", name="uq_routing_decision_referral"),
    )
    op.create_index(
        "ix_routing_referral_created", "routing_decisions", ["referral_id", "created_at"]
    )
    op.create_table(
        "audit_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.String(length=100), nullable=False),
        sa.Column("referral_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=200), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("resource_id", sa.String(length=100), nullable=False),
        sa.Column("trace_id", sa.String(length=36), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("previous_event_hash", sa.String(length=64), nullable=True),
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["referral_id"], ["referrals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("referral_id", "event_hash", name="uq_audit_referral_hash"),
        sa.UniqueConstraint("referral_id", "sequence", name="uq_audit_referral_sequence"),
    )
    op.create_index("ix_audit_referral_sequence", "audit_records", ["referral_id", "sequence"])


def downgrade() -> None:
    op.drop_index("ix_audit_referral_sequence", table_name="audit_records")
    op.drop_table("audit_records")
    op.drop_index("ix_routing_referral_created", table_name="routing_decisions")
    op.drop_table("routing_decisions")
    op.drop_index("ix_validation_issues_referral", table_name="validation_issues")
    op.drop_table("validation_issues")
    op.drop_table("evidence_references")
    op.drop_table("extracted_fields")
    op.drop_table("text_blocks")
    op.drop_table("document_pages")
    op.drop_index("ix_documents_org_referral", table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_referrals_org_status", table_name="referrals")
    op.drop_table("referrals")
