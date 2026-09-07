"""Per-item rich-text notes.

Each note carries TipTap-authored HTML alongside a plain-text twin
that powers the `tsv` column. The plain text is extracted server-side
from the HTML — relying on the client to send both invites drift; the
canonical text projection always comes from the same place.
"""

import re
import uuid
from datetime import UTC, datetime
from html import unescape
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import get_current_user
from ..db import get_session
from ..models import Item, Note, Space, User

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


class NoteUpdate(BaseModel):
    content_html: str = Field(default="", max_length=200_000)


class NoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_id: uuid.UUID
    content_html: str
    content_text: str
    created_at: datetime
    updated_at: datetime


async def _resolve_item(
    db: AsyncSession, user: User, item_id: uuid.UUID
) -> Item:
    item = await db.get(Item, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    space = await db.get(Space, item.space_id)
    if space is None or space.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    return item


async def _resolve_note(
    db: AsyncSession, user: User, note_id: uuid.UUID
) -> Note:
    note = await db.get(Note, note_id)
    if note is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Note not found")
    item = await db.get(Item, note.item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Note not found")
    space = await db.get(Space, item.space_id)
    if space is None or space.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Note not found")
    return note


@router.get(
    "/api/items/{item_id}/notes", response_model=list[NoteResponse]
)
async def list_notes(
    item_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[Note]:
    await _resolve_item(db, user, item_id)
    rows = await db.execute(
        select(Note)
        .where(Note.item_id == item_id)
        .order_by(Note.updated_at.desc(), Note.id)
    )
    return list(rows.scalars().all())


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
) -> Note:
    await _resolve_item(db, user, item_id)
    note = Note(
        item_id=item_id,
        content_html=payload.content_html,
        content_text=html_to_text(payload.content_html),
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return note


@router.put("/api/notes/{note_id}", response_model=NoteResponse)
async def update_note(
    note_id: uuid.UUID,
    payload: NoteUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Note:
    note = await _resolve_note(db, user, note_id)
    note.content_html = payload.content_html
    note.content_text = html_to_text(payload.content_html)
    note.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(note)
    return note


@router.delete(
    "/api/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_note(
    note_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    note = await _resolve_note(db, user, note_id)
    await db.delete(note)
    await db.commit()
