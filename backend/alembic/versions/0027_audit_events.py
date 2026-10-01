"""audit_events: who did what, to what, in which space

Revision ID: 0027_audit_events
Revises: 0026_space_collection_profiles
Create Date: 2026-10-01

An append-only log behind the admin "Audit log" tab: sharing changes,
uploads, downloads and edits. Rows are written in the same transaction
as the change they describe.

Purely additive — a new table nothing older reads — so a pod still on
the previous build runs fine against it during a rolling upgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0027_audit_events"
down_revision: str | Sequence[str] | None = "0026_space_collection_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "actor_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("actor_email", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column(
            "space_id",
            sa.Uuid(),
            sa.ForeignKey("spaces.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("target_type", sa.String(), nullable=True),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("target_label", sa.String(), nullable=True),
        sa.Column("details", JSONB(), nullable=True),
        sa.Column("via", sa.String(), nullable=False, server_default="web"),
    )
    op.create_index("ix_audit_events_created", "audit_events", ["created_at", "id"])
    op.create_index(
        "ix_audit_events_space_created", "audit_events", ["space_id", "created_at"]
    )
    op.create_index(
        "ix_audit_events_actor_created", "audit_events", ["actor_id", "created_at"]
    )
    op.create_index(
        "ix_audit_events_target", "audit_events", ["target_type", "target_id"]
    )
    op.create_index("ix_audit_events_action", "audit_events", ["action"])


def downgrade() -> None:
    op.drop_table("audit_events")
