"""attachments table

Revision ID: 0004_attachments
Revises: 0003_collections
Create Date: 2026-05-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_attachments"
down_revision: str | Sequence[str] | None = "0003_collections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "attachments",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "item_id",
            sa.Uuid,
            sa.ForeignKey("items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("storage_key", sa.String(), nullable=False, unique=True),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_by",
            sa.Uuid,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_attachments_item", "attachments", ["item_id"])


def downgrade() -> None:
    op.drop_index("ix_attachments_item", table_name="attachments")
    op.drop_table("attachments")
