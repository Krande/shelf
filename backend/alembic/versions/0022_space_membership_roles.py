"""space_memberships: constrain role, add added_at and a user index

Revision ID: 0022_space_membership_roles
Revises: 0021_user_role
Create Date: 2026-09-15

The table has existed since 0001 but nothing ever read or wrote it —
every permission check went through Space.owner_id instead. Activating it
means pinning down what `role` is allowed to contain.

'viewer' and 'editor' only. Owner is not storable: it belongs to the
creator through Spaces.owner_id, and a second owner would have no meaning
the code could act on.

The UPDATE before the CHECK is belt-and-braces. The table should be empty
on every existing instance, but a hand-inserted row with some other value
would otherwise fail the constraint and abort the migration, which is a
poor trade for a column nothing was reading.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_space_membership_roles"
down_revision: str | Sequence[str] | None = "0021_user_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "space_memberships",
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.execute(
        "UPDATE space_memberships SET role = 'viewer' "
        "WHERE role NOT IN ('viewer', 'editor')"
    )
    op.create_check_constraint(
        "ck_space_memberships_role",
        "space_memberships",
        "role IN ('viewer', 'editor')",
    )
    op.create_index(
        "ix_space_memberships_user_id", "space_memberships", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_space_memberships_user_id", table_name="space_memberships")
    op.drop_constraint(
        "ck_space_memberships_role", "space_memberships", type_="check"
    )
    op.drop_column("space_memberships", "added_at")
