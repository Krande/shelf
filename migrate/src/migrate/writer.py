"""Idempotent upserts into Shelf's Postgres.

Every row's primary key is a UUIDv5 derived from the legacy Zotero
key (see ``transform.py``), so re-running the importer hits the same
rows and updates them in place rather than inserting duplicates.

Synthetic parent items are materialised here for orphaned attachments
and notes — Zotero allows standalones, Shelf requires every
attachment/note to belong to an item. We mint a deterministic UUID
seeded from the orphan's own legacy key so it stays stable too.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from shelf.models import (
    Annotation,
    Attachment,
    Collection,
    Identity,
    Item,
    ItemCollection,
    ItemTag,
    Note,
    Space,
    Tag,
    User,
)
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .transform import SHELF_IMPORT_NS, item_uuid


# ── User / space resolution ─────────────────────────────────────────────────


async def resolve_target_space(
    session: AsyncSession,
    *,
    target_identity: str,
    target_email: str,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Return ``(user_id, space_id)`` for the import target.

    Lookup precedence: ``target_identity`` (``<idp>:<sub>``) → email →
    "exactly one user exists, pick them". Anything else raises.
    """
    user: User | None = None
    if target_identity:
        idp, _, sub = target_identity.partition(":")
        if not sub:
            raise RuntimeError("--target-identity must be '<idp>:<subject>'")
        ident = (
            await session.execute(
                select(Identity).where(Identity.idp == idp, Identity.subject == sub)
            )
        ).scalar_one_or_none()
        if not ident:
            raise RuntimeError(f"no Shelf identity for {target_identity!r}")
        user = await session.get(User, ident.user_id)
    elif target_email:
        user = (
            await session.execute(select(User).where(User.email == target_email))
        ).scalar_one_or_none()
        if not user:
            raise RuntimeError(f"no Shelf user with email {target_email!r}")
    else:
        users = list((await session.execute(select(User))).scalars())
        if len(users) != 1:
            raise RuntimeError(
                f"target user not specified and {len(users)} users exist; "
                "set MIGRATE_TARGET_IDENTITY or MIGRATE_TARGET_EMAIL"
            )
        user = users[0]

    assert user is not None
    space = (
        await session.execute(select(Space).where(Space.owner_id == user.id))
    ).scalar_one_or_none()
    if not space:
        raise RuntimeError(f"user {user.email} has no owned space")
    return user.id, space.id


# ── Items ───────────────────────────────────────────────────────────────────


async def upsert_items(
    session: AsyncSession,
    rows: Sequence[dict[str, Any]],
    *,
    space_id: uuid.UUID,
    created_by: uuid.UUID,
) -> int:
    if not rows:
        return 0
    payload = [
        {
            "id": r["id"],
            "space_id": space_id,
            "item_type": r["item_type"],
            "data": r["data"],
            "created_by": created_by,
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]
    stmt = pg_insert(Item).values(payload)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Item.id],
        set_={
            "item_type": stmt.excluded.item_type,
            "data": stmt.excluded.data,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    await session.execute(stmt)
    return len(payload)


# ── Synthetic parents for orphaned attachments / notes ──────────────────────


def synthetic_parent_uuid(legacy_key: str) -> uuid.UUID:
    """Stable UUID for the placeholder item that adopts a standalone
    attachment/note. Seeded off the orphan's own key so re-runs
    converge on the same row."""
    return uuid.uuid5(SHELF_IMPORT_NS, f"synthetic-parent:{legacy_key}")


async def materialise_synthetic_parents(
    session: AsyncSession,
    *,
    space_id: uuid.UUID,
    created_by: uuid.UUID,
    orphan_legacy_keys_with_titles: Sequence[tuple[str, str]],
) -> dict[str, uuid.UUID]:
    """Insert one item per orphan and return ``{legacy_key:
    item_uuid}``. ``item_type='document'`` so they show up as plain
    items in the UI."""
    if not orphan_legacy_keys_with_titles:
        return {}
    payload = []
    mapping: dict[str, uuid.UUID] = {}
    now = datetime.now()
    for legacy_key, title in orphan_legacy_keys_with_titles:
        u = synthetic_parent_uuid(legacy_key)
        mapping[legacy_key] = u
        payload.append(
            {
                "id": u,
                "space_id": space_id,
                "item_type": "document",
                "data": {"title": title or "Imported attachment"},
                "created_by": created_by,
                "created_at": now,
                "updated_at": now,
            }
        )
    stmt = pg_insert(Item).values(payload).on_conflict_do_nothing(
        index_elements=[Item.id]
    )
    await session.execute(stmt)
    return mapping


# ── Collections ─────────────────────────────────────────────────────────────


async def upsert_collections(
    session: AsyncSession,
    rows: Sequence[dict[str, Any]],
    *,
    space_id: uuid.UUID,
    legacy_id_to_uuid: dict[int, uuid.UUID],
) -> int:
    """Insert parents before children, in one pass per layer."""
    if not rows:
        return 0
    pending = list(rows)
    inserted_uuids: set[uuid.UUID] = set()
    rounds = 0
    while pending and rounds < 32:
        rounds += 1
        ready: list[dict[str, Any]] = []
        deferred: list[dict[str, Any]] = []
        for r in pending:
            parent_legacy = r["parent_legacy_id"]
            if parent_legacy is None:
                ready.append(r)
                continue
            parent_uuid = legacy_id_to_uuid.get(parent_legacy)
            if parent_uuid and (
                parent_uuid in inserted_uuids
                or await _collection_exists(session, parent_uuid)
            ):
                ready.append(r)
            else:
                deferred.append(r)
        if not ready:
            # Cycle or dangling parent — flatten the remainder to root.
            for r in deferred:
                r["parent_legacy_id"] = None
            ready, deferred = deferred, []
        payload = [
            {
                "id": r["id"],
                "space_id": space_id,
                "parent_id": (
                    legacy_id_to_uuid.get(r["parent_legacy_id"])
                    if r["parent_legacy_id"] is not None
                    else None
                ),
                "name": r["name"],
            }
            for r in ready
        ]
        stmt = pg_insert(Collection).values(payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Collection.id],
            set_={
                "name": stmt.excluded.name,
                "parent_id": stmt.excluded.parent_id,
            },
        )
        await session.execute(stmt)
        inserted_uuids.update(r["id"] for r in ready)
        pending = deferred
    return len(rows)


async def _collection_exists(session: AsyncSession, cid: uuid.UUID) -> bool:
    return (
        await session.execute(select(Collection.id).where(Collection.id == cid))
    ).scalar_one_or_none() is not None


async def upsert_item_collections(
    session: AsyncSession,
    pairs: Sequence[tuple[int, int]],
    *,
    item_legacy_to_uuid: dict[int, uuid.UUID],
    collection_legacy_to_uuid: dict[int, uuid.UUID],
) -> int:
    payload = []
    for item_legacy_id, collection_legacy_id in pairs:
        iu = item_legacy_to_uuid.get(item_legacy_id)
        cu = collection_legacy_to_uuid.get(collection_legacy_id)
        if iu and cu:
            payload.append({"item_id": iu, "collection_id": cu})
    if not payload:
        return 0
    stmt = pg_insert(ItemCollection).values(payload).on_conflict_do_nothing(
        index_elements=[ItemCollection.item_id, ItemCollection.collection_id]
    )
    await session.execute(stmt)
    return len(payload)


# ── Tags ────────────────────────────────────────────────────────────────────


async def upsert_tags(
    session: AsyncSession,
    rows: Sequence[dict[str, Any]],
    *,
    space_id: uuid.UUID,
) -> dict[int, uuid.UUID]:
    """Tags dedup on (space_id, name) — Zotero auto-tags can produce
    near-duplicates that differ only in case, and Shelf's CITEXT name
    will collapse those. We resolve the final UUID per legacy tagID
    by selecting after insert.
    """
    if not rows:
        return {}
    payload = [
        {
            "id": uuid.uuid4(),
            "space_id": space_id,
            "name": r["name"],
        }
        for r in rows
    ]
    stmt = pg_insert(Tag).values(payload).on_conflict_do_nothing(
        index_elements=[Tag.space_id, Tag.name]
    )
    await session.execute(stmt)
    # Resolve tagID → final UUID via a single SELECT.
    name_to_legacy: dict[str, int] = {r["name"]: r["legacy_id"] for r in rows}
    existing = (
        await session.execute(
            select(Tag.id, Tag.name).where(
                Tag.space_id == space_id, Tag.name.in_(list(name_to_legacy.keys()))
            )
        )
    ).all()
    return {name_to_legacy[name]: tid for tid, name in existing if name in name_to_legacy}


async def upsert_item_tags(
    session: AsyncSession,
    pairs: Sequence[tuple[int, int]],
    *,
    item_legacy_to_uuid: dict[int, uuid.UUID],
    tag_legacy_to_uuid: dict[int, uuid.UUID],
) -> int:
    payload = []
    for item_legacy_id, tag_legacy_id in pairs:
        iu = item_legacy_to_uuid.get(item_legacy_id)
        tu = tag_legacy_to_uuid.get(tag_legacy_id)
        if iu and tu:
            payload.append({"item_id": iu, "tag_id": tu})
    if not payload:
        return 0
    stmt = pg_insert(ItemTag).values(payload).on_conflict_do_nothing(
        index_elements=[ItemTag.item_id, ItemTag.tag_id]
    )
    await session.execute(stmt)
    return len(payload)


# ── Attachments ─────────────────────────────────────────────────────────────


def attachment_storage_key(item_uuid_: uuid.UUID, att_uuid: uuid.UUID) -> str:
    """Match the layout that ``api/attachments.py`` produces for new
    uploads, so imported rows are indistinguishable from native ones."""
    return f"items/{item_uuid_}/attachments/{att_uuid}"


async def upsert_attachments(
    session: AsyncSession,
    rows: Sequence[dict[str, Any]],
    *,
    item_legacy_to_uuid: dict[int, uuid.UUID],
    created_by: uuid.UUID,
    uploaded_at: datetime | None,
) -> int:
    """Each row needs ``parent_uuid`` already filled in (either real
    parent item or synthetic placeholder). The blob copy is run
    separately in the orchestrator after this returns."""
    if not rows:
        return 0
    payload = []
    for r in rows:
        item_id = item_legacy_to_uuid.get(r["parent_legacy_id"]) if r["parent_legacy_id"] else None
        item_id = r.get("parent_uuid_override") or item_id
        if not item_id:
            continue
        att_id = r["id"]
        payload.append(
            {
                "id": att_id,
                "item_id": item_id,
                "storage_key": attachment_storage_key(item_id, att_id),
                "filename": r["filename"],
                "content_type": r["content_type"],
                "size_bytes": r.get("size_bytes"),
                "uploaded_at": uploaded_at,
                "created_by": created_by,
            }
        )
    if not payload:
        return 0
    stmt = pg_insert(Attachment).values(payload)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Attachment.id],
        set_={
            "filename": stmt.excluded.filename,
            "content_type": stmt.excluded.content_type,
            "size_bytes": stmt.excluded.size_bytes,
            "uploaded_at": stmt.excluded.uploaded_at,
        },
    )
    await session.execute(stmt)
    return len(payload)


# ── Notes ───────────────────────────────────────────────────────────────────


async def upsert_notes(
    session: AsyncSession,
    rows: Sequence[dict[str, Any]],
    *,
    item_legacy_to_uuid: dict[int, uuid.UUID],
) -> int:
    if not rows:
        return 0
    payload = []
    for r in rows:
        item_id = item_legacy_to_uuid.get(r["parent_legacy_id"]) if r["parent_legacy_id"] else None
        item_id = r.get("parent_uuid_override") or item_id
        if not item_id:
            continue
        payload.append(
            {
                "id": r["id"],
                "item_id": item_id,
                "content_html": r["content_html"],
                "content_text": r["content_text"],
            }
        )
    if not payload:
        return 0
    stmt = pg_insert(Note).values(payload)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Note.id],
        set_={
            "content_html": stmt.excluded.content_html,
            "content_text": stmt.excluded.content_text,
        },
    )
    await session.execute(stmt)
    return len(payload)


# ── Annotations ─────────────────────────────────────────────────────────────


async def upsert_annotations(
    session: AsyncSession,
    rows: Sequence[dict[str, Any]],
    *,
    attachment_legacy_to_uuid: dict[int, uuid.UUID],
    created_by: uuid.UUID,
) -> int:
    if not rows:
        return 0
    payload = []
    for r in rows:
        att_uuid = attachment_legacy_to_uuid.get(r["parent_attachment_legacy_id"])
        if not att_uuid:
            continue
        payload.append(
            {
                "id": r["id"],
                "attachment_id": att_uuid,
                "kind": r["kind"],
                "page_number": r["page_number"],
                "rects": r["rects"],
                "color": r["color"],
                "text": r["text"],
                "created_by": created_by,
            }
        )
    if not payload:
        return 0
    stmt = pg_insert(Annotation).values(payload)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Annotation.id],
        set_={
            "kind": stmt.excluded.kind,
            "page_number": stmt.excluded.page_number,
            "rects": stmt.excluded.rects,
            "color": stmt.excluded.color,
            "text": stmt.excluded.text,
        },
    )
    await session.execute(stmt)
    return len(payload)


# ── Helpers used by the orchestrator ────────────────────────────────────────


def reverse_item_uuid_map(items: Sequence[dict[str, Any]]) -> dict[int, uuid.UUID]:
    """Map ``legacy_item_id`` → final ``UUID`` for every regular item."""
    return {it["legacy_item_id"]: item_uuid(it["legacy_key"]) for it in items}
