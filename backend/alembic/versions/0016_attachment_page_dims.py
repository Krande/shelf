"""attachment_pages — add width_pts / height_pts columns

Revision ID: 0016_attachment_page_dims
Revises: 0015_attachment_derivation
Create Date: 2026-05-08

Lets the reader seed its per-page virtualizer height map from server
data instead of opening every page client-side at PDF load time. Old
rows stay NULL until the next extract run repopulates them; the
reader falls back to a sample-first-page baseline when dims aren't
available.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_attachment_page_dims"
down_revision: str | Sequence[str] | None = "0015_attachment_derivation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "attachment_pages",
        sa.Column("width_pts", sa.Float(), nullable=True),
    )
    op.add_column(
        "attachment_pages",
        sa.Column("height_pts", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("attachment_pages", "height_pts")
    op.drop_column("attachment_pages", "width_pts")
