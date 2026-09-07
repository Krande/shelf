"""Versioned token-auth API surface — `/api/v1/*`.

This is the curl-friendly side of Shelf: every endpoint takes
`Authorization: Bearer shelf_<token>` and respects the token's scopes
+ optional `allowed_collection_ids`. The cookie-authenticated /api/*
surface used by the SPA stays as-is.

Why a separate prefix instead of mixing auth modes on the same paths:
the SPA depends on cookies and would silently fail closed on a Bearer
client; making the namespaces explicit keeps each path honest.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import RedirectResponse
from obstore import put_async
from pydantic import BaseModel, ConfigDict
from sqlalchemy import cast, delete, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import String

from ..auth.tokens import TokenAuth, require_scope
from ..db import get_session
from ..models import ApiToken, Attachment, Collection, Item, ItemCollection, Space
from ..services import extraction, storage

router = APIRouter(tags=["v1"], prefix="/api/v1")


class ItemSummary(BaseModel):
    """Slim, token-safe view of an item; mirrors what the SPA's
    Item endpoint returns minus collection_ids (which are filtered
    by the token's scope and rebuilt below)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    space_id: uuid.UUID
    item_type: str
    data: dict[str, object]
    created_at: datetime
    updated_at: datetime
    collection_ids: list[uuid.UUID] = []


class AttachmentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int | None
    uploaded_at: datetime | None = None


# ── helpers ───────────────────────────────────────────────────────────────


async def _resolve_owned_item(
    db: AsyncSession, auth: TokenAuth, item_id: uuid.UUID
) -> Item:
    item = await db.get(Item, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    space = await db.get(Space, item.space_id)
    if space is None or space.owner_id != auth.user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    return item


async def _effective_allowed_collection_ids(
    db: AsyncSession, token: ApiToken
) -> set[uuid.UUID] | None:
    """Resolve a token's `allowed_collection_ids` to the full set of
    collection ids it actually covers — or ``None`` for unrestricted.

    When ``include_descendants`` is on, walks the parent_id tree so a
    seed collection implicitly grants access to everything nested
    below it. The walk runs at request time (recursive CTE), which
    means collections added after mint are picked up automatically.
    """
    if not token.allowed_collection_ids:
        return None
    seeds = {uuid.UUID(c) for c in token.allowed_collection_ids}
    if not token.include_descendants:
        return seeds

    base = (
        select(Collection.id)
        .where(Collection.id.in_(seeds))
        .cte(name="allowed_descendants", recursive=True)
    )
    # UNION (not UNION ALL): collection.parent_id has no cycle check at
    # the schema level, so a stray cycle in user data would loop a
    # UNION ALL forever. UNION dedupes against accumulated rows and
    # therefore terminates on cycles.
    walk = base.union(
        select(Collection.id).where(Collection.parent_id == base.c.id)
    )
    rows = await db.execute(select(walk.c.id))
    return {row[0] for row in rows.all()}


async def _enforce_collection_scope(
    db: AsyncSession, auth: TokenAuth, item: Item
) -> None:
    """If the token is collection-scoped, the item must be a member of
    at least one of the allowed collections. If the token is
    unrestricted (`allowed_collection_ids` is NULL), this is a no-op."""
    allowed = await _effective_allowed_collection_ids(db, auth.token)
    if allowed is None:
        return
    rows = await db.execute(
        select(ItemCollection.collection_id).where(
            ItemCollection.item_id == item.id,
            ItemCollection.collection_id.in_(allowed),
        )
    )
    if rows.first() is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Token cannot access this item's collections",
        )


async def _hydrate_collection_ids(
    db: AsyncSession, items: list[Item]
) -> list[dict[str, object]]:
    if not items:
        return []
    ids = [it.id for it in items]
    rows = await db.execute(
        select(ItemCollection.item_id, ItemCollection.collection_id).where(
            ItemCollection.item_id.in_(ids)
        )
    )
    by_item: dict[uuid.UUID, list[uuid.UUID]] = {iid: [] for iid in ids}
    for item_id, coll_id in rows.all():
        by_item[item_id].append(coll_id)
    return [
        {
            "id": it.id,
            "space_id": it.space_id,
            "item_type": it.item_type,
            "data": it.data,
            "created_at": it.created_at,
            "updated_at": it.updated_at,
            "collection_ids": by_item.get(it.id, []),
        }
        for it in items
    ]


async def _resolve_or_create_item(
    db: AsyncSession,
    auth: TokenAuth,
    *,
    item_id: uuid.UUID | None,
    title: str | None,
    space_slug: str | None,
    collection_id: list[uuid.UUID] | None,
) -> tuple[Item, Item | None]:
    """Resolve an existing item (item_id) OR mint a new document item
    (title) honouring token's collection scope. Returns
    (item, created_item_or_None) where created_item is non-None only on
    the create path."""
    if (item_id is None) == (title is None):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Pass exactly one of `item_id` (attach to existing item) or "
            "`title` (create a new document item).",
        )

    if item_id is not None:
        item = await _resolve_owned_item(db, auth, item_id)
        await _enforce_collection_scope(db, auth, item)
        return item, None

    if space_slug is not None:
        space = (
            await db.execute(
                select(Space).where(
                    Space.slug == space_slug, Space.owner_id == auth.user.id
                )
            )
        ).scalar_one_or_none()
        if space is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Space not found")
    else:
        space = (
            await db.execute(
                select(Space)
                .where(Space.owner_id == auth.user.id)
                .order_by(Space.created_at)
            )
        ).scalars().first()
        if space is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "User has no space yet — sign in to the SPA once to bootstrap one",
            )

    item = Item(
        space_id=space.id,
        item_type="document",
        data={"title": (title or "Untitled").strip() or "Untitled"},
        created_by=auth.user.id,
    )
    db.add(item)
    await db.flush()

    if collection_id:
        allowed = await _effective_allowed_collection_ids(db, auth.token)
        for cid in collection_id:
            if allowed is not None and cid not in allowed:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    f"Token cannot file items in collection {cid}",
                )
            coll = await db.get(Collection, cid)
            if coll is None or coll.space_id != space.id:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Collection {cid} doesn't belong to this space",
                )
            db.add(ItemCollection(item_id=item.id, collection_id=cid))
    elif auth.token.allowed_collection_ids:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Token is collection-scoped; pass at least one collection_id",
        )

    return item, item


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ── collections ───────────────────────────────────────────────────────────


