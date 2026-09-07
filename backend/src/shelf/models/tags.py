import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from .base import UUIDPK, Base, Timestamps


class Tag(UUIDPK, Timestamps, Base):
    __tablename__ = "tags"

    space_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(CITEXT(), nullable=False)
    color: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (UniqueConstraint("space_id", "name", name="uq_tags_space_name"),)


class ItemTag(Base):
    __tablename__ = "item_tags"

    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )
