"""attachment_processing: track whether the original PDF is preserved

Revision ID: 0013_orig_preserved
Revises: 0012_attachment_processing
Create Date: 2026-05-05

Adds ``original_preserved_at`` to ``attachment_processing``. Set the
moment we make a copy of the upload at the ``<storage_key>.original``
sibling key — either eagerly when the upload completes, or lazily
right before the first mutation (OCR re-write) for attachments that
predate this column.

A non-null value here is the signal the SPA needs to enable the
"restore original" action.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_orig_preserved"
down_revision: str | Sequence[str] | None = "0012_attachment_processing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "attachment_processing",
        sa.Column(
            "original_preserved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("attachment_processing", "original_preserved_at")