class CollectionSummary(BaseModel):
    """List/create response for /api/v1/collections.

    `path` is the slash-joined chain of ancestor names (root → leaf),
    so a token client can resolve a collection by its human-readable
    location ("FEM / Code Aster") without walking parent_id itself.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    space_id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    description: str | None
    position: int
    path: str


class CollectionCreatePayload(BaseModel):
    """Body for POST /api/v1/collections.

    Pass `parent_id` to nest under an existing collection (the space is
    inherited from the parent). To create a root collection, pass
    `space_slug` — or omit both and the user's first-created space is
    used, mirroring /upload's space resolution.
    """

    name: str
    parent_id: uuid.UUID | None = None
    description: str | None = None
    space_slug: str | None = None


def _build_paths(
    collections: list[Collection],
) -> dict[uuid.UUID, str]:
    """Compute the " / "-joined ancestor chain for each collection,
    using only the in-memory list (no extra round trips). When a parent
    isn't in the list — e.g. a token-scoped client only sees descendants
    of an allowed collection — the walk stops at the missing ancestor."""
    by_id = {c.id: c for c in collections}
    paths: dict[uuid.UUID, str] = {}

    def resolve(coll: Collection) -> str:
        if coll.id in paths:
            return paths[coll.id]
        if coll.parent_id is None or coll.parent_id not in by_id:
            paths[coll.id] = coll.name
        else:
            paths[coll.id] = resolve(by_id[coll.parent_id]) + " / " + coll.name
        return paths[coll.id]

    for coll in collections:
        resolve(coll)
    return paths


@router.get(
    "/collections",
    response_model=list[CollectionSummary],
    summary="List collections visible to this token",
)
async def list_collections(
    auth: Annotated[TokenAuth, Depends(require_scope("search"))],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict[str, object]]:
    """Returns every collection across spaces the user owns, narrowed
    to the token's effective allow-list (with descendants) when the
    token is collection-scoped. Each row carries a `path` so callers
    can look up by human-readable location instead of UUID."""
    spaces = (
        await db.execute(select(Space.id).where(Space.owner_id == auth.user.id))
    ).scalars().all()
    if not spaces:
        return []

    stmt = (
        select(Collection)
        .where(Collection.space_id.in_(spaces))
        .order_by(Collection.space_id, Collection.position, Collection.name)
    )
    rows = list((await db.execute(stmt)).scalars().all())

    allowed = await _effective_allowed_collection_ids(db, auth.token)
    if allowed is not None:
        rows = [c for c in rows if c.id in allowed]

    paths = _build_paths(rows)
    return [
        {
            "id": c.id,
            "space_id": c.space_id,
            "parent_id": c.parent_id,
            "name": c.name,
            "description": c.description,
            "position": c.position,
            "path": paths[c.id],
        }
        for c in rows
    ]


@router.post(
    "/collections",
    response_model=CollectionSummary,
    status_code=status.HTTP_201_CREATED,
    summary="Create a (sub)collection",
)
async def create_collection(
    payload: CollectionCreatePayload,
    auth: Annotated[TokenAuth, Depends(require_scope("upload"))],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """Create a collection nested under `parent_id` (preferred) or at
    the root of `space_slug` (or the user's first space).

    Token scope: when `allowed_collection_ids` is set, the parent must
    fall within the allowed tree — otherwise the new collection would
    be invisible to the token that just minted it. Root creation is
    refused for scoped tokens for the same reason."""
    name = payload.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name required")

    if payload.parent_id is not None:
        parent = await db.get(Collection, payload.parent_id)
        if parent is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Parent collection not found"
            )
        space = await db.get(Space, parent.space_id)
        if space is None or space.owner_id != auth.user.id:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Parent collection not found"
            )
        allowed = await _effective_allowed_collection_ids(db, auth.token)
        if allowed is not None and parent.id not in allowed:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Token cannot nest a collection under that parent",
            )
        parent_id = parent.id
        space_id = space.id
    else:
        if auth.token.allowed_collection_ids:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Token is collection-scoped; pass parent_id (root creation "
                "would be outside the token's allow-list)",
            )
        if payload.space_slug is not None:
            space = (
                await db.execute(
                    select(Space).where(
                        Space.slug == payload.space_slug,
                        Space.owner_id == auth.user.id,
                    )
                )
            ).scalar_one_or_none()
            if space is None:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND, "Space not found"
                )
        else:
            space = (
                await db.execute(
                    select(Space)
                    .where(Space.owner_id == auth.user.id)
                    .order_by(Space.created_at)
                )
            ).scalars().first()
            if space is None:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "User has no space yet — sign in to the SPA once to bootstrap one",
                )
        parent_id = None
        space_id = space.id

    sibling_count = (
        await db.execute(
            select(Collection).where(
                Collection.space_id == space_id,
                Collection.parent_id.is_(None)
                if parent_id is None
                else Collection.parent_id == parent_id,
            )
        )
    ).scalars().all()
    desc = (payload.description or "").strip() or None
    coll = Collection(
        space_id=space_id,
        parent_id=parent_id,
        name=name,
        description=desc,
        position=len(sibling_count),
    )
    db.add(coll)
    await db.commit()
    await db.refresh(coll)

    # Build path from this collection up; we only need parents for the
    # response, so a small targeted walk beats reloading the whole tree.
    chain: list[str] = [coll.name]
    cursor_parent_id = coll.parent_id
    while cursor_parent_id is not None:
        ancestor = await db.get(Collection, cursor_parent_id)
        if ancestor is None:
            break
        chain.append(ancestor.name)
        cursor_parent_id = ancestor.parent_id
    path = " / ".join(reversed(chain))

    return {
        "id": coll.id,
        "space_id": coll.space_id,
        "parent_id": coll.parent_id,
        "name": coll.name,
        "description": coll.description,
        "position": coll.position,
        "path": path,
    }


@router.delete(
    "/collections/{collection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a collection (children + memberships cascade)",
)
async def delete_collection(
    collection_id: uuid.UUID,
    auth: Annotated[TokenAuth, Depends(require_scope("upload"))],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """ON DELETE CASCADE drops child collections and item_collections
    rows; items themselves stay put. Mirrors the cookie-auth delete and
    re-packs sibling positions so the rail stays dense."""
    coll = await db.get(Collection, collection_id)
    if coll is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collection not found")
    space = await db.get(Space, coll.space_id)
    if space is None or space.owner_id != auth.user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collection not found")
    allowed = await _effective_allowed_collection_ids(db, auth.token)
    if allowed is not None and coll.id not in allowed:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Token cannot delete that collection"
        )

    space_id = coll.space_id
    parent_id = coll.parent_id
    await db.delete(coll)
    await db.flush()

    siblings_stmt = select(Collection).where(Collection.space_id == space_id)
    if parent_id is None:
        siblings_stmt = siblings_stmt.where(Collection.parent_id.is_(None))
    else:
        siblings_stmt = siblings_stmt.where(Collection.parent_id == parent_id)
    siblings = list(
        (
            await db.execute(siblings_stmt.order_by(Collection.position))
        ).scalars().all()
    )
    for idx, sib in enumerate(siblings):
        if sib.position != idx:
            sib.position = idx
    await db.commit()


# ── item membership ───────────────────────────────────────────────────────


class ItemCollectionsPayload(BaseModel):
    collection_ids: list[uuid.UUID]


@router.put(
    "/items/{item_id}/collections",
    response_model=list[uuid.UUID],
    summary="Replace an item's collection memberships",
)
async def set_item_collections(
    item_id: uuid.UUID,
    payload: ItemCollectionsPayload,
    auth: Annotated[TokenAuth, Depends(require_scope("upload"))],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[uuid.UUID]:
    """Wholesale replacement of the item's `item_collections` rows. All
    target collections must live in the same space as the item, and —
    when the token is collection-scoped — fall within the allow-list
    (with descendants) so the token can't fling items into folders it
    can't see."""
    item = await _resolve_owned_item(db, auth, item_id)
    await _enforce_collection_scope(db, auth, item)

    requested = set(payload.collection_ids)
    allowed = await _effective_allowed_collection_ids(db, auth.token)
    for cid in requested:
        coll = await db.get(Collection, cid)
        if coll is None or coll.space_id != item.space_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Collection {cid} doesn't belong to this item's space",
            )
        if allowed is not None and cid not in allowed:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Token cannot file items in collection {cid}",
            )

    await db.execute(
        delete(ItemCollection).where(ItemCollection.item_id == item_id)
    )
    for cid in requested:
        db.add(ItemCollection(item_id=item_id, collection_id=cid))
    await db.commit()
    return sorted(requested)


# ── upload ────────────────────────────────────────────────────────────────


class UploadResponse(BaseModel):
    """Returned by /api/v1/upload. The attachment is always present;
    `item` carries the freshly-created document item when the upload
    minted one, and is null when the caller targeted an existing item."""

    attachment: AttachmentSummary
    item: ItemSummary | None = None


@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a file, optionally creating a document item to hold it",
)
async def upload(
    auth: Annotated[TokenAuth, Depends(require_scope("upload"))],
    db: Annotated[AsyncSession, Depends(get_session)],
    file: Annotated[UploadFile, File()],
    item_id: Annotated[uuid.UUID | None, Form()] = None,
    title: Annotated[str | None, Form()] = None,
    space_slug: Annotated[str | None, Form()] = None,
    collection_id: Annotated[list[uuid.UUID] | None, Form()] = None,
) -> UploadResponse:
    """Stream a file body straight into Shelf's bucket and create the
    matching attachment row.

    Two shapes:
    - **Existing item**: pass `item_id` and the file is attached to it.
    - **New document**: pass `title` (and optionally `space_slug` to
      pick a non-personal space, plus `collection_id`s to file the
      new item) and the endpoint mints a `document` item, attaches
      the file, and returns both. Useful for importers that want one
      curl per file.

    For bulk imports — anywhere the bytes shouldn't pass through the
    API — use /api/v1/uploads/register + /complete and PUT directly
    to the presigned URL. The SPA's drag-drop path at
    /api/items/{id}/attachments uses the same primitive over cookie
    auth.
    """
    item, created_item = await _resolve_or_create_item(
        db,
        auth,
        item_id=item_id,
        title=title,
        space_slug=space_slug,
        collection_id=collection_id,
    )

    att_id = uuid.uuid4()
    storage_key = f"items/{item.id}/attachments/{att_id}"
    body = await file.read()
    await put_async(storage.get_store(), storage_key, body)

    att = Attachment(
        id=att_id,
        item_id=item.id,
        storage_key=storage_key,
        filename=file.filename or "upload",
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(body),
        # Inline upload: the bytes are already in the bucket, mark
        # ready in the same transaction.
        uploaded_at=_utcnow(),
        created_by=auth.user.id,
    )
    db.add(att)
    await extraction.mark_and_enqueue(att)
    await db.commit()
    await db.refresh(att)
    if created_item is not None:
        await db.refresh(created_item)
        item_payload = (await _hydrate_collection_ids(db, [created_item]))[0]
        return UploadResponse(
            attachment=AttachmentSummary.model_validate(att),
            item=ItemSummary.model_validate(item_payload),
        )
    return UploadResponse(attachment=AttachmentSummary.model_validate(att))


