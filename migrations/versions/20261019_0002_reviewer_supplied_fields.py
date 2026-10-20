"""Allow reviewer-supplied fields without inventing an AI proposal."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261019_0002"
down_revision: str | Sequence[str] | None = "20260823_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("extracted_fields", "ai_value", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    # Never manufacture extraction history to satisfy the old NOT NULL constraint.
    connection = op.get_bind()
    count = connection.scalar(
        sa.text("SELECT count(*) FROM extracted_fields WHERE ai_value IS NULL")
    )
    if count:
        raise RuntimeError("Cannot downgrade while reviewer-supplied fields exist")
    op.alter_column("extracted_fields", "ai_value", existing_type=sa.Text(), nullable=False)
