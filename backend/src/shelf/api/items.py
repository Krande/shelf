"""Item CRUD endpoints.

List / create / get / update / soft-delete / restore /
permanent-delete. Permissions come from the caller's role in the item's
space: viewer reads, editor writes (see auth/spaces.py).
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import String

from ..auth.deps import get_current_user
from ..auth.spaces import (
    SPACE_ROLE_EDITOR,
    SPACE_ROLE_VIEWER,
    require_space_role,
)
from ..db import get_session
from ..models import (
    Attachment,
    AttachmentPage,
    Item,
    ItemCollection,
    ItemTag,
    Space,
    Tag,
    User,
)

router = APIRouter(tags=["items"])


def _escape_ilike(s: str) -> str:
    """Quote LIKE/ILIKE wildcard chars so a user-supplied query is
    treated as a literal substring. Backslash is the escape char we
    pass via `escape="\\\\"` at call sites; chosen because it's not
    one of LIKE's metacharacters and keeps the SQL readable."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# Snippet rendering window for fulltext hits. ~60 chars on each side
# of the match keeps the snippet readable on the LibraryPage row
# without truncating short pages mid-sentence.
_SNIPPET_PAD = 60
_SNIPPET_MAX_PER_PAGE = 1


def _build_snippet(text: str, needle: str) -> str | None:
    """Return an HTML-safe snippet of `text` with the first match of
    `needle` (case-insensitive) wrapped in `<mark>…</mark>`. Other
    HTML metacharacters are escaped so we can safely render via
    dangerouslySetInnerHTML on the client."""
    from html import escape as html_escape

    if not needle:
        return None
    lower = text.lower()
    idx = lower.find(needle.lower())
    if idx == -1:
        return None
    start = max(0, idx - _SNIPPET_PAD)
    end = min(len(text), idx + len(needle) + _SNIPPET_PAD)
    pre = ("…" if start > 0 else "") + html_escape(text[start:idx])
    hit = html_escape(text[idx : idx + len(needle)])
    post = html_escape(text[idx + len(needle) : end]) + (
        "…" if end < len(text) else ""
    )
    # Collapse whitespace so multi-line PDF text wraps as a single
    # readable line in the result row.
    pre = " ".join(pre.split())
    post = " ".join(post.split())
    return f"{pre} <mark>{hit}</mark> {post}".strip()


class ItemCreate(BaseModel):
    item_type: str
    data: dict[str, Any] = {}


class ItemUpdate(BaseModel):
    item_type: str | None = None
    data: dict[str, Any] | None = None


class ItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    space_id: uuid.UUID
    item_type: str
    data: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    collection_ids: list[uuid.UUID] = []
    tag_ids: list[uuid.UUID] = []


class ListItemsResponse(BaseModel):
    """Paginated items response. ``total`` is the count for the
    current filter set across the whole space — independent of the
    requested page — so the SPA can render "X items" without having
    to fetch every page just to count them."""

    items: list[ItemResponse]
    total: int


async def _attach_collection_ids(
    db: AsyncSession, items: list[Item]
) -> list[dict[str, Any]]:
    """Hydrate items with their collection_ids and tag_ids in two
    single queries (one per join). Returning bare ORM rows would force
    the response_model to figure out the joins lazily; we emit dicts
    so FastAPI can serialise without issuing an N+1.
    """
    if not items:
        return []
    ids = [it.id for it in items]
    coll_rows = await db.execute(
        select(ItemCollection.item_id, ItemCollection.collection_id).where(
            ItemCollection.item_id.in_(ids)
        )
    )
    coll_by_item: dict[uuid.UUID, list[uuid.UUID]] = {iid: [] for iid in ids}
    for item_id, coll_id in coll_rows.all():
        coll_by_item[item_id].append(coll_id)
    tag_rows = await db.execute(
        select(ItemTag.item_id, ItemTag.tag_id).where(ItemTag.item_id.in_(ids))
    )
    tag_by_item: dict[uuid.UUID, list[uuid.UUID]] = {iid: [] for iid in ids}
    for item_id, tag_id in tag_rows.all():
        tag_by_item[item_id].append(tag_id)
    return [
        {
            "id": it.id,
            "space_id": it.space_id,
            "item_type": it.item_type,
            "data": it.data,
            "created_at": it.created_at,
            "updated_at": it.updated_at,
            "deleted_at": it.deleted_at,
            "collection_ids": coll_by_item.get(it.id, []),
            "tag_ids": tag_by_item.get(it.id, []),
        }
        for it in items
    ]


