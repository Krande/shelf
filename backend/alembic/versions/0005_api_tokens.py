"""api_tokens table

Revision ID: 0005_api_tokens
Revises: 0004_attachments
Create Date: 2026-05-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0005_api_tokens"
down_revision: str | Sequence[str] | None = "0004_attachments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False, unique=True),
        sa.Column("prefix", sa.String(), nullable=False),
        sa.Column("scopes", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("allowed_collection_ids", JSONB(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_api_tokens_user", "api_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_api_tokens_user", table_name="api_tokens")
    op.drop_table("api_tokens")
