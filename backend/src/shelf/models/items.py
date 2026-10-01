import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import UUIDPK, Base, Timestamps


class Item(UUIDPK, Timestamps, Base):
    __tablename__ = "items"

    space_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False
    )
    item_type: Mapped[str] = mapped_column(String, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True
    )
    # The item this one was imported from (see archive_format), followed
    # back to the first: an archive import uses it to recognise a
    # document it has already brought in, so importing the same archive
    # twice -- or finishing an interrupted import -- doesn't duplicate.
    # Not a foreign key: the source usually lives on another instance.
    origin_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    __table_args__ = (
        Index("ix_items_space_updated", "space_id", "updated_at"),
        Index("ix_items_space_origin", "space_id", "origin_id"),
    )
