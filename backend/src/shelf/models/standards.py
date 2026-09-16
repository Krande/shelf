"""Engineering standards and their revisions.

An engineering standard is republished over time, and which edition
applies is a decision a project makes deliberately. That needs two things
the plain item model can't express: revisions of one standard have to
know they're the same standard, and a space has to be able to say which
of them it uses.

`StandardFamily` is the standard itself (say, "ACME 1234"), independent
of any edition. `StandardRevision` attaches one item to one family — the
item carries the PDF, the title, the metadata; the revision row carries
only what orders it against its siblings.

Families are instance-wide rather than space-scoped on purpose. A given
standard is the same standard whichever space holds a copy, and a project
pinning a revision needs to name a family that the Standards space also
recognises. The trade is that two spaces holding their own copy of the
same edition both point at one family, which is what makes the revision
dropdown work across an inheritance link.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from .base import UUIDPK, Base, Timestamps, utcnow


class StandardFamily(UUIDPK, Timestamps, Base):
    """One standard, across every edition of it."""

    __tablename__ = "standard_families"
    __table_args__ = (
        # CITEXT on both halves so a body or designation typed with
        # different capitalisation lands on the same family instead of
        # quietly forking the revision history in two.
        UniqueConstraint("body", "designation", name="uq_standard_families_key"),
    )

    # Whoever publishes it. Free text rather than an enum — the list of
    # bodies an engineering office cares about is long, changes, and
    # isn't ours to fix.
    body: Mapped[str] = mapped_column(CITEXT(), nullable=False)
    # The designation without an edition — "ACME 1234", "ACME-RP-7". The
    # edition lives on the revision.
    designation: Mapped[str] = mapped_column(CITEXT(), nullable=False)
    # Human title, for the picker.
    title: Mapped[str | None] = mapped_column(Text, nullable=True)


class StandardRevision(Base):
    """One item, as one edition of one family.

    `item_id` is the primary key: an item is a revision of at most one
    standard. Deleting the item takes the revision row with it; deleting
    the family is blocked while revisions point at it (RESTRICT), since
    that would silently orphan every pin.
    """

    __tablename__ = "standard_revisions"
    __table_args__ = (
        # The revision dropdown: every sibling of a family, in order.
        Index("ix_standard_revisions_family_issued", "family_id", "issued_on"),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    family_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_families.id", ondelete="RESTRICT"), nullable=False
    )
    # What this edition is called: "2020", "Rev. 5", "Ed. 3 Amd. 1".
    # Display only — ordering is `issued_on`, because label formats vary
    # by body and don't sort sensibly against each other.
    label: Mapped[str] = mapped_column(String, nullable=False)
    # Publication date. Nullable because a PDF sometimes doesn't say, and
    # a revision with an unknown date is still worth recording — it just
    # sorts last and can never be "latest". Ordering nulls last is what
    # keeps an undated import from displacing a dated current edition.
    issued_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Explicitly withdrawn or superseded by the issuing body. Excluded
    # from "latest" regardless of date, so a withdrawn late amendment
    # doesn't get recommended over the current edition.
    superseded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class SpaceStandardPin(Base):
    """The revision of one family that one space treats as its own.

    A project space pins "we build to the 2018 edition of ACME 1234", and
    its library stops showing the four other editions it inherits from
    the Standards space unless someone asks for them. The pin is per
    space, so the Standards space itself stays the complete record.

    `item_id` is not constrained to the family here — the API checks it,
    because the useful error is "that item isn't a revision of that
    standard" rather than an integrity violation.
    """

    __tablename__ = "space_standard_pins"

    space_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("spaces.id", ondelete="CASCADE"), primary_key=True
    )
    family_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_families.id", ondelete="CASCADE"), primary_key=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), nullable=False
    )
    pinned_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
