"""spaces + collections: profile (description, default library columns)

Revision ID: 0026_space_collection_profiles
Revises: 0025_attachment_sha256
Create Date: 2026-09-30

A profile says what a space or a collection is for — a description, and
which columns its library table shows by default. A Standards space wants
Designation and Edition where a paper library wants Creator.

`columns` is NULL when nothing is set, which is what makes inheritance
work: a collection without its own list takes its parent's, then its
space's, then the built-in default. An empty list would be a (useless)
setting of its own, so the API never stores one.

Collections already carry a description; spaces gain one here.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0026_space_collection_profiles"
down_revision: str | Sequence[str] | None = "0025_attachment_sha256"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("spaces", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("spaces", sa.Column("columns", JSONB(), nullable=True))
    op.add_column("collections", sa.Column("columns", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("collections", "columns")
    op.drop_column("spaces", "columns")
    op.drop_column("spaces", "description")