# ── direct-to-bucket: register + complete ─────────────────────────────────


class UploadRegisterPayload(BaseModel):
    """JSON body for /api/v1/uploads/register.

    Mirrors the form fields of /api/v1/upload but expressed as JSON so
    importers don't have to multipart-encode anything to get a
    presigned PUT URL.
    """

    filename: str
    content_type: str
    size_bytes: int | None = None
    item_id: uuid.UUID | None = None
    title: str | None = None
    space_slug: str | None = None
    collection_id: list[uuid.UUID] | None = None


class UploadRegisterResponse(BaseModel):
    attachment: AttachmentSummary
    item: ItemSummary | None = None
    upload_url: str
    expires_at: datetime


@router.post(
    "/uploads/register",
    response_model=UploadRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register an attachment and get a presigned PUT URL",
)
async def uploads_register(
    payload: UploadRegisterPayload,
    auth: Annotated[TokenAuth, Depends(require_scope("upload"))],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> UploadRegisterResponse:
    """Reserve a storage key + return a presigned PUT URL so the
    caller can upload the file body directly to Garage.

    The attachment row is created immediately with `uploaded_at = NULL`
    and is intentionally visible right away — the migration script
    needs to be able to look up the row by attachment id when it later
    calls /complete. Orphan rows from a client that never PUTs can be
    pruned with a periodic HEAD-check pass.
    """
    item, created_item = await _resolve_or_create_item(
        db,
        auth,
        item_id=payload.item_id,
        title=payload.title,
        space_slug=payload.space_slug,
        collection_id=payload.collection_id,
    )

    att_id = uuid.uuid4()
    storage_key = f"items/{item.id}/attachments/{att_id}"
    att = Attachment(
        id=att_id,
        item_id=item.id,
        storage_key=storage_key,
        filename=payload.filename,
        content_type=payload.content_type,
        size_bytes=payload.size_bytes,
        uploaded_at=None,
        created_by=auth.user.id,
    )
    db.add(att)
    await db.commit()
    await db.refresh(att)

    ttl = storage.DEFAULT_PRESIGN_TTL
    upload_url = await storage.presign_upload(storage_key, ttl)
    expires_at = _utcnow() + ttl

    if created_item is not None:
        await db.refresh(created_item)
        item_payload = (await _hydrate_collection_ids(db, [created_item]))[0]
        return UploadRegisterResponse(
            attachment=AttachmentSummary.model_validate(att),
            item=ItemSummary.model_validate(item_payload),
            upload_url=upload_url,
            expires_at=expires_at,
        )
    return UploadRegisterResponse(
        attachment=AttachmentSummary.model_validate(att),
        upload_url=upload_url,
        expires_at=expires_at,
    )


@router.post(
    "/uploads/{attachment_id}/complete",
    response_model=AttachmentSummary,
    summary="Mark a registered attachment as uploaded",
)
async def uploads_complete(
    attachment_id: uuid.UUID,
    auth: Annotated[TokenAuth, Depends(require_scope("upload"))],
    db: Annotated[AsyncSession, Depends(get_session)],
    verify: Annotated[bool, Query()] = False,
) -> Attachment:
    """Set `uploaded_at` once the client has PUT the body to the
    presigned URL.

    Pass `?verify=true` to HEAD the bucket first — useful for the
    migration script as a belt-and-braces check that the
    server-side CopyObject actually wrote the right number of bytes.
    Idempotent: completing an already-complete attachment is a
    no-op.
    """
    att = await db.get(Attachment, attachment_id)
    if att is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Attachment not found"
        )
    item = await db.get(Item, att.item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Attachment not found"
        )
    space = await db.get(Space, item.space_id)
    if space is None or space.owner_id != auth.user.id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Attachment not found"
        )
    await _enforce_collection_scope(db, auth, item)

    if verify:
        try:
            head = await storage.head_object(att.storage_key)
        except Exception as e:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Object not found in storage: {e}",
            ) from e
        # Trust the bucket's reported size over what the client
        # claimed at register time — the bucket is authoritative.
        observed = head.get("size")
        if isinstance(observed, int):
            att.size_bytes = observed

    if att.uploaded_at is None:
        att.uploaded_at = _utcnow()
        await extraction.mark_and_enqueue(att)
        await db.commit()
        await db.refresh(att)
    return att