class ItemStatus(StrEnum):
    active = "active"
    trashed = "trashed"
    all = "all"


class ItemSort(StrEnum):
    # Trailing underscores on the Python names dodge collisions with
    # `str.title()` and `type` (a builtin) — the wire values stay
    # "title" and "type" so the query string is unaffected.
    updated = "updated"
    created = "created"
    title_ = "title"
    type_ = "type"


class SortDirection(StrEnum):
    asc = "asc"
    desc = "desc"


class SearchScope(StrEnum):
    """Fields searchable via ?q=. The default (no `scope=` param) is
    every value below — passing one or more `scope=` values narrows
    the `q` match to just those.

    `title_` is suffixed to dodge the `str.title()` shadow; the wire
    value stays "title".

    `fulltext` matches the body text extracted from PDF attachments
    (see `attachments.tsv` GIN index + the worker pipeline). It's an
    EXISTS join rather than a column on items, so it short-circuits
    out of the OR'd metadata clauses.
    """

    title_ = "title"
    creators = "creators"
    abstract = "abstract"
    extra = "extra"
    fulltext = "fulltext"


async def _resolve_space(
    db: AsyncSession,
    user: User,
    slug: str,
    minimum: str = SPACE_ROLE_VIEWER,
) -> Space:
    result = await db.execute(select(Space).where(Space.slug == slug))
    space = result.scalar_one_or_none()
    await require_space_role(db, space, user.id, minimum, label="Space not found")
    assert space is not None  # require_space_role raises when it isn't
    return space


async def _resolve_item(
    db: AsyncSession,
    user: User,
    item_id: uuid.UUID,
    *,
    minimum: str = SPACE_ROLE_VIEWER,
    include_trashed: bool = False,
) -> Item:
    """Look up an item the caller is allowed to see. By default trashed
    items 404 — callers that operate on the trash (restore, permanent
    delete) opt in via include_trashed=True.

    `minimum` is the role the caller needs in the item's space: viewer to
    read, editor to change anything.
    """
    item = await db.get(Item, item_id)
    if item is None or (item.deleted_at is not None and not include_trashed):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    space = await db.get(Space, item.space_id)
    await require_space_role(db, space, user.id, minimum, label="Item not found")
    return item


