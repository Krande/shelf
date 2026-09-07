"""collections: description + position columns

Revision ID: 0019_collections_organize
Revises: 0018_token_include_descendants
Create Date: 2026-05-14

Two additions to support user-driven reorganization of collections:

* ``description`` — optional free-form text shown alongside the
  collection name (in the page header when the collection is active).
* ``position`` — integer per (space_id, parent_id) controlling sibling
  order in the rail. Dense from 0, server renumbers on insert / move.

Existing rows get a deterministic backfill: alphabetical-by-name within
each (space_id, parent_id) group.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_collections_organize"
down_revision: str | Sequence[str] | None = "0018_token_include_descendants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "collections",
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.add_column(
        "collections",
        sa.Column(
            "position",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    # Backfill: alphabetical-by-name positions within each (space_id,
    # parent_id) group. Window function does this in a single statement.
    # Note: NULL parent_id rows are grouped together correctly by
    # PARTITION BY because NULLs compare equal under partitioning.
    op.execute(
        """
        UPDATE collections AS c
        SET position = sub.rn - 1
        FROM (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY space_id, parent_id ORDER BY name
            ) AS rn
            FROM collections
        ) AS sub
        WHERE c.id = sub.id
        """
    )


def downgrade() -> None:
    op.drop_column("collections", "position")
    op.drop_column("collections", "description")
