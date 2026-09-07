import uuid

from sqlalchemy import ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import UUIDPK, Base, Timestamps


class Note(UUIDPK, Timestamps, Base):
    """A rich-text note attached to an item.

    Two text columns: `content_html` is what the editor round-trips,
    `content_text` is a plain-text projection used to feed the GIN
    tsvector (`notes.tsv`, set up via the migration as a STORED
    generated column). Plain text is also handy for previews in lists.
    """

    __tablename__ = "notes"

    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), nullable=False
    )
    content_html: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (Index("ix_notes_item_updated", "item_id", "updated_at"),)
