"""Collection (folder) CRUD + membership endpoints.

Collections form a tree inside a Space via parent_id. Items can belong
to many collections — the join table is `item_collections`. The set of
collections an item belongs to is exposed on the item response and
managed via PUT /api/items/{id}/collections.

Sibling order is dense within (space_id, parent_id) — the API renumbers
on insert and move so positions always match what the rail renders.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import get_current_user
from ..auth.spaces import SPACE_ROLE_EDITOR, SPACE_ROLE_VIEWER, require_space_role
from ..db import get_session
from ..models import Collection, Item, ItemCollection, Space, User

router = APIRouter(tags=["collections"])


class CollectionCreate(BaseModel):
    name: str
    parent_id: uuid.UUID | None = None
    description: str | None = None


class CollectionUpdate(BaseModel):
    """Partial update.

    All fields are optional, but **presence** matters — using
    ``model_fields_set`` we distinguish "field not provided" from
    "field set to null". This lets the client move a collection back to
    the root (parent_id = null) without colliding with the unset case.
    """

    name: str | None = None
    description: str | None = None
    parent_id: uuid.UUID | None = None
    position: int | None = None


class CollectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    space_id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    description: str | None
    position: int
    created_at: datetime
    updated_at: datetime


class ItemCollectionsUpdate(BaseModel):
    collection_ids: list[uuid.UUID]


async def _resolve_space(
    db: AsyncSession, user: User, slug: str, minimum: str = SPACE_ROLE_VIEWER
) -> Space:
    result = await db.execute(select(Space).where(Space.slug == slug))
    space = result.scalar_one_or_none()
    await require_space_role(db, space, user.id, minimum, label="Space not found")
    assert space is not None  # require_space_role raises when it isn't
    return space


async def _resolve_collection(
    db: AsyncSession,
    user: User,
    collection_id: uuid.UUID,
    minimum: str = SPACE_ROLE_VIEWER,
) -> Collection:
    coll = await db.get(Collection, collection_id)
    if coll is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collection not found")
    space = await db.get(Space, coll.space_id)
    await require_space_role(
        db, space, user.id, minimum, label="Collection not found"
    )
    return coll


async def _resolve_item(
    db: AsyncSession,
    user: User,
    item_id: uuid.UUID,
    minimum: str = SPACE_ROLE_VIEWER,
) -> Item:
    item = await db.get(Item, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    space = await db.get(Space, item.space_id)
    await require_space_role(db, space, user.id, minimum, label="Item not found")
    return item


async def _siblings(
    db: AsyncSession,
    space_id: uuid.UUID,
    parent_id: uuid.UUID | None,
) -> list[Collection]:
    """All collections under ``parent_id`` in ``space_id``, in position
    order. NULL parent_id (root) is handled explicitly because ``==
    None`` in SQL does the wrong thing."""
    stmt = (
        select(Collection)
        .where(Collection.space_id == space_id)
        .order_by(Collection.position)
    )
    if parent_id is None:
        stmt = stmt.where(Collection.parent_id.is_(None))
    else:
        stmt = stmt.where(Collection.parent_id == parent_id)
    return list((await db.execute(stmt)).scalars().all())


async def _renumber(siblings: list[Collection]) -> None:
    """Assign dense 0..N-1 positions to the given (already-ordered) list."""
    for idx, sib in enumerate(siblings):
        if sib.position != idx:
            sib.position = idx


@router.get(
    "/api/spaces/{slug}/collections", response_model=list[CollectionResponse]
)
async def list_collections(
    slug: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[Collection]:
    space = await _resolve_space(db, user, slug)
    # Position-only sort: the client groups by parent_id and the
    # relative order within each group survives because sibling
    # positions are dense and unique per (space_id, parent_id).
    result = await db.execute(
        select(Collection)
        .where(Collection.space_id == space.id)
        .order_by(Collection.position, Collection.name)
    )
    return list(result.scalars().all())


@router.post(
    "/api/spaces/{slug}/collections",
    response_model=CollectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_collection(
    slug: str,
    payload: CollectionCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Collection:
    space = await _resolve_space(db, user, slug, SPACE_ROLE_EDITOR)
    if payload.parent_id is not None:
        parent = await _resolve_collection(db, user, payload.parent_id)
        if parent.space_id != space.id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Parent collection lives in a different space",
            )
    name = payload.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name required")
    # Append: position = current sibling count.
    sibling_count = len(await _siblings(db, space.id, payload.parent_id))
    desc = (payload.description or "").strip() or None
    coll = Collection(
        space_id=space.id,
        parent_id=payload.parent_id,
        name=name,
        description=desc,
        position=sibling_count,
    )
    db.add(coll)
    await db.commit()
    await db.refresh(coll)
    return coll


@router.patch(
    "/api/collections/{collection_id}", response_model=CollectionResponse
)
async def update_collection(
    collection_id: uuid.UUID,
    payload: CollectionUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Collection:
    coll = await _resolve_collection(db, user, collection_id, SPACE_ROLE_EDITOR)
    provided = payload.model_fields_set

    if "name" in provided and payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name required")
        coll.name = name

    if "description" in provided:
        # Strip + treat empty as NULL so "clear" and "set to whitespace"
        # collapse to the same canonical state.
        desc = (payload.description or "").strip()
        coll.description = desc or None

    # parent_id / position handling. Both can be set independently:
    # - parent_id only → append to new parent's end.
    # - position only → reorder within current parent.
    # - both → move to position N under new parent.
    reparenting = "parent_id" in provided and payload.parent_id != coll.parent_id
    reordering = "position" in provided and payload.position is not None

    if reparenting:
        new_parent_id = payload.parent_id
        if new_parent_id is not None:
            if new_parent_id == coll.id:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "A collection cannot be its own parent",
                )
            parent = await _resolve_collection(db, user, new_parent_id)
            if parent.space_id != coll.space_id:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Parent collection lives in a different space",
                )
            # Cycle check: walk up the new parent's ancestors and refuse
            # if we land back on coll.
            ancestor: Collection | None = parent
            while ancestor is not None:
                if ancestor.id == coll.id:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        "Reparenting would create a cycle",
                    )
                ancestor = (
                    await db.get(Collection, ancestor.parent_id)
                    if ancestor.parent_id is not None
                    else None
                )
    else:
        new_parent_id = coll.parent_id

    if reparenting or reordering:
        # Remove from old siblings and renumber them.
        old_siblings = [
            s
            for s in await _siblings(db, coll.space_id, coll.parent_id)
            if s.id != coll.id
        ]
        await _renumber(old_siblings)

        # Insert into new sibling list at the requested (clamped) index.
        if reparenting:
            new_siblings = await _siblings(db, coll.space_id, new_parent_id)
        else:
            new_siblings = old_siblings  # same parent — already deduped
        # `reordering` already implies a non-None position (see where it is
        # computed), but repeating the check here is what lets the type
        # narrow from int | None to int.
        if reordering and payload.position is not None:
            target = payload.position
        else:
            target = len(new_siblings)
        target = max(0, min(target, len(new_siblings)))
        coll.parent_id = new_parent_id
        new_siblings.insert(target, coll)
        await _renumber(new_siblings)

    coll.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(coll)
    return coll


@router.delete(
    "/api/collections/{collection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_collection(
    collection_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    coll = await _resolve_collection(db, user, collection_id, SPACE_ROLE_EDITOR)
    space_id = coll.space_id
    parent_id = coll.parent_id
    # ON DELETE CASCADE drops both the children (via parent_id) and the
    # item_collections rows; the items themselves stay put.
    await db.delete(coll)
    await db.flush()
    # Re-pack siblings so positions stay dense after the deletion.
    remaining = await _siblings(db, space_id, parent_id)
    await _renumber(remaining)
    await db.commit()


@router.put(
    "/api/items/{item_id}/collections",
    response_model=list[uuid.UUID],
)
async def set_item_collections(
    item_id: uuid.UUID,
    payload: ItemCollectionsUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[uuid.UUID]:
    item = await _resolve_item(db, user, item_id, SPACE_ROLE_EDITOR)
    # Validate every requested collection belongs to the same space and
    # the caller can write to it. De-dup as a side effect of using a set.
    requested = set(payload.collection_ids)
    for cid in requested:
        coll = await _resolve_collection(db, user, cid, SPACE_ROLE_EDITOR)
        if coll.space_id != item.space_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Collection lives in a different space than the item",
            )

    # Replace the set wholesale — easier to reason about than diff +
    # add/remove and the row counts stay tiny.
    await db.execute(
        delete(ItemCollection).where(ItemCollection.item_id == item_id)
    )
    for cid in requested:
        db.add(ItemCollection(item_id=item_id, collection_id=cid))
    await db.commit()
    return sorted(requested)