@router.get("/api/spaces/{slug}/items", response_model=ListItemsResponse)
async def list_items(
    slug: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query(max_length=200)] = None,
    tag: Annotated[list[str] | None, Query()] = None,
    status_: Annotated[ItemStatus, Query(alias="status")] = ItemStatus.active,
    sort: Annotated[ItemSort, Query()] = ItemSort.updated,
    direction: Annotated[SortDirection, Query()] = SortDirection.desc,
    collection: Annotated[str | None, Query(max_length=64)] = None,
    scope: Annotated[list[SearchScope] | None, Query()] = None,
) -> dict[str, Any]:
    space = await _resolve_space(db, user, slug)
    stmt = select(Item).where(Item.space_id == space.id)
    if status_ is ItemStatus.active:
        stmt = stmt.where(Item.deleted_at.is_(None))
    elif status_ is ItemStatus.trashed:
        stmt = stmt.where(Item.deleted_at.is_not(None))
    # `all` skips the deleted_at predicate entirely.

    # Scope-priority ranking. When q is set, items with a title hit
    # come before items whose only match is in creators / abstract /
    # extra, and fulltext-only matches go last. Otherwise a query
    # like "002" (which matches hundreds of PDF bodies) buries the
    # handful of title hits past the first page and the user never
    # sees them. Built outside the q block so it's None when no
    # search is active.
    scope_rank = None
    if q and q.strip():
        # Case-insensitive substring match across selected metadata
        # fields. Creators are a JSONB array of objects, so we cast
        # to text and ilike the result — works for first/last/`name`
        # without a normalised people table. The `fulltext` scope
        # bolts on an EXISTS join against attachments.tsv (filled by
        # the extraction worker) so a PDF body match surfaces the
        # parent item alongside metadata hits.
        enabled = set(scope) if scope else set(SearchScope)
        cleaned = q.strip()
        needle = f"%{cleaned}%"
        clauses = []
        title_clause = Item.data["title"].astext.ilike(needle)
        creators_clause = cast(Item.data["creators"], String).ilike(needle)
        abstract_clause = Item.data["abstractNote"].astext.ilike(needle)
        extra_clause = Item.data["extra"].astext.ilike(needle)
        if SearchScope.title_ in enabled:
            clauses.append(title_clause)
        if SearchScope.creators in enabled:
            clauses.append(creators_clause)
        if SearchScope.abstract in enabled:
            clauses.append(abstract_clause)
        if SearchScope.extra in enabled:
            clauses.append(extra_clause)
        if SearchScope.fulltext in enabled:
            # Case-insensitive substring match, exactly like the
            # reader's in-PDF Ctrl-F find tool — a search for "Test"
            # picks up "Testing", "Latest", etc. The lexeme-based
            # tsvector approach stems away these substrings, which
            # surprised users (an item shows up under Ctrl-F but not
            # in the library search). The same matcher powers
            # /fulltext-hits so the listing and the expansion always
            # agree.
            text_needle = f"%{_escape_ilike(cleaned)}%"
            clauses.append(
                select(AttachmentPage.attachment_id)
                .join(
                    Attachment,
                    Attachment.id == AttachmentPage.attachment_id,
                )
                .where(
                    Attachment.item_id == Item.id,
                    AttachmentPage.text.ilike(text_needle, escape="\\"),
                )
                .exists()
            )
        if clauses:
            stmt = stmt.where(or_(*clauses))
            # Lower number = higher priority. Mirrors the order the
            # SPA already uses to bucket results into "Title hits" /
            # "Creator hits" / etc. sections.
            scope_rank = case(
                (title_clause, 0),
                (creators_clause, 1),
                (abstract_clause, 2),
                (extra_clause, 3),
                else_=4,
            )
        else:
            # All scopes disabled — by definition no matches.
            return {"items": [], "total": 0}
    if tag:
        # AND across multiple ?tag= params: each tag must be present.
        # Tags are matched by name (case-insensitive via the CITEXT
        # column) against the normalised `tags` table joined through
        # `item_tags`. Subquery-per-tag keeps the SQL straightforward
        # and the cardinality is small for any single user's library.
        for t in tag:
            t_clean = t.strip()
            if t_clean:
                stmt = stmt.where(
                    select(ItemTag.item_id)
                    .join(Tag, Tag.id == ItemTag.tag_id)
                    .where(
                        ItemTag.item_id == Item.id,
                        Tag.space_id == space.id,
                        Tag.name == t_clean,
                    )
                    .exists()
                )

    if collection:
        # Special-cased "unfiled" → items with no collection memberships.
        if collection == "unfiled":
            stmt = stmt.where(
                ~select(ItemCollection.item_id)
                .where(ItemCollection.item_id == Item.id)
                .exists()
            )
        else:
            try:
                cid = uuid.UUID(collection)
            except ValueError as e:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "collection must be a UUID or 'unfiled'",
                ) from e
            stmt = stmt.join(
                ItemCollection, ItemCollection.item_id == Item.id
            ).where(ItemCollection.collection_id == cid)

    # Sort. Title sort dips into the JSONB blob via data->>'title' and
    # is therefore not index-backed; build an expression index when the
    # row count makes it noticeable.
    sort_columns = {
        ItemSort.updated: Item.updated_at,
        ItemSort.created: Item.created_at,
        ItemSort.title_: Item.data["title"].astext,
        ItemSort.type_: Item.item_type,
    }
    primary = sort_columns[sort]
    primary = primary.asc() if direction is SortDirection.asc else primary.desc()
    # Stable order tie-breaker on id keeps pagination consistent across
    # rows that share the primary sort key. ``scope_rank`` (only set
    # during a search) leads so title hits surface above body-text
    # hits regardless of update time.
    if scope_rank is not None:
        stmt = stmt.order_by(scope_rank, primary, Item.id)
    else:
        stmt = stmt.order_by(primary, Item.id)

    # Total across all matches for this filter set — drives the
    # "X items" header in the SPA without it having to fetch every
    # page. ``order_by(None)`` strips the ORDER BY since count() over
    # a sub-select doesn't need ordering and Postgres rejects an
    # ORDER BY in some sub-select contexts otherwise.
    count_stmt = select(func.count()).select_from(
        stmt.order_by(None).subquery()
    )
    total = (await db.execute(count_stmt)).scalar_one()

    result = await db.execute(stmt.limit(limit).offset(offset))
    items = await _attach_collection_ids(db, list(result.scalars().all()))
    return {"items": items, "total": total}