# ── search ────────────────────────────────────────────────────────────────


@router.get("/search", response_model=list[ItemSummary])
async def search(
    auth: Annotated[TokenAuth, Depends(require_scope("search"))],
    db: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[str | None, Query(max_length=200)] = None,
    tag: Annotated[list[str] | None, Query()] = None,
    collection: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, object]]:
    """Item search restricted to spaces the user owns and, when the
    token is collection-scoped, to items belonging to at least one of
    the allowed collections."""
    # Find spaces owned by the user; v1 search is read-only and never
    # touches trash.
    spaces = (
        await db.execute(select(Space.id).where(Space.owner_id == auth.user.id))
    ).scalars().all()
    if not spaces:
        return []

    stmt = select(Item).where(
        Item.space_id.in_(spaces), Item.deleted_at.is_(None)
    )
    if q and q.strip():
        # Mirror the SPA search across title / abstract / creators /
        # extra — see api/items.list_items for the rationale.
        needle = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Item.data["title"].astext.ilike(needle),
                Item.data["abstractNote"].astext.ilike(needle),
                Item.data["extra"].astext.ilike(needle),
                cast(Item.data["creators"], String).ilike(needle),
            )
        )
    if tag:
        for t in tag:
            if t.strip():
                stmt = stmt.where(
                    Item.data.cast(JSONB).contains({"tags": [t.strip()]})
                )

    if collection is not None:
        stmt = stmt.join(
            ItemCollection, ItemCollection.item_id == Item.id
        ).where(ItemCollection.collection_id == collection)

    allowed = await _effective_allowed_collection_ids(db, auth.token)
    if allowed is not None:
        # Token-scoped: items must be in at least one allowed collection.
        # Composes with the optional ?collection= filter — if the explicit
        # collection isn't in the token's effective allow-list (which may
        # include descendants), the result is empty by design.
        if collection is None:
            stmt = stmt.join(
                ItemCollection, ItemCollection.item_id == Item.id
            ).where(ItemCollection.collection_id.in_(allowed))
        elif collection not in allowed:
            return []
        # Else: we already joined with the explicit collection filter,
        # which is sufficient.

    stmt = stmt.order_by(Item.updated_at.desc()).limit(limit).offset(offset)

    rows = (await db.execute(stmt)).scalars().unique().all()
    return await _hydrate_collection_ids(db, list(rows))


