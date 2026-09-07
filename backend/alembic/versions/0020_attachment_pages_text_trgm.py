"""attachment_pages: pg_trgm GIN index on text

Revision ID: 0020_attachment_pages_text_trgm
Revises: 0019_collections_organize
Create Date: 2026-05-16

Fulltext scope runs ``attachment_pages.text ILIKE '%foo%'`` for every
candidate row — the existing tsvector GIN doesn't help because the
search path is deliberately substring-based (lexeme stemming was
surprising users when a Ctrl-F hit didn't show up in the library
search). On any non-trivial corpus that means a sequential scan
across every extracted PDF page.

A ``gin_trgm_ops`` GIN on the raw text column makes those unanchored
ILIKE lookups index-backed, the same trick we already use on
``items.data->>'title'`` (0017). The build is heavy — page bodies are
much larger than titles — but it's a one-shot cost and pays back
every fulltext query.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020_attachment_pages_text_trgm"
down_revision: str | Sequence[str] | None = "0019_collections_organize"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Extension is already present from 0017 but the IF NOT EXISTS
    # keeps this migration self-contained for a fresh-init.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_attachment_pages_text_trgm "
        "ON attachment_pages USING gin (text gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_attachment_pages_text_trgm")
