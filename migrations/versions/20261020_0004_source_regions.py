"""Preserve text/OCR method, confidence, and source-page coordinates."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261020_0004"
down_revision: str | Sequence[str] | None = "20261019_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "text_blocks",
        sa.Column("extraction_method", sa.String(20), nullable=False, server_default="fixture"),
    )
    for name in ("ocr_confidence", "region_x", "region_y", "region_width", "region_height"):
        op.add_column("text_blocks", sa.Column(name, sa.Float(), nullable=True))


def downgrade() -> None:
    for name in (
        "region_height",
        "region_width",
        "region_y",
        "region_x",
        "ocr_confidence",
        "extraction_method",
    ):
        op.drop_column("text_blocks", name)
