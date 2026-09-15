"""users: role column

Revision ID: 0021_user_role
Revises: 0020_attachment_pages_text_trgm
Create Date: 2026-09-15

Shelf had no global role until now — every authorization check was
"does the caller own the space that owns this row?". That covers the
per-user library but leaves nowhere to hang instance-wide operations
(listing users, handing out roles).

``role`` is deliberately not called ``scopes``: ``api_tokens.scopes``
already owns that word for per-token bearer permissions, and having two
different things named scope reads badly at the call site. A TEXT
column with a CHECK also leaves room for a third role later without
another migration, which an ``is_admin`` boolean would not.

Existing rows default to 'user'. The first admin comes from
``SHELF_ADMIN_EMAILS`` on next login, or ``pixi run grant-admin``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_user_role"
down_revision: str | Sequence[str] | None = "0020_attachment_pages_text_trgm"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.Text(), nullable=False, server_default="user"),
    )
    op.create_check_constraint(
        "ck_users_role",
        "users",
        "role IN ('admin', 'user')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.drop_column("users", "role")
