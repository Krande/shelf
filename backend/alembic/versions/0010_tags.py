"""tags + item_tags tables

Revision ID: 0010_tags
Revises: 0009_attachment_pages
Create Date: 2026-05-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import CITEXT

revision: str = "0010_tags"
down_revision: str | Sequence[str] | None = "0009_attachment_pages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tags",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "space_id",
            sa.Uuid,
            sa.ForeignKey("spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # CITEXT so (space_id, name) uniqueness is case-insensitive —
        # "Foo" and "foo" should not coexist as separate tags. The
        # extension is already enabled by 0001_initial.
        sa.Column("name", CITEXT(), nullable=False),
        sa.Column("color", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("space_id", "name", name="uq_tags_space_name"),
    )

    op.create_table(
        "item_tags",
        sa.Column(
            "item_id",
            sa.Uuid,
            sa.ForeignKey("items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            sa.Uuid,
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("item_tags")
    op.drop_table("tags")
