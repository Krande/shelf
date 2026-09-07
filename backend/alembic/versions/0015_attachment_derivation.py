"""attachment_derivation — multi-version derived PDFs per attachment

Revision ID: 0015_attachment_derivation
Revises: 0014_processing_progress
Create Date: 2026-05-06

Replaces the snapshot-and-overwrite-storage_key model. Going forward,
each derived PDF (OCR'd, outlined, or anything else we add later) is
written to its OWN storage key and gets a row here pointing at its
parent (the input it was derived from). The reader resolves "current
view" as latest-outline > latest-ocr > attachment.storage_key, but
also lets the user step back through the chain for comparison —
crucial for evaluating different OCR / outline algorithms against
each other against the same source PDF.

Why a generic table instead of columns: the user wants N versions
per kind to compare across runs, not just one "current". Columns
collapse to a single value; the table preserves history.

Legacy data (rows where original_preserved_at IS NOT NULL): not
back-filled here. ``attachments.storage_key`` for those rows may
still be a derivative — the .original sibling has the truth — but
the reader resolution above will fall through to ``storage_key``
(no derivations exist), so the legacy attachment continues to look
the same as before. New runs of OCR / outline will write fresh
derivations and shift the reader to the new chain.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_attachment_derivation"
down_revision: str | Sequence[str] | None = "0014_proc_progress"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "attachment_derivation",
        sa.Column(
            "id",
            sa.Uuid,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "attachment_id",
            sa.Uuid,
            sa.ForeignKey("attachments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # 'ocr' | 'outline' (open string so adding a new derivation kind
        # is a code-only change — no migration needed).
        sa.Column("kind", sa.String(), nullable=False),
        # Where the derived PDF lives in object storage. UNIQUE so
        # accidental duplicate writes for the same key surface as a
        # constraint violation rather than silent collision.
        sa.Column("storage_key", sa.String(), nullable=False, unique=True),
        # The input this derivation was produced from — usually the
        # previous derivation's storage_key, or the attachment's
        # original storage_key when this is the first step.
        sa.Column("parent_storage_key", sa.String(), nullable=False),
        # Versioned engine identifier (e.g. "olmocr/0.4.27 shelf/sha-…").
        # Same string the UI shows on the per-row engine attribution.
        sa.Column("engine", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # "Latest derivation of kind X for attachment Y" is the hot query
    # (reader resolution + worker input lookup); index in the same
    # order it'll be filtered + sorted.
    op.create_index(
        "ix_attachment_derivation_attachment_kind_created",
        "attachment_derivation",
        ["attachment_id", "kind", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_attachment_derivation_attachment_kind_created",
        table_name="attachment_derivation",
    )
    op.drop_table("attachment_derivation")
