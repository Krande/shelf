"""items: origin_id, the item an archive import came from

Revision ID: 0028_item_origin
Revises: 0027_audit_events
Create Date: 2026-10-01

An archive import creates new items -- the source's ids may be taken, and
usually belong to another instance -- so without a record of where each
came from, importing the same archive twice would duplicate it, and an
interrupted import couldn't be resumed by running it again.

A plain column, not a foreign key: the source is typically elsewhere.
Indexed with space_id because the lookup is always "which items in this
space came from these".
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028_item_origin"
down_revision: str | Sequence[str] | None = "0027_audit_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "items",
        sa.Column("origin_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_items_space_origin", "items", ["space_id", "origin_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_items_space_origin", table_name="items")
    op.drop_column("items", "origin_id")
