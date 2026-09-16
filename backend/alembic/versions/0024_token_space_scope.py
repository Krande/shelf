"""api_tokens: optional per-space allow-list

Revision ID: 0024_token_space_scope
Revises: 0023_inherit_standards_vis
Create Date: 2026-09-16

Tokens could already be narrowed to a set of collections; this adds the
coarser cut people actually reach for first — "this token is for the
project space, and nothing else".

NULL means unrestricted, which is what every existing row gets, so no
token changes behaviour on upgrade. The column holds space ids rather
than slugs because slugs are renameable as of 0023's companion change;
an allow-list keyed on a name would stop matching the day someone fixed
a typo.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024_token_space_scope"
down_revision: str | Sequence[str] | None = "0023_inherit_standards_vis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "api_tokens",
        sa.Column("allowed_space_ids", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("api_tokens", "allowed_space_ids")
