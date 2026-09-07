"""attachment_processing — quality assessment + future-worker state

Revision ID: 0012_attachment_processing
Revises: 0011_notes
Create Date: 2026-05-04

Phase A of the post-extraction pipeline: every PDF attachment gets
one row here describing the extracted-text quality, the embedded
TOC size, and whether OCR or outline-generation should run as a
follow-up. Phases B (OCR worker) and C (outline worker) populate
the *_status / outline_json fields; Phase A only fills the
diagnostic columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0012_attachment_processing"
down_revision: str | Sequence[str] | None = "0011_notes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "attachment_processing",
        sa.Column(
            "attachment_id",
            sa.Uuid,
            sa.ForeignKey("attachments.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=True),
        # Quality metrics from the extract worker.
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("text_chars", sa.Integer(), nullable=True),
        sa.Column("replacement_char_ratio", sa.Float(), nullable=True),
        sa.Column("alpha_ratio", sa.Float(), nullable=True),
        sa.Column("chars_per_page", sa.Float(), nullable=True),
        sa.Column("toc_entry_count", sa.Integer(), nullable=True),
        # Detection flags.
        sa.Column(
            "needs_ocr", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "needs_outline",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        # Phase B (OCR worker) state.
        sa.Column(
            "ocr_status",
            sa.String(),
            nullable=False,
            server_default=sa.text("'untouched'"),
        ),
        sa.Column("ocr_engine", sa.String(), nullable=True),
        sa.Column(
            "ocr_completed_at", sa.DateTime(timezone=True), nullable=True
        ),
        # Phase C (outline worker) state.
        sa.Column(
            "outline_status",
            sa.String(),
            nullable=False,
            server_default=sa.text("'untouched'"),
        ),
        sa.Column("outline_engine", sa.String(), nullable=True),
        sa.Column(
            "outline_completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("outline_json", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_attachment_processing_needs_ocr",
        "attachment_processing",
        ["needs_ocr"],
        postgresql_where=sa.text("needs_ocr = true"),
    )
    op.create_index(
        "ix_attachment_processing_needs_outline",
        "attachment_processing",
        ["needs_outline"],
        postgresql_where=sa.text("needs_outline = true"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_attachment_processing_needs_outline",
        table_name="attachment_processing",
    )
    op.drop_index(
        "ix_attachment_processing_needs_ocr",
        table_name="attachment_processing",
    )
    op.drop_table("attachment_processing")
