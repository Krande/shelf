"""Finding documents by what they are rather than by this instance's ids.

Item and attachment ids differ per instance, so anything outside the
library — a profile importer, a link in another tool — names a document
by one of two portable keys instead:

- the **file's SHA-256**, which is the same wherever the bytes are, and
- a **standard edition** — `(body, designation, label)` — which survives
  a different download of the same document (publishers watermark each
  copy, which changes the hash).

Three routes look these up: the token-facing `/api/v1/attachments/resolve`
and `/api/v1/standards/resolve`, and the session-facing
`/api/attachments/resolve` behind the SPA's `/open` links. They differ in
who the caller is and so in which spaces are reachable; everything after
that — narrowing to one space, the match itself, what is left out, the
order, the cap — lives here so the three cannot drift apart.

Every query is bounded by the `reachable` spaces the caller passes in,
so these answer "can *I* see this document", never "does anyone have it".
"""

import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import ColumnElement, Select, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Attachment, Item, Space, StandardFamily, StandardRevision

# Upper bound on matches per lookup. One document normally resolves to a
# handful of rows (its copies across spaces), and every caller either
# takes the first or refuses to guess between several, so anything past
# this is noise that only costs a larger response.
RESOLVE_LIMIT = 50

SHA256_PATTERN = r"^[0-9a-fA-F]{64}$"
BODY_MAX_LENGTH = 120
DESIGNATION_MAX_LENGTH = 200
LABEL_MAX_LENGTH = 120
# Matches the longest slug `api/spaces.py` will create.
SPACE_SLUG_MAX_LENGTH = 64

SpaceSlugQuery = Annotated[str | None, Query(max_length=SPACE_SLUG_MAX_LENGTH)]

# Either a subquery of space ids (session callers) or concrete ids (token
# callers, already narrowed by the token's allow-list).
Reachable = Select[tuple[uuid.UUID]] | Sequence[uuid.UUID]


class AttachmentMatch(BaseModel):
    attachment_id: uuid.UUID
    filename: str
    item_id: uuid.UUID
    space_id: uuid.UUID


class AttachmentResolveResponse(BaseModel):
    """Possibly empty: a file the caller can't see is not an error."""

    attachments: list[AttachmentMatch]


async def narrow_to_space(
    db: AsyncSession, reachable: Reachable, slug: str | None
) -> Reachable:
    """`reachable`, or only the space named `slug` when one is given.

    A slug the caller can't reach is the same 404 as one that doesn't
    exist, so the parameter can't be used to probe for private spaces.
    """
    if slug is None:
        return reachable
    target = (
        await db.execute(
            select(Space.id).where(Space.slug == slug, Space.id.in_(reachable))
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Space not found")
    return [target]


def _edition_filter(
    body: str, designation: str, label: str
) -> list[ColumnElement[bool]]:
    """Body and designation are CITEXT, so this is the same
    case-insensitive match filing a revision uses; the label is matched
    as written."""
    return [
        StandardFamily.body == body.strip(),
        StandardFamily.designation == designation.strip(),
        StandardRevision.label == label.strip(),
    ]


def _is_pdf_attachment() -> ColumnElement[bool]:
    """SQL twin of `conversion.is_pdf`, loosened to "declared a PDF, or
    named like one" — it only orders results, never decides access."""
    return or_(
        func.lower(Attachment.content_type).startswith("application/pdf"),
        func.lower(Attachment.filename).endswith(".pdf"),
    )


def _attachments_in(
    reachable: Reachable, *, completed_only: bool
) -> Select[tuple[uuid.UUID, str, uuid.UUID, uuid.UUID]]:
    stmt = (
        select(Attachment.id, Attachment.filename, Item.id, Item.space_id)
        .join(Item, Item.id == Attachment.item_id)
        .where(Item.deleted_at.is_(None), Item.space_id.in_(reachable))
    )
    if completed_only:
        stmt = stmt.where(Attachment.uploaded_at.is_not(None))
    return stmt


async def _attachment_matches(
    db: AsyncSession, stmt: Select[tuple[uuid.UUID, str, uuid.UUID, uuid.UUID]]
) -> list[AttachmentMatch]:
    rows = (await db.execute(stmt.limit(RESOLVE_LIMIT))).all()
    return [
        AttachmentMatch(
            attachment_id=att_id, filename=filename, item_id=item_id, space_id=space_id
        )
        for att_id, filename, item_id, space_id in rows
    ]


async def attachments_by_sha256(
    db: AsyncSession, reachable: Reachable, sha256: str, *, completed_only: bool
) -> list[AttachmentMatch]:
    """Attachments carrying exactly these bytes, oldest first, so a link
    keeps opening the same file when a copy is made later.

    `completed_only` leaves out presigned uploads whose bytes haven't
    arrived. A reader wants that — there is nothing to open yet. An
    importer asking "have I pushed this already" does not: the declared
    hash is its answer, and leaving the row out would push it twice.
    """
    stmt = (
        _attachments_in(reachable, completed_only=completed_only)
        .where(Attachment.sha256 == sha256.lower())
        .order_by(Attachment.created_at, Attachment.id)
    )
    return await _attachment_matches(db, stmt)


async def attachments_by_edition(
    db: AsyncSession,
    reachable: Reachable,
    body: str,
    designation: str,
    label: str,
    *,
    completed_only: bool,
) -> list[AttachmentMatch]:
    """Attachments of the items that are this edition of a standard.

    Oldest edition item first; within one, a PDF before the other files
    it carries (a cover letter, a source .docx), since a PDF is what the
    reader opens without converting.
    """
    stmt = (
        _attachments_in(reachable, completed_only=completed_only)
        .join(StandardRevision, StandardRevision.item_id == Item.id)
        .join(StandardFamily, StandardFamily.id == StandardRevision.family_id)
        .where(*_edition_filter(body, designation, label))
        .order_by(
            Item.created_at,
            case((_is_pdf_attachment(), 0), else_=1),
            Attachment.created_at,
            Attachment.id,
        )
    )
    return await _attachment_matches(db, stmt)


async def edition_items(
    db: AsyncSession, reachable: Reachable, body: str, designation: str, label: str
) -> list[tuple[uuid.UUID, uuid.UUID, str]]:
    """`(item_id, space_id, label)` of the items that are this edition,
    oldest first. The same edition legitimately lives in more than one
    space — copying puts it there — so this is a list, not one row."""
    rows = (
        await db.execute(
            select(Item.id, Item.space_id, StandardRevision.label)
            .join(StandardRevision, StandardRevision.item_id == Item.id)
            .join(StandardFamily, StandardFamily.id == StandardRevision.family_id)
            .where(
                *_edition_filter(body, designation, label),
                Item.deleted_at.is_(None),
                Item.space_id.in_(reachable),
            )
            .order_by(Item.created_at, Item.id)
            .limit(RESOLVE_LIMIT)
        )
    ).all()
    return [(item_id, space_id, row_label) for item_id, space_id, row_label in rows]
