"""attachment_processing: live progress columns

Revision ID: 0014_proc_progress
Revises: 0013_orig_preserved
Create Date: 2026-05-05

Adds ``progress_done`` and ``progress_total`` so a running OCR or
outline job can stream a "page 23 / 198" indicator into the UI.
The OCR worker writes them by tailing ocrmypdf's stderr; the
outline worker (Phase C) wires up Marker's progress callback.

Both columns are nullable: a row only has progress info while a
job is actively running, and only for workers that emit it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_proc_progress"
down_revision: str | Sequence[str] | None = "0013_orig_preserved"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "attachment_processing",
        sa.Column("progress_done", sa.Integer(), nullable=True),
    )
    op.add_column(
        "attachment_processing",
        sa.Column("progress_total", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("attachment_processing", "progress_total")
    op.drop_column("attachment_processing", "progress_done")
