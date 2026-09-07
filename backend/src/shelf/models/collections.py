import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import UUIDPK, Base, Timestamps


class Collection(UUIDPK, Timestamps, Base):
    """A folder inside a Space.

    Collections form a tree via `parent_id`; root collections have
    parent_id = NULL. An item can belong to many collections (the join
    table `item_collections`). Deleting a collection unlinks the items
    it held but leaves the items themselves untouched — matches Zotero's
    semantics and is what the UI signals.

    ``position`` is a dense 0-based sibling order within (space_id,
    parent_id). The API renumbers it on insert / move so the value
    always matches the rail's render order.
    """

    __tablename__ = "collections"

    space_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    __table_args__ = (
        Index("ix_collections_space_parent", "space_id", "parent_id"),
    )


class ItemCollection(Base):
    __tablename__ = "item_collections"

    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    collection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True
    )
