"""notes table — per-item rich-text notes

Revision ID: 0011_notes
Revises: 0010_tags
Create Date: 2026-05-04

A note is a child of an item (Zotero-style). Each note carries the
TipTap-authored HTML alongside a server-extracted plain-text twin
that powers the GIN-indexed tsvector. The plain-text column is also
useful for snippet rendering on a future "search inside notes" UI.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_notes"
down_revision: str | Sequence[str] | None = "0010_tags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notes",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "item_id",
            sa.Uuid,
            sa.ForeignKey("items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content_html", sa.Text(), nullable=False, server_default=""),
        sa.Column("content_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_notes_item_updated", "notes", ["item_id", "updated_at"]
    )
    op.execute(
        "ALTER TABLE notes ADD COLUMN tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', coalesce(content_text,''))) STORED"
    )
    op.create_index(
        "ix_notes_tsv", "notes", ["tsv"], postgresql_using="gin"
    )


def downgrade() -> None:
    op.drop_index("ix_notes_tsv", table_name="notes")
    op.drop_index("ix_notes_item_updated", table_name="notes")
    op.drop_table("notes")
