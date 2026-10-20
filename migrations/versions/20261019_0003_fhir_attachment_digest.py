"""Store the FHIR R4 attachment digest separately from the integrity SHA-256."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261019_0003"
down_revision: str | Sequence[str] | None = "20261019_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing objects are not fetched by a schema migration. Reprocessing fills this
    # optional interoperability digest after verifying the original SHA-256 and size.
    op.add_column("documents", sa.Column("fhir_sha1", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "fhir_sha1")
