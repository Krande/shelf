"""collections + item_collections tables

Revision ID: 0003_collections
Revises: 0002_items
Create Date: 2026-05-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_collections"
down_revision: str | Sequence[str] | None = "0002_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "collections",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "space_id",
            sa.Uuid,
            sa.ForeignKey("spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_id",
            sa.Uuid,
            sa.ForeignKey("collections.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_collections_space_parent",
        "collections",
        ["space_id", "parent_id"],
    )

    op.create_table(
        "item_collections",
        sa.Column(
            "item_id",
            sa.Uuid,
            sa.ForeignKey("items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "collection_id",
            sa.Uuid,
            sa.ForeignKey("collections.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("item_collections")
    op.drop_index("ix_collections_space_parent", table_name="collections")
    op.drop_table("collections")
