"""space inheritance, engineering standards, and note/annotation visibility

Revision ID: 0023_inherit_standards_vis
Revises: 0022_space_membership_roles
Create Date: 2026-09-16

Three related additions, in one revision because the feature needs all
three to mean anything:

1. `spaces.subscribable` + `space_inheritance` — a space can read another
   space's items without copying them. A project space inherits the
   shared Standards space; so can a personal space.

2. `standard_families` / `standard_revisions` / `space_standard_pins` —
   editions of one standard know they're the same standard, and a space
   can name the one it builds to.

3. `notes.visibility` / `annotations.visibility` (+ `notes.author_id`) —
   markup on an inherited document belongs to the person who wrote it,
   not to everyone who can see the document.

Every existing row keeps the behaviour it had: `subscribable` defaults
false (nothing inherits anything until someone says so) and `visibility`
defaults 'space' (notes and highlights in a shared space stay visible to
its members, exactly as before this revision).

`notes.author_id` backfills to NULL rather than guessing. A pre-existing
note has no recorded author, and inventing one would hand somebody a
private-note toggle over text they may not have written. NULL author +
'space' visibility reads as "shared, authorship unknown", which is the
truth.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Keep this under 32 characters: `alembic_version.version_num` is
# varchar(32), and a longer id creates every table successfully and then
# fails on the bookkeeping UPDATE at the end.
revision: str = "0023_inherit_standards_vis"
down_revision: str | Sequence[str] | None = "0022_space_membership_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── 1. Inheritance ──────────────────────────────────────────────────
    op.add_column(
        "spaces",
        sa.Column(
            "subscribable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_table(
        "space_inheritance",
        sa.Column("child_space_id", sa.Uuid(), nullable=False),
        sa.Column("parent_space_id", sa.Uuid(), nullable=False),
        sa.Column("added_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["child_space_id"], ["spaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["parent_space_id"], ["spaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["added_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("child_space_id", "parent_space_id"),
        sa.CheckConstraint(
            "child_space_id <> parent_space_id",
            name="ck_space_inheritance_not_self",
        ),
    )
    op.create_index(
        "ix_space_inheritance_parent", "space_inheritance", ["parent_space_id"]
    )

    # ── 2. Standards ────────────────────────────────────────────────────
    op.create_table(
        "standard_families",
        sa.Column(
            "id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("body", postgresql_citext(), nullable=False),
        sa.Column("designation", postgresql_citext(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("body", "designation", name="uq_standard_families_key"),
    )
    op.create_table(
        "standard_revisions",
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("issued_on", sa.Date(), nullable=True),
        sa.Column(
            "superseded", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        # RESTRICT: dropping a family out from under its revisions would
        # orphan every pin that names it.
        sa.ForeignKeyConstraint(
            ["family_id"], ["standard_families.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("item_id"),
    )
    op.create_index(
        "ix_standard_revisions_family_issued",
        "standard_revisions",
        ["family_id", "issued_on"],
    )
    op.create_table(
        "space_standard_pins",
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("pinned_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["space_id"], ["spaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["family_id"], ["standard_families.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pinned_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("space_id", "family_id"),
    )

    # ── 3. Visibility ───────────────────────────────────────────────────
    op.add_column(
        "notes", sa.Column("author_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "fk_notes_author_id_users",
        "notes",
        "users",
        ["author_id"],
        ["id"],
        ondelete="SET NULL",
    )
    for table in ("notes", "annotations"):
        op.add_column(
            table,
            sa.Column(
                "visibility",
                sa.String(),
                nullable=False,
                server_default="space",
            ),
        )
        op.create_check_constraint(
            f"ck_{table}_visibility",
            table,
            "visibility IN ('private', 'space')",
        )
    op.create_index(
        "ix_notes_author_visibility", "notes", ["author_id", "visibility"]
    )
    op.create_index(
        "ix_annotations_created_by_visibility",
        "annotations",
        ["created_by", "visibility"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_annotations_created_by_visibility", table_name="annotations"
    )
    op.drop_index("ix_notes_author_visibility", table_name="notes")
    for table in ("annotations", "notes"):
        op.drop_constraint(f"ck_{table}_visibility", table, type_="check")
        op.drop_column(table, "visibility")
    op.drop_constraint("fk_notes_author_id_users", "notes", type_="foreignkey")
    op.drop_column("notes", "author_id")

    op.drop_table("space_standard_pins")
    op.drop_index(
        "ix_standard_revisions_family_issued", table_name="standard_revisions"
    )
    op.drop_table("standard_revisions")
    op.drop_table("standard_families")

    op.drop_index("ix_space_inheritance_parent", table_name="space_inheritance")
    op.drop_table("space_inheritance")
    op.drop_column("spaces", "subscribable")


def postgresql_citext() -> sa.types.TypeEngine[str]:
    """CITEXT, matching how `users.email` and `tags.name` are declared.

    A local helper rather than a module-level import so the revision
    reads top-down; the extension itself is created in 0001.
    """
    from sqlalchemy.dialects.postgresql import CITEXT

    return CITEXT()