# ── attachment listing ────────────────────────────────────────────────────


@router.get(
    "/items/{item_id}/attachments",
    response_model=list[AttachmentSummary],
    summary="List attachments on an item",
)
async def list_item_attachments(
    item_id: uuid.UUID,
    auth: Annotated[TokenAuth, Depends(require_scope("search"))],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[Attachment]:
    """Resolve an item to the attachments the caller can download.

    Pairs with ``GET /api/v1/download/{attachment_id}``: ``search``
    gives item IDs (with no attachment refs), this gives the
    per-item attachment IDs, ``download`` returns a presigned URL.
    Useful for automation that wants to fetch all of an item's
    attachments by item UUID without going through the SPA.

    Scope: ``search`` — listing is read-only metadata; the actual
    bytes still require the ``download`` scope.
    """
    item = await _resolve_owned_item(db, auth, item_id)
    await _enforce_collection_scope(db, auth, item)
    rows = (
        await db.execute(
            select(Attachment)
            .where(Attachment.item_id == item.id)
            .order_by(Attachment.created_at.asc())
        )
    ).scalars().all()
    return list(rows)


# ── download ──────────────────────────────────────────────────────────────


@router.get("/download/{attachment_id}")
async def download(
    attachment_id: uuid.UUID,
    auth: Annotated[TokenAuth, Depends(require_scope("download"))],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> RedirectResponse:
    """302-redirects to a short-lived presigned GET on the bucket. The
    redirect saves us streaming the bytes through the API and lets
    big files travel at line rate from Garage to the caller."""
    att = await db.get(Attachment, attachment_id)
    if att is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    item = await db.get(Item, att.item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    space = await db.get(Space, item.space_id)
    if space is None or space.owner_id != auth.user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")

    await _enforce_collection_scope(db, auth, item)

    url = await storage.presign_download(att.storage_key)
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)
