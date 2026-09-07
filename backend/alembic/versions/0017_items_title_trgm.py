"""items: pg_trgm GIN index on data->>'title'

Revision ID: 0017_items_title_trgm
Revises: 0016_attachment_page_dims
Create Date: 2026-05-08

The library search runs ``data->>'title' ILIKE '%foo%'`` per query.
Without an index, that's a sequential JSONB scan — fine at a few
hundred items, painful past a couple thousand. ``pg_trgm``'s
``gin_trgm_ops`` operator class supports unanchored ILIKE/LIKE
directly on a raw text expression, so a GIN index over
``(data->>'title')`` makes any title substring lookup index-backed.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017_items_title_trgm"
down_revision: str | Sequence[str] | None = "0016_attachment_page_dims"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_items_title_trgm "
        "ON items USING gin ((data->>'title') gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_items_title_trgm")
    # Leave the extension in place — other indexes may depend on it
    # in the future and dropping it on rollback is an over-reach.
