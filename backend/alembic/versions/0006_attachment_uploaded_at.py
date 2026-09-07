"""attachments.uploaded_at

Revision ID: 0006_attachment_uploaded_at
Revises: 0005_api_tokens
Create Date: 2026-05-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_attachment_uploaded_at"
down_revision: str | Sequence[str] | None = "0005_api_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "attachments",
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Existing rows were created under the old "trust on register" model
    # — they have an object in the bucket and should not look pending.
    op.execute(
        "UPDATE attachments SET uploaded_at = created_at WHERE uploaded_at IS NULL"
    )


def downgrade() -> None:
    op.drop_column("attachments", "uploaded_at")
