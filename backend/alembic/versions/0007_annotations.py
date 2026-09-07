"""annotations table

Revision ID: 0007_annotations
Revises: 0006_attachment_uploaded_at
Create Date: 2026-05-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0007_annotations"
down_revision: str | Sequence[str] | None = "0006_attachment_uploaded_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "annotations",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "attachment_id",
            sa.Uuid,
            sa.ForeignKey("attachments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column(
            "rects",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "color",
            sa.String(),
            nullable=False,
            server_default=sa.text("'#ffd400'"),
        ),
        sa.Column("text", sa.String(), nullable=True),
        sa.Column(
            "created_by",
            sa.Uuid,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_annotations_attachment_page",
        "annotations",
        ["attachment_id", "page_number"],
    )


def downgrade() -> None:
    op.drop_index("ix_annotations_attachment_page", table_name="annotations")
    op.drop_table("annotations")
