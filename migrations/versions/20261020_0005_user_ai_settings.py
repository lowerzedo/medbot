"""Personal provider choices, encrypted credentials, and safe settings audit events."""

import sqlalchemy as sa
from alembic import op

revision = "20261020_0005"
down_revision = "20261020_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_ai_settings",
        sa.Column("organization_id", sa.String(100), primary_key=True),
        sa.Column("user_id", sa.String(200), primary_key=True),
        sa.Column("active_provider", sa.String(20)),
        sa.Column("ollama_model", sa.String(150), nullable=False),
        sa.Column("openai_model", sa.String(150), nullable=False),
        sa.Column("gemini_model", sa.String(150), nullable=False),
        sa.Column("openai_key", sa.LargeBinary()),
        sa.Column("gemini_key", sa.LargeBinary()),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ai_settings_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(100), nullable=False),
        sa.Column("user_id", sa.String(200), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("provider", sa.String(20)),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ai_settings_events")
    op.drop_table("user_ai_settings")
