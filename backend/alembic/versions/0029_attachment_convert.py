"""attachment_processing: convert_status / engine / completed_at / error

Revision ID: 0029_attachment_convert
Revises: 0028_item_origin
Create Date: 2026-10-01

Non-PDF uploads (Word, PowerPoint, spreadsheets, images, ...) are rendered
to PDF by the worker's ``convert`` consumer; the PDF lands as an
``attachment_derivation`` row of kind ``convert`` and the original upload
stays at ``attachments.storage_key``. These columns carry that job's state
the same way ``ocr_*`` / ``outline_*`` carry theirs.

``convert_error`` is new in kind: a failed conversion is usually the
file's fault (password-protected, corrupt, an unsupported variant), and
the uploader needs to see why rather than a bare "failed".
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_attachment_convert"
down_revision: str | Sequence[str] | None = "0028_item_origin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "attachment_processing",
        sa.Column(
            "convert_status",
            sa.String(),
            nullable=False,
            server_default="untouched",
        ),
    )
    op.add_column(
        "attachment_processing",
        sa.Column("convert_engine", sa.String(), nullable=True),
    )
    op.add_column(
        "attachment_processing",
        sa.Column(
            "convert_completed_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "attachment_processing",
        sa.Column("convert_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("attachment_processing", "convert_error")
    op.drop_column("attachment_processing", "convert_completed_at")
    op.drop_column("attachment_processing", "convert_engine")
    op.drop_column("attachment_processing", "convert_status")
