import uuid
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import UUIDPK, Base, Timestamps
from .notes import VISIBILITY_SPACE


class AnnotationKind(StrEnum):
    """Coarse type of annotation. `highlight` carries text rects on
    the page; `note` is a single pin (a 1x1 rect). Pen / image
    annotations can land in a future migration when they have a
    concrete need."""

    highlight = "highlight"
    note = "note"


class Annotation(UUIDPK, Timestamps, Base):
    """A user-created markup on a PDF attachment.

    Coordinates are stored in PDF user-space (origin bottom-left,
    independent of render scale) so they survive zoom changes + DPI
    differences across viewer instances.
    """

    __tablename__ = "annotations"

    attachment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("attachments.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # `rects` is a list of [x, y, w, h] tuples in PDF user-space —
    # one entry per visible quad for highlights spanning multiple
    # text runs; a single 1x1 rect for note pins.
    rects: Mapped[list[list[float]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    color: Mapped[str] = mapped_column(String, nullable=False, default="#ffd400")
    text: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Same two values and the same rule as Note.visibility: `space` is
    # everyone who can read the item's space, `private` is the author
    # alone. Highlights on a shared standard are markup on someone else's
    # document, so the API defaults them to private when the PDF is only
    # reachable through an inheritance link.
    visibility: Mapped[str] = mapped_column(
        String, nullable=False, default=VISIBILITY_SPACE, server_default=VISIBILITY_SPACE
    )

    __table_args__ = (
        CheckConstraint(
            "visibility IN ('private', 'space')", name="ck_annotations_visibility"
        ),
        Index("ix_annotations_attachment_page", "attachment_id", "page_number"),
        Index("ix_annotations_created_by_visibility", "created_by", "visibility"),
    )
