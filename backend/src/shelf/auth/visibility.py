"""Who sees a note or a highlight.

Notes and annotations used to belong to the space that owns the item:
anyone who could read the PDF could read every note on it. That works
while a space is a team's own library. It stops working the moment one
document is read by everyone — a standard in a shared Standards space,
inherited by every project and every person — because then "everyone who
can read this PDF" is the whole company, and a working note to yourself
is published to it.

So each note and each annotation carries a `visibility`:

    private   the author, and nobody else
    space     everyone who can read the space that owns the item

The audience for a shared note is always the **item's** space, never the
space the reader came in through. You read a standard in your personal
shelf because it subscribes to Standards; sharing your note on it
publishes to Standards, where the other subscribers are. Publishing to
your personal space would be shouting into an empty room.

Two consequences worth stating plainly:

  * Sharing does not need write access. A subscriber is a viewer on the
    space that owns the standard, and being able to contribute a note
    back is the point of the feature, not a hole in it. What a viewer
    still cannot do is touch anyone else's note, or the item itself.

  * Creating a note does not need write access either, as long as it's
    private. Annotating a document you can only read is exactly the case
    this exists for.

Rows written before the column existed default to `space` with a null
author, which reads as "shared, authorship unknown" — the behaviour they
had, described honestly.
"""

import uuid

from sqlalchemy import ColumnElement, or_

from ..models import VISIBILITY_PRIVATE, VISIBILITY_SPACE, Annotation, Note


def default_visibility(*, inherited: bool) -> str:
    """What a new note or annotation should default to.

    Private when the item is only reachable through an inheritance link.
    Everywhere else the old behaviour stands: a note in a space you are
    actually a member of is shared with that space, because that is what
    people expect of a team library and changing it silently would hide
    notes colleagues rely on.
    """
    return VISIBILITY_PRIVATE if inherited else VISIBILITY_SPACE


def visible_notes(user_id: uuid.UUID) -> ColumnElement[bool]:
    """Filter for notes `user_id` may read, given they can read the item.

    Space-visible notes, plus their own private ones. Assumes the caller
    has already been authorized for the item — this narrows within an
    item, it does not decide access to it.
    """
    return or_(
        Note.visibility == VISIBILITY_SPACE,
        Note.author_id == user_id,
    )


def visible_annotations(user_id: uuid.UUID) -> ColumnElement[bool]:
    """The same rule for PDF markup. `created_by` is the author column
    annotations already had, so there is no second name for it."""
    return or_(
        Annotation.visibility == VISIBILITY_SPACE,
        Annotation.created_by == user_id,
    )
