import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import UUIDPK, Base, Timestamps

# Who a note or annotation is for. `space` means everyone who can read
# the space that owns the item — the behaviour everything had before
# there was a column. `private` means the author alone.
#
# The audience for a shared note is always the item's own space, not the
# space the reader came in through. That's the point for an inherited
# standard: you read it from your personal space, and sharing your note
# on it publishes to the Standards space, where the other subscribers
# are — not to your personal space, where nobody is.
VISIBILITY_PRIVATE = "private"
VISIBILITY_SPACE = "space"
VISIBILITIES = (VISIBILITY_PRIVATE, VISIBILITY_SPACE)


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
    # Null for rows written before the column existed, and for anything
    # created by a bearer token rather than a person.
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Defaults to `space` at the database level so existing rows and any
    # writer that doesn't know about the column keep the old behaviour.
    # The API picks the default per note instead: private when the item
    # is only reachable through an inheritance link, shared otherwise.
    visibility: Mapped[str] = mapped_column(
        String, nullable=False, default=VISIBILITY_SPACE, server_default=VISIBILITY_SPACE
    )

    __table_args__ = (
        CheckConstraint(
            "visibility IN ('private', 'space')", name="ck_notes_visibility"
        ),
        Index("ix_notes_item_updated", "item_id", "updated_at"),
        # "My private notes on this item" is on the read path for every
        # item detail render, and the visibility filter is what it leads
        # with.
        Index("ix_notes_author_visibility", "author_id", "visibility"),
    )
