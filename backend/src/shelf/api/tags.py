"""Tag CRUD + per-item attach/detach.

Tags are space-scoped and (space_id, name) is unique case-insensitively
(name uses CITEXT). The `item_tags` join holds membership; the set of
tag IDs an item belongs to is exposed on the item response and managed
via PUT /api/items/{id}/tags.

Tag *names* (lowercased) remain the search/filter token used by the
existing list-items `?tag=` filter — see items.py — so URLs and UI
state stay readable. The normalised table replaces the per-item JSONB
`data.tags` array.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import get_current_user
from ..db import get_session
from ..models import Item, ItemTag, Space, Tag, User

router = APIRouter(tags=["tags"])


class TagCreate(BaseModel):
    name: str
    color: str | None = None


class TagUpdate(BaseModel):
    name: str | None = None
    color: str | None = None


class TagResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    space_id: uuid.UUID
    name: str
    color: str | None
    created_at: datetime
    updated_at: datetime


class ItemTagsUpdate(BaseModel):
    tag_ids: list[uuid.UUID]


async def _resolve_space(db: AsyncSession, user: User, slug: str) -> Space:
    result = await db.execute(select(Space).where(Space.slug == slug))
    space = result.scalar_one_or_none()
    if space is None or space.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Space not found")
    return space


async def _resolve_tag(db: AsyncSession, user: User, tag_id: uuid.UUID) -> Tag:
    tag = await db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tag not found")
    space = await db.get(Space, tag.space_id)
    if space is None or space.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tag not found")
    return tag


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


@router.get("/api/spaces/{slug}/tags", response_model=list[TagResponse])
async def list_tags(
    slug: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> list[Tag]:
    space = await _resolve_space(db, user, slug)
    stmt = select(Tag).where(Tag.space_id == space.id)
    if q and q.strip():
        # CITEXT already case-insensitive on equality; for prefix match
        # we still need an explicit lower() since LIKE is case-sensitive.
        prefix = f"{q.strip().lower()}%"
        stmt = stmt.where(func.lower(Tag.name).like(prefix))
    stmt = stmt.order_by(Tag.name)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post(
    "/api/spaces/{slug}/tags",
    response_model=TagResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_tag(
    slug: str,
    payload: TagCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Tag:
    space = await _resolve_space(db, user, slug)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name required")
    tag = Tag(space_id=space.id, name=name, color=payload.color)
    db.add(tag)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Tag with this name already exists"
        ) from e
    await db.refresh(tag)
    return tag


@router.patch("/api/tags/{tag_id}", response_model=TagResponse)
async def update_tag(
    tag_id: uuid.UUID,
    payload: TagUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Tag:
    tag = await _resolve_tag(db, user, tag_id)
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name required")
        tag.name = name
    if payload.color is not None:
        tag.color = payload.color or None
    tag.updated_at = datetime.now(UTC)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Tag with this name already exists"
        ) from e
    await db.refresh(tag)
    return tag


@router.delete("/api/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(
    tag_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    tag = await _resolve_tag(db, user, tag_id)
    # ON DELETE CASCADE drops item_tags rows for us.
    await db.delete(tag)
    await db.commit()


@router.put(
    "/api/items/{item_id}/tags",
    response_model=list[uuid.UUID],
)
async def set_item_tags(
    item_id: uuid.UUID,
    payload: ItemTagsUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[uuid.UUID]:
    item = await _resolve_item(db, user, item_id)
    requested = set(payload.tag_ids)
    # Validate every requested tag is in the same space and the user
    # owns it. De-duped via the set.
    for tid in requested:
        tag = await _resolve_tag(db, user, tid)
        if tag.space_id != item.space_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Tag lives in a different space than the item",
            )

    await db.execute(delete(ItemTag).where(ItemTag.item_id == item_id))
    for tid in requested:
        db.add(ItemTag(item_id=item_id, tag_id=tid))
    await db.commit()
    return sorted(requested)
