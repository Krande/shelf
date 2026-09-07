"""initial schema: extensions, users, identities, spaces, space_memberships

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import CITEXT

revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute("CREATE EXTENSION IF NOT EXISTS fuzzystrmatch")

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("email", CITEXT(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "identities",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idp", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.UniqueConstraint("idp", "subject", name="uq_identities_idp_subject"),
    )

    op.create_table(
        "spaces",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("owner_id", sa.Uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("slug", name="uq_spaces_slug"),
    )

    op.create_table(
        "space_memberships",
        sa.Column(
            "space_id",
            sa.Uuid,
            sa.ForeignKey("spaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.Uuid,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("space_memberships")
    op.drop_table("spaces")
    op.drop_table("identities")
    op.drop_table("users")
