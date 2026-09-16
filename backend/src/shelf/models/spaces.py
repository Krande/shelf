import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import UUIDPK, Base, Timestamps, utcnow


class Space(UUIDPK, Timestamps, Base):
    __tablename__ = "spaces"

    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # The creator. Always holds the owner role, carries no membership row,
    # and cannot be demoted by editing space_memberships.
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    # Whether other spaces may subscribe to this one (see SpaceInheritance).
    # Off by default: inheriting grants read of this space's items to
    # everyone who can read the subscriber, so the owner opts in once
    # rather than approving each subscription. Turning it back off stops
    # new subscriptions; existing ones are listed so the owner can drop
    # them deliberately rather than having access vanish by side effect.
    subscribable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )


class SpaceMembership(Base):
    __tablename__ = "space_memberships"
    __table_args__ = (
        CheckConstraint(
            "role IN ('viewer', 'editor')", name="ck_space_memberships_role"
        ),
        # "Which spaces am I a member of" runs on every space listing; the
        # composite PK leads with space_id and so doesn't serve it.
        Index("ix_space_memberships_user_id", "user_id"),
    )

    space_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("spaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # 'viewer' or 'editor' — see auth/spaces.py. Owner is deliberately not
    # storable here.
    role: Mapped[str] = mapped_column(String, nullable=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class SpaceInheritance(Base):
    """`child_space_id` subscribes to `parent_space_id`.

    The child's library gains the parent's items, read-only: a project
    space inherits the shared "Standards" space, and a personal space can
    subscribe to the same thing. Nothing is copied — there is one row per
    standard in the instance, and the subscribers read it where it lives.

    **This is a read grant.** Everyone who can read the child can read the
    parent's items through it, which is why creating a row needs owner on
    the child *and* `Space.subscribable` set on the parent.

    Deliberately not transitive: if A inherits B and B inherits C, A does
    not see C. One hop is what the model promises, so a space owner can
    answer "who can see my items" by reading one table rather than
    chasing a graph. The composite PK makes a subscription idempotent,
    and the CHECK keeps a space from inheriting itself.
    """

    __tablename__ = "space_inheritance"
    __table_args__ = (
        CheckConstraint(
            "child_space_id <> parent_space_id", name="ck_space_inheritance_not_self"
        ),
        # "Who subscribes to this space" — the owner-facing listing, which
        # the composite PK (leading with child) doesn't serve.
        Index("ix_space_inheritance_parent", "parent_space_id"),
    )

    child_space_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("spaces.id", ondelete="CASCADE"), primary_key=True
    )
    parent_space_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("spaces.id", ondelete="CASCADE"), primary_key=True
    )
    added_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
