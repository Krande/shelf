"""Per-item rich-text notes.

Each note carries TipTap-authored HTML alongside a plain-text twin
that powers the `tsv` column. The plain text is extracted server-side
from the HTML — relying on the client to send both invites drift; the
canonical text projection always comes from the same place.

Notes have an author and an audience — see `auth/visibility.py`. The
short version: writing a note needs only read access to the item, the
note is private when the item is inherited, and the author alone decides
whether to share it with the space that owns the item.
"""

import re
import uuid
from datetime import UTC, datetime
from html import unescape
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import get_current_user
from ..auth.spaces import (
    SPACE_ROLE_EDITOR,
    SPACE_ROLE_VIEWER,
    effective_role,
    rank,
    require_space_role,
)
from ..auth.visibility import default_visibility, visible_notes
from ..db import get_session
from ..models import VISIBILITY_SPACE, Item, Note, Space, User

router = APIRouter(tags=["notes"])


_BLOCK_BREAK_TAGS = re.compile(
    r"</(?:p|div|li|h[1-6]|blockquote|br)\s*>", re.IGNORECASE
)
_TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(html: str) -> str:
    """Cheap, dependency-free HTML → plain text. Block-level closers
    become newlines so list items / paragraphs stay separated, then
    everything between angle brackets is stripped and entities
    decoded. Good enough to feed into to_tsvector and to render as a
    list-row preview; not a sanitizer."""
    if not html:
        return ""
    s = _BLOCK_BREAK_TAGS.sub("\n", html)
    # Self-closing <br> variants the regex above missed.
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = _TAG_RE.sub("", s)
    s = unescape(s)
    # Collapse runs of blank lines but keep single line breaks so
    # list structure survives.
    s = re.sub(r"\n[ \t]*\n+", "\n\n", s)
    return s.strip()


class NoteCreate(BaseModel):
    content_html: str = Field(default="", max_length=200_000)
    # Omitted means "decide for me": private on an inherited item, shared
    # with the space otherwise.
    visibility: Literal["private", "space"] | None = None


class NoteUpdate(BaseModel):
    content_html: str = Field(default="", max_length=200_000)


class NoteVisibilityUpdate(BaseModel):
    visibility: Literal["private", "space"]


class NoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_id: uuid.UUID
    content_html: str
    content_text: str
    created_at: datetime
    updated_at: datetime
    author_id: uuid.UUID | None = None
    visibility: str = VISIBILITY_SPACE
    # Whether the caller wrote this note, so the SPA can offer the share
    # toggle without comparing ids itself. Not persisted.
    is_mine: bool = False


def _to_response(note: Note, user_id: uuid.UUID) -> NoteResponse:
    return NoteResponse(
        id=note.id,
        item_id=note.item_id,
        content_html=note.content_html,
        content_text=note.content_text,
        created_at=note.created_at,
        updated_at=note.updated_at,
        author_id=note.author_id,
        visibility=note.visibility,
        is_mine=note.author_id == user_id,
    )


async def _resolve_item(
    db: AsyncSession,
    user: User,
    item_id: uuid.UUID,
    minimum: str = SPACE_ROLE_VIEWER,
) -> tuple[Item, str, bool]:
    """`(item, role, inherited)` for an item the caller may reach."""
    item = await db.get(Item, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    space = await db.get(Space, item.space_id)
    role = await require_space_role(
        db, space, user.id, minimum, label="Item not found"
    )
    assert space is not None
    _, inherited = await effective_role(db, space, user.id)
    return item, role, inherited


async def _resolve_note(
    db: AsyncSession, user: User, note_id: uuid.UUID
) -> tuple[Note, str]:
    """A note the caller may read, with their role in the item's space.

    A private note belonging to someone else is a 404, not a 403: it is
    not theirs to know about.
    """
    note = await db.get(Note, note_id)
    if note is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Note not found")
    item = await db.get(Item, note.item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Note not found")
    space = await db.get(Space, item.space_id)
    role = await require_space_role(
        db, space, user.id, SPACE_ROLE_VIEWER, label="Note not found"
    )
    if note.visibility != VISIBILITY_SPACE and note.author_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Note not found")
    return note, role


def _require_can_edit(note: Note, user: User, role: str) -> None:
    """The author may always change their own note.

    Otherwise it takes editor on the space, which covers tidying up the
    author-less rows that predate the column. A shared note is still
    somebody's writing, so this is deliberately not "any editor may edit
    anyone's note" for private ones — those never get here, because
    `_resolve_note` has already 404'd them.
    """
    if note.author_id is not None and note.author_id == user.id:
        return
    if rank(role) >= rank(SPACE_ROLE_EDITOR):
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN, "That note belongs to someone else"
    )


@router.get(
    "/api/items/{item_id}/notes", response_model=list[NoteResponse]
)
async def list_notes(
    item_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[NoteResponse]:
    """Notes shared with the item's space, plus the caller's own."""
    await _resolve_item(db, user, item_id)
    rows = await db.execute(
        select(Note)
        .where(Note.item_id == item_id, visible_notes(user.id))
        .order_by(Note.updated_at.desc(), Note.id)
    )
    return [_to_response(n, user.id) for n in rows.scalars().all()]


@router.post(
    "/api/items/{item_id}/notes",
    response_model=NoteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_note(
    item_id: uuid.UUID,
    payload: NoteCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> NoteResponse:
    """Write a note on any item the caller can read.

    Viewer is enough — annotating a document you can only read is the
    case this exists for. What varies is the audience: on an inherited
    item the note starts private, and the author shares it deliberately.
    """
    _, _role, inherited = await _resolve_item(db, user, item_id)
    note = Note(
        item_id=item_id,
        content_html=payload.content_html,
        content_text=html_to_text(payload.content_html),
        author_id=user.id,
        visibility=payload.visibility
        or default_visibility(inherited=inherited),
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return _to_response(note, user.id)


@router.put("/api/notes/{note_id}", response_model=NoteResponse)
async def update_note(
    note_id: uuid.UUID,
    payload: NoteUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> NoteResponse:
    note, role = await _resolve_note(db, user, note_id)
    _require_can_edit(note, user, role)
    note.content_html = payload.content_html
    note.content_text = html_to_text(payload.content_html)
    note.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(note)
    return _to_response(note, user.id)


@router.patch("/api/notes/{note_id}/visibility", response_model=NoteResponse)
async def set_note_visibility(
    note_id: uuid.UUID,
    payload: NoteVisibilityUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> NoteResponse:
    """Share this note with the item's space, or take it back.

    The author's call alone — not an editor's, and not the space owner's.
    Sharing is publishing your own writing; nobody else gets to publish
    it for you, and nobody else gets to retract it either.

    A note with no recorded author (written before authorship existed) is
    already shared and has nobody who could change that, so it stays put.
    """
    note, _role = await _resolve_note(db, user, note_id)
    if note.author_id is None or note.author_id != user.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only the note's author can change who sees it",
        )
    note.visibility = payload.visibility
    note.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(note)
    return _to_response(note, user.id)


@router.delete(
    "/api/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_note(
    note_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    note, role = await _resolve_note(db, user, note_id)
    _require_can_edit(note, user, role)
    await db.delete(note)
    await db.commit()
