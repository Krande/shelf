import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import UUIDPK, Base, Timestamps, utcnow


class Space(UUIDPK, Timestamps, Base):
    __tablename__ = "spaces"

    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # The creator. Always holds the owner role, carries no membership row,
    # and cannot be demoted by editing space_memberships.
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)


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
