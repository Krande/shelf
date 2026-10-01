import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import UUIDPK, Base, utcnow


class AuditEvent(UUIDPK, Base):
    """One thing someone did: who, what, to which object, in which space.

    Written in the same transaction as the change it describes, so a
    rolled-back request leaves no entry and a committed one always has
    one. Append-only — nothing updates or deletes these rows.

    The target is a type + id + label rather than a foreign key: the
    log's whole point is to outlive what it describes, and a trashed
    item that has since been purged still needs to read as something
    better than a dangling id. The actor's email is kept for the same
    reason — `actor_id` goes NULL if the account is ever deleted.
    """

    __tablename__ = "audit_events"

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_email: Mapped[str | None] = mapped_column(String, nullable=True)
    # Dotted, noun first — `item.trash`, `space.member.add` — so a prefix
    # filter (`item.`) selects a whole family.
    action: Mapped[str] = mapped_column(String, nullable=False)
    space_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True
    )
    target_type: Mapped[str | None] = mapped_column(String, nullable=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    target_label: Mapped[str | None] = mapped_column(String, nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # "web" for the cookie-session SPA, "api" for bearer-token clients.
    via: Mapped[str] = mapped_column(String, nullable=False, default="web")

    __table_args__ = (
        Index("ix_audit_events_created", "created_at", "id"),
        Index("ix_audit_events_space_created", "space_id", "created_at"),
        Index("ix_audit_events_actor_created", "actor_id", "created_at"),
        Index("ix_audit_events_target", "target_type", "target_id"),
        Index("ix_audit_events_action", "action"),
    )