@router.post(
    "/api/spaces/{slug}/items",
    response_model=ItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_item(
    slug: str,
    payload: ItemCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    space = await _resolve_space(db, user, slug, SPACE_ROLE_EDITOR)
    item = Item(
        space_id=space.id,
        item_type=payload.item_type,
        data=payload.data,
        created_by=user.id,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return (await _attach_collection_ids(db, [item]))[0]


@router.get("/api/items/{item_id}", response_model=ItemResponse)
async def get_item(
    item_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    item = await _resolve_item(db, user, item_id)
    return (await _attach_collection_ids(db, [item]))[0]


class FulltextHit(BaseModel):
    page_number: int
    snippet_html: str


class FulltextHitsAttachment(BaseModel):
    attachment_id: uuid.UUID
    filename: str
    hits: list[FulltextHit]


@router.get(
    "/api/items/{item_id}/fulltext-hits",
    response_model=list[FulltextHitsAttachment],
)
async def fulltext_hits(
    item_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[str, Query(min_length=1, max_length=200)],
) -> list[dict[str, Any]]:
    """Per-page snippet hits for the search expansion in LibraryPage.

    Substring/ILIKE-based — same matcher as the reader's in-PDF find
    tool, so the listing-level "this item matches" and the expansion
    "here are the pages" always agree. Snippet HTML is built in
    Python with html.escape + a single `<mark>` wrap so the client
    can render via dangerouslySetInnerHTML safely.
    """
    item = await _resolve_item(db, user, item_id)
    cleaned = q.strip()
    if not cleaned:
        return []

    att_rows = (
        await db.execute(
            select(Attachment.id, Attachment.filename).where(
                Attachment.item_id == item.id
            )
        )
    ).all()
    if not att_rows:
        return []

    text_needle = f"%{_escape_ilike(cleaned)}%"

    out: list[dict[str, Any]] = []
    for att_id, filename in att_rows:
        page_rows = (
            await db.execute(
                select(
                    AttachmentPage.page_number,
                    AttachmentPage.text,
                )
                .where(
                    AttachmentPage.attachment_id == att_id,
                    AttachmentPage.text.ilike(text_needle, escape="\\"),
                )
                .order_by(AttachmentPage.page_number.asc())
            )
        ).all()
        if not page_rows:
            continue
        hits: list[dict[str, Any]] = []
        for page_number, text in page_rows:
            snippet = _build_snippet(text, cleaned)
            if snippet is None:
                continue
            hits.append({"page_number": page_number, "snippet_html": snippet})
        if hits:
            out.append(
                {
                    "attachment_id": att_id,
                    "filename": filename,
                    "hits": hits,
                }
            )
    return out


@router.patch("/api/items/{item_id}", response_model=ItemResponse)
async def update_item(
    item_id: uuid.UUID,
    payload: ItemUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    item = await _resolve_item(db, user, item_id, minimum=SPACE_ROLE_EDITOR)
    if payload.item_type is not None:
        item.item_type = payload.item_type
    if payload.data is not None:
        item.data = payload.data
    await db.commit()
    await db.refresh(item)
    return (await _attach_collection_ids(db, [item]))[0]


@router.delete("/api/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(
    item_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    permanent: Annotated[bool, Query()] = False,
) -> None:
    """Soft-delete an active item, or hard-delete a trashed one.

    Default behaviour mirrors a "move to trash" gesture — the row stays
    in the table with deleted_at set so the user can restore it. Pass
    ?permanent=true on a row that is already trashed to drop it for
    good (used by Empty Trash). Trying to permanent-delete an active
    item is rejected so a single accidental click can't take an item
    straight off the shelf.
    """
    item = await _resolve_item(
        db, user, item_id, minimum=SPACE_ROLE_EDITOR, include_trashed=permanent
    )
    if permanent:
        if item.deleted_at is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Item must be trashed before it can be permanently deleted",
            )
        await db.delete(item)
    else:
        item.deleted_at = datetime.now(UTC)
    await db.commit()


@router.post("/api/items/{item_id}/restore", response_model=ItemResponse)
async def restore_item(
    item_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Item:
    item = await _resolve_item(
        db, user, item_id, minimum=SPACE_ROLE_EDITOR, include_trashed=True
    )
    if item.deleted_at is None:
        # No-op restore is fine to expose; just don't write.
        return item
    item.deleted_at = None
    await db.commit()
    await db.refresh(item)
    return item
