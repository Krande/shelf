"""attachment full-text columns

Revision ID: 0008_attachment_fulltext
Revises: 0007_annotations
Create Date: 2026-05-04

Adds the columns + index that back fulltext search over PDF bodies.
The text itself lands in `text_content`; `tsv` is a STORED generated
column so the GIN index stays in sync without trigger plumbing. The
status enum is a plain string column rather than a Postgres ENUM so
adding new states later (e.g. `ocr_pending`) doesn't need a migration.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_attachment_fulltext"
down_revision: str | Sequence[str] | None = "0007_annotations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "attachments",
        sa.Column("text_content", sa.Text(), nullable=True),
    )
    op.add_column(
        "attachments",
        sa.Column("text_chars", sa.Integer(), nullable=True),
    )
    op.add_column(
        "attachments",
        sa.Column(
            "extracted_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "attachments",
        sa.Column("extraction_status", sa.String(), nullable=True),
    )
    # GENERATED ALWAYS … STORED — Postgres recomputes on row write,
    # which is cheap enough at the volumes we expect and means we
    # never have to remember to update tsv ourselves.
    op.execute(
        "ALTER TABLE attachments ADD COLUMN tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', coalesce(text_content,''))) STORED"
    )
    op.create_index(
        "ix_attachments_tsv",
        "attachments",
        ["tsv"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_attachments_tsv", table_name="attachments")
    op.execute("ALTER TABLE attachments DROP COLUMN tsv")
    op.drop_column("attachments", "extraction_status")
    op.drop_column("attachments", "extracted_at")
    op.drop_column("attachments", "text_chars")
    op.drop_column("attachments", "text_content")
