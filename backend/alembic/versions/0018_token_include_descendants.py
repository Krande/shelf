"""api_tokens: add include_descendants flag

Revision ID: 0018_token_include_descendants
Revises: 0017_items_title_trgm
Create Date: 2026-05-10

When a token is collection-scoped via ``allowed_collection_ids``, this
flag turns the list into "these collections and everything nested
below them" — resolved at request time so collections added later are
covered automatically. False (default) keeps the historical exact-match
semantics.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_token_include_descendants"
down_revision: str | Sequence[str] | None = "0017_items_title_trgm"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "api_tokens",
        sa.Column(
            "include_descendants",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("api_tokens", "include_descendants")
