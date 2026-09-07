"""attachment_pages — per-page text + tsv

Revision ID: 0009_attachment_pages
Revises: 0008_attachment_fulltext
Create Date: 2026-05-04

The single concatenated `attachments.text_content` column is fine for
"is there a hit" checks but loses page numbers, which the snippet UI
needs to deep-link into the reader. This adds a sibling table that
holds one row per PDF page, with its own STORED tsvector + GIN so
ts_headline can produce per-page snippets ranked by ts_rank.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_attachment_pages"
down_revision: str | Sequence[str] | None = "0008_attachment_fulltext"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "attachment_pages",
        sa.Column(
            "attachment_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("attachments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "attachment_id", "page_number", name="pk_attachment_pages"
        ),
    )
    op.execute(
        "ALTER TABLE attachment_pages ADD COLUMN tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', coalesce(text,''))) STORED"
    )
    op.create_index(
        "ix_attachment_pages_tsv",
        "attachment_pages",
        ["tsv"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_attachment_pages_tsv", table_name="attachment_pages")
    op.drop_table("attachment_pages")
