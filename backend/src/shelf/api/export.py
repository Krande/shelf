"""Bibliographic export — single-item and bulk.

Wraps the renderers in services/export_renderers behind two HTTP
endpoints. The bulk endpoint reuses the list-items filter surface
(?q=, ?tag=, ?collection=, ?status=) so the user can scope an export
to whatever filtered view they had in the UI.
"""

import hashlib
import io
import logging
import re
import uuid
import zipfile
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from itertools import batched
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import __version__, archive_format
from ..archive_format import (
    ArchiveCollection,
    ArchiveFile,
    ArchiveIndex,
    ArchiveItem,
    ArchiveMissing,
    ArchiveSource,
    ArchiveSpace,
    ArchiveTag,
)
from ..auth.deps import get_current_user
from ..auth.spaces import SPACE_ROLE_VIEWER, require_space_role
from ..db import get_session
from ..models import (
    Attachment,
    Collection,
    Item,
    ItemCollection,
    ItemTag,
    Space,
    Tag,
    User,
)
from ..services import storage
from ..services.audit import AuditAction, item_label, record_read
from ..services.export_renderers import (
    attachment_zip_path,
    render_bibtex,
    render_csl_json,
    render_zotero_rdf,
)
from ..services.zip_stream import ZipStream
from .attachments import current_versions

log = logging.getLogger(__name__)

router = APIRouter(tags=["export"])


class ExportFormat(StrEnum):
    bibtex = "bibtex"
    csl_json = "csl-json"
    rdf = "rdf"


_FORMAT_META: dict[ExportFormat, tuple[str, str]] = {
    # (mime, file extension)
    ExportFormat.bibtex: ("application/x-bibtex", "bib"),
    ExportFormat.csl_json: ("application/vnd.citationstyles.csl+json", "json"),
    ExportFormat.rdf: ("application/rdf+xml", "rdf"),
}


async def _resolve_space(db: AsyncSession, user: User, slug: str) -> Space:
    result = await db.execute(select(Space).where(Space.slug == slug))
    space = result.scalar_one_or_none()
    # Export is read-only, so viewer is enough throughout this router.
    await require_space_role(
        db, space, user.id, SPACE_ROLE_VIEWER, label="Space not found"
    )
    assert space is not None  # require_space_role raises when it isn't
    return space


async def _resolve_item(db: AsyncSession, user: User, item_id: uuid.UUID) -> Item:
    item = await db.get(Item, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    space = await db.get(Space, item.space_id)
    await require_space_role(
        db, space, user.id, SPACE_ROLE_VIEWER, label="Item not found"
    )
    return item


async def _hydrate(
    db: AsyncSession, items: list[Item]
) -> tuple[
    dict[uuid.UUID, list[Attachment]],
    dict[uuid.UUID, list[Tag]],
    dict[uuid.UUID, list[Collection]],
    dict[uuid.UUID, Collection],
]:
    """Fetch attachments, tags, collections + the index of every
    collection referenced by these items (including ancestors). Uses
    a small handful of bulk SELECTs so the serializer can stay sync.
    """
    if not items:
        return {}, {}, {}, {}
    ids = [i.id for i in items]

    att_rows = await db.execute(
        select(Attachment).where(Attachment.item_id.in_(ids))
    )
    attachments_by_item: dict[uuid.UUID, list[Attachment]] = {}
    for att in att_rows.scalars().all():
        attachments_by_item.setdefault(att.item_id, []).append(att)

    tag_rows = await db.execute(
        select(Tag, ItemTag.item_id)
        .join(ItemTag, ItemTag.tag_id == Tag.id)
        .where(ItemTag.item_id.in_(ids))
    )
    tags_by_item: dict[uuid.UUID, list[Tag]] = {}
    for tag, item_id in tag_rows.all():
        tags_by_item.setdefault(item_id, []).append(tag)

    coll_rows = await db.execute(
        select(Collection, ItemCollection.item_id)
        .join(ItemCollection, ItemCollection.collection_id == Collection.id)
        .where(ItemCollection.item_id.in_(ids))
    )
    collections_by_item: dict[uuid.UUID, list[Collection]] = {}
    coll_index: dict[uuid.UUID, Collection] = {}
    for coll, item_id in coll_rows.all():
        collections_by_item.setdefault(item_id, []).append(coll)
        coll_index[coll.id] = coll

    # Walk parents so the RDF emitter can chain dcterms:isPartOf
    # without re-querying.
    parents_to_fetch: set[uuid.UUID] = set()
    for c in coll_index.values():
        if c.parent_id and c.parent_id not in coll_index:
            parents_to_fetch.add(c.parent_id)
    while parents_to_fetch:
        rows = await db.execute(
            select(Collection).where(Collection.id.in_(parents_to_fetch))
        )
        next_parents: set[uuid.UUID] = set()
        for c in rows.scalars().all():
            coll_index[c.id] = c
            if c.parent_id and c.parent_id not in coll_index:
                next_parents.add(c.parent_id)
        parents_to_fetch = next_parents

    return attachments_by_item, tags_by_item, collections_by_item, coll_index


def _render(
    fmt: ExportFormat,
    items: list[Item],
    *,
    attachments_by_item: dict[uuid.UUID, list[Attachment]],
    tags_by_item: dict[uuid.UUID, list[Tag]],
    collections_by_item: dict[uuid.UUID, list[Collection]],
    collections_index: dict[uuid.UUID, Collection],
    include_file_paths: bool = False,
) -> str:
    if fmt is ExportFormat.bibtex:
        return render_bibtex(items)
    if fmt is ExportFormat.csl_json:
        return render_csl_json(items)
    return render_zotero_rdf(
        items,
        attachments_by_item=attachments_by_item,
        tags_by_item=tags_by_item,
        collections_by_item=collections_by_item,
        collections_index=collections_index,
        include_file_paths=include_file_paths,
    )


async def _build_rdf_bundle(
    *,
    base_name: str,
    items: list[Item],
    attachments_by_item: dict[uuid.UUID, list[Attachment]],
    tags_by_item: dict[uuid.UUID, list[Tag]],
    collections_by_item: dict[uuid.UUID, list[Collection]],
    collections_index: dict[uuid.UUID, Collection],
) -> bytes:
    """Assemble a Zotero-RDF + files ZIP. Layout matches what Zotero's
    own translator produces: ``<base_name>.rdf`` at the root and a
    sibling ``files/<attachment-id>/<filename>`` directory tree."""
    rdf_text = render_zotero_rdf(
        items,
        attachments_by_item=attachments_by_item,
        tags_by_item=tags_by_item,
        collections_by_item=collections_by_item,
        collections_index=collections_index,
        include_file_paths=True,
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{base_name}.rdf", rdf_text)
        for atts in attachments_by_item.values():
            for att in atts:
                try:
                    body = await storage.read_object(att.storage_key)
                except Exception as exc:
                    # A missing file shouldn't sink the whole export.
                    # Log and write a stub so the user notices the gap
                    # without the import failing silently.
                    log.warning(
                        "rdf-export: skipping attachment %s (%s): %s",
                        att.id,
                        att.filename,
                        exc,
                    )
                    z.writestr(
                        attachment_zip_path(att) + ".missing.txt",
                        f"could not fetch {att.storage_key}: {exc}",
                    )
                    continue
                z.writestr(attachment_zip_path(att), body)
    return buf.getvalue()


@router.get("/api/items/{item_id}/export")
async def export_item(
    item_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    format: Annotated[ExportFormat, Query()] = ExportFormat.bibtex,
    bundle: Annotated[bool, Query()] = False,
) -> Response:
    """Single-item export. ``bundle=true`` (RDF only) returns a ZIP
    with the .rdf file alongside ``files/<id>/<filename>`` for every
    attachment — same shape as Zotero's native export."""
    item = await _resolve_item(db, user, item_id)
    att, tags, colls, idx = await _hydrate(db, [item])
    bundled = format is ExportFormat.rdf and bundle
    details: dict[str, object] = {"format": format.value}
    if bundled:
        details["files"] = sum(len(v) for v in att.values())
    await record_read(
        db,
        user,
        AuditAction.export_item,
        space_id=item.space_id,
        target_type="item",
        target_id=item.id,
        label=item_label(item),
        details=details,
    )
    if bundled:
        zip_bytes = await _build_rdf_bundle(
            base_name=f"item-{item.id.hex[:8]}",
            items=[item],
            attachments_by_item=att,
            tags_by_item=tags,
            collections_by_item=colls,
            collections_index=idx,
        )
        filename = f"item-{item.id.hex[:8]}.zip"
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            },
        )
    body = _render(
        format,
        [item],
        attachments_by_item=att,
        tags_by_item=tags,
        collections_by_item=colls,
        collections_index=idx,
    )
    mime, ext = _FORMAT_META[format]
    filename = f"item-{item.id.hex[:8]}.{ext}"
    return Response(
        content=body,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/spaces/{slug}/export")
async def export_space(
    slug: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    format: Annotated[ExportFormat, Query()] = ExportFormat.bibtex,
    collection: Annotated[str | None, Query(max_length=64)] = None,
    tag: Annotated[list[str] | None, Query()] = None,
    bundle: Annotated[bool, Query()] = True,
) -> Response:
    """Bulk export. RDF defaults to ``bundle=true`` (ZIP with files/),
    matching Zotero's behaviour for whole-library exports. BibTeX and
    CSL-JSON ignore ``bundle`` — there's no portable way to bundle
    files for those formats."""
    space = await _resolve_space(db, user, slug)
    stmt = select(Item).where(
        Item.space_id == space.id, Item.deleted_at.is_(None)
    )
    if collection:
        try:
            cid = uuid.UUID(collection)
        except ValueError as e:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "collection must be a UUID"
            ) from e
        stmt = stmt.join(
            ItemCollection, ItemCollection.item_id == Item.id
        ).where(ItemCollection.collection_id == cid)
    if tag:
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
    stmt = stmt.order_by(Item.created_at)
    rows = await db.execute(stmt)
    items = list(rows.scalars().all())
    att, tags, colls, idx = await _hydrate(db, items)
    bundled = format is ExportFormat.rdf and bundle
    details: dict[str, object] = {"format": format.value, "items": len(items)}
    if bundled:
        details["files"] = sum(len(v) for v in att.values())
    if collection:
        details["collection_id"] = collection
    if tag:
        details["tags"] = [t.strip() for t in tag if t.strip()]
    await record_read(
        db,
        user,
        AuditAction.export_space,
        space_id=space.id,
        target_type="space",
        target_id=space.id,
        label=space.name,
        details=details,
    )
    if bundled:
        zip_bytes = await _build_rdf_bundle(
            base_name=slug,
            items=items,
            attachments_by_item=att,
            tags_by_item=tags,
            collections_by_item=colls,
            collections_index=idx,
        )
        filename = f"{slug}.zip"
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            },
        )
    body = _render(
        format,
        items,
        attachments_by_item=att,
        tags_by_item=tags,
        collections_by_item=colls,
        collections_index=idx,
    )
    mime, ext = _FORMAT_META[format]
    filename = f"{slug}.{ext}"
    return Response(
        content=body,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _is_pdf(att: Attachment) -> bool:
    """A PDF attachment by declared type or filename extension. Uploads
    always carry ``application/pdf``; the filename check covers rows
    imported with a generic ``application/octet-stream`` content type."""
    return (
        att.content_type == "application/pdf"
        or att.filename.lower().endswith(".pdf")
    )


def _safe_zip_name(name: str, fallback: str) -> str:
    """Sanitise a filename for a ZIP entry: drop path separators and
    control chars so nothing escapes the archive root, and cap the
    length so pathological titles can't blow up the entry name."""
    cleaned = re.sub(r"[/\\\x00-\x1f]+", "_", name).strip().strip(".")
    return cleaned[:150] or fallback


def _dedupe_name(name: str, used: set[str]) -> str:
    """Return ``name`` if unused, else append ``" (2)"``, ``" (3)"`` …
    before the extension so two documents with the same PDF filename
    both survive in a flat ZIP."""
    if name not in used:
        used.add(name)
        return name
    stem, dot, ext = name.rpartition(".")
    base, suffix = (stem, f".{ext}") if dot else (name, "")
    n = 2
    while True:
        candidate = f"{base} ({n}){suffix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        n += 1


# Ids per IN (...) list. asyncpg caps a statement at 32767 bind
# parameters, and a whole space can hold more documents than that.
_IN_BATCH = 5000


@dataclass
class _Tree:
    """The collections an archive covers, and what is filed in them."""

    # Parents before children, siblings in rail order.
    collections: list[ArchiveCollection]
    # Every item filed anywhere in the tree -> the tree's collections it
    # is filed in, most recently updated item first.
    memberships: dict[uuid.UUID, list[uuid.UUID]]
    folders: dict[uuid.UUID, str]

    def folder_of(self, item_id: uuid.UUID) -> str:
        """The ZIP folder an item's PDFs go in: the shallowest of its
        collections, so an item filed in several is written once. An
        item in none of them (whole-space archives) goes at the root."""
        return min(
            (self.folders[cid] for cid in self.memberships.get(item_id, [])),
            key=lambda f: f.count("/") + 1 if f else 0,
            default="",
        )


async def _collection_tree(
    db: AsyncSession, space_id: uuid.UUID, collection_id: uuid.UUID | None
) -> _Tree:
    """One of this space's collections and its subcollections to any
    depth -- or, with no ``collection_id``, all of the space's
    collections -- and the items filed in any of them.

    For one collection, its own documents sit at the archive root and
    each subcollection becomes a folder, so the download mirrors the
    tree in the rail. A collection that only groups subcollections -- no
    documents of its own -- still downloads everything below it rather
    than coming back empty. For the whole space every top-level
    collection is a folder, and the root holds what isn't filed at all.
    Empty collections are kept in the index either way, so an import
    rebuilds the tree as it was.

    A collection belonging to some other space is a 404 rather than an
    empty archive, for the same reason the item listing rejects one: a
    filter that silently matches nothing reads as "there is nothing
    here".
    """
    # The whole space's tree in one query and walked here: it's small,
    # and the walk needs the names to build folder paths anyway.
    rows = (
        await db.execute(
            select(Collection)
            .where(Collection.space_id == space_id)
            .order_by(Collection.position)
        )
    ).scalars().all()
    by_id = {c.id: c for c in rows}
    if collection_id is not None and collection_id not in by_id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No such collection in this space"
        )
    children: dict[uuid.UUID | None, list[Collection]] = {}
    for c in rows:
        children.setdefault(c.parent_id, []).append(c)
    roots = (
        [by_id[collection_id]] if collection_id is not None else children.get(None, [])
    )

    collections: list[ArchiveCollection] = []
    folders: dict[uuid.UUID, str] = {}
    # Reversed so the first sibling is popped, and listed, first.
    stack = list(reversed(roots))
    while stack:
        c = stack.pop()
        if c.id in folders:
            continue
        if c.id == collection_id:
            # The exported collection *is* the archive root.
            folder = ""
            parent_id = None
        else:
            seg = _safe_zip_name(c.name, c.id.hex[:8])
            parent = folders.get(c.parent_id, "") if c.parent_id else ""
            folder = f"{parent}/{seg}" if parent else seg
            parent_id = c.parent_id
        folders[c.id] = folder
        collections.append(
            ArchiveCollection(
                id=c.id,
                parent_id=parent_id,
                name=c.name,
                description=c.description,
                folder=folder,
            )
        )
        stack.extend(reversed(children.get(c.id, [])))

    stmt = (
        select(Item.id, ItemCollection.collection_id)
        .join(ItemCollection, ItemCollection.item_id == Item.id)
        .where(Item.space_id == space_id, Item.deleted_at.is_(None))
        .order_by(Item.updated_at.desc())
    )
    if collection_id is not None:
        stmt = stmt.where(ItemCollection.collection_id.in_(list(folders)))
    else:
        # A join rather than IN (...): a big space's collection ids
        # could outnumber the bind parameters one statement may carry.
        stmt = stmt.join(
            Collection, Collection.id == ItemCollection.collection_id
        ).where(Collection.space_id == space_id)
    memberships: dict[uuid.UUID, list[uuid.UUID]] = {}
    for item_id, cid in (await db.execute(stmt)).all():
        memberships.setdefault(item_id, []).append(cid)
    return _Tree(
        collections=collections, memberships=memberships, folders=folders
    )


@dataclass
class _ZipFile:
    """One PDF the archive will hold, resolved before streaming starts.

    Plain values rather than ORM rows, so nothing in the stream can
    reach back into the session.
    """

    item_id: uuid.UUID
    attachment_id: uuid.UUID
    filename: str
    content_type: str
    storage_key: str
    # "original", or the derivation kind the bytes come from.
    version: str
    folder: str


@dataclass
class _ZipPlan:
    items: list[Item]
    files: list[_ZipFile]
    # Everything but the files, which are added as they're written.
    index: ArchiveIndex


async def _plan_pdf_zip(
    db: AsyncSession,
    space: Space,
    item: list[uuid.UUID] | None,
    collection: uuid.UUID | None,
    whole_space: bool = False,
) -> _ZipPlan:
    """Everything the archive needs from the database, in a handful of
    bulk queries, so streaming touches storage only.

    That split matters beyond speed: the response outlives the request
    handler, and a download that held a database session open for as
    long as a slow client takes to pull a few GB would starve the pool.
    """
    if sum([bool(item), collection is not None, whole_space]) != 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "pass exactly one of: item ids, a collection, or whole_space",
        )

    tree: _Tree | None = None
    if collection is not None or whole_space:
        # Resolved here rather than by the caller listing the collection
        # and sending an id per item: a folder with a few hundred
        # documents would otherwise build a query string long enough to
        # be refused, and the membership rule stays in one place.
        tree = await _collection_tree(db, space.id, collection)
    if whole_space:
        # The space's own documents, filed or not; inherited ones belong
        # to the space they live in and are downloaded from there.
        ordered = list(
            (
                await db.execute(
                    select(Item)
                    .where(Item.space_id == space.id, Item.deleted_at.is_(None))
                    .order_by(Item.updated_at.desc())
                )
            ).scalars().all()
        )
    else:
        if tree is not None:
            ids = list(tree.memberships)
        else:
            # De-dupe while preserving the caller's selection order so
            # ZIP entries come out in a predictable sequence.
            assert item is not None
            ids = list(dict.fromkeys(item))
        items_by_id: dict[uuid.UUID, Item] = {}
        for batch in batched(ids, _IN_BATCH):
            for row in (
                await db.execute(
                    select(Item).where(
                        Item.space_id == space.id,
                        Item.deleted_at.is_(None),
                        Item.id.in_(batch),
                    )
                )
            ).scalars():
                items_by_id[row.id] = row
        ordered = [items_by_id[i] for i in ids if i in items_by_id]

    folder = {it.id: tree.folder_of(it.id) if tree else "" for it in ordered}
    if tree is not None:
        # Folder by folder, root first, rather than interleaved by
        # recency; sorted() is stable, so each folder keeps that order.
        ordered.sort(key=lambda it: folder[it.id])
    item_ids = [i.id for i in ordered]

    atts_by_item: dict[uuid.UUID, list[Attachment]] = {}
    tags_by_item: dict[uuid.UUID, list[str]] = {}
    tags: dict[str, ArchiveTag] = {}
    for batch in batched(item_ids, _IN_BATCH):
        for att in (
            await db.execute(
                select(Attachment)
                .where(Attachment.item_id.in_(batch))
                .order_by(Attachment.created_at)
            )
        ).scalars():
            if _is_pdf(att):
                atts_by_item.setdefault(att.item_id, []).append(att)
        for item_id, name, color in (
            await db.execute(
                select(ItemTag.item_id, Tag.name, Tag.color)
                .join(Tag, Tag.id == ItemTag.tag_id)
                .where(ItemTag.item_id.in_(batch))
                .order_by(Tag.name)
            )
        ).all():
            tags_by_item.setdefault(item_id, []).append(name)
            tags.setdefault(name, ArchiveTag(name=name, color=color))

    # The same "current best" blob the single-file download serves
    # (latest outline > latest OCR > original). Reading ``storage_key``
    # directly would miss every attachment whose original was superseded
    # by a derivation -- its original object may no longer exist.
    pdfs = [a for it in ordered for a in atts_by_item.get(it.id, [])]
    current = {}
    for att_batch in batched(pdfs, _IN_BATCH):
        current.update(await current_versions(db, list(att_batch)))

    index = ArchiveIndex(
        format=archive_format.FORMAT,
        version=archive_format.VERSION,
        created_at=datetime.now(UTC),
        generator=f"shelf {__version__}",
        source=ArchiveSource(
            space=ArchiveSpace(id=space.id, slug=space.slug, name=space.name)
        ),
        scope=(
            "space" if whole_space else "collection" if collection else "items"
        ),
        root_collection_id=collection,
        collections=tree.collections if tree else [],
        tags=sorted(tags.values(), key=lambda t: t.name.casefold()),
        items=[
            ArchiveItem(
                id=it.id,
                origin_id=it.origin_id,
                item_type=it.item_type,
                data=dict(it.data) if isinstance(it.data, dict) else {},
                tags=tags_by_item.get(it.id, []),
                collection_ids=tree.memberships.get(it.id, []) if tree else [],
            )
            for it in ordered
        ],
    )
    return _ZipPlan(
        items=ordered,
        files=[
            _ZipFile(
                item_id=it.id,
                attachment_id=att.id,
                filename=att.filename,
                content_type=att.content_type,
                storage_key=current[att.id].storage_key,
                version=current[att.id].kind,
                folder=folder[it.id],
            )
            for it in ordered
            for att in atts_by_item.get(it.id, [])
        ],
        index=index,
    )


class _Tally:
    """Size and SHA-256 of the bytes passing through, for the index."""

    def __init__(self) -> None:
        self.sha256 = hashlib.sha256()
        self.size = 0

    async def wrap(self, chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
        async for chunk in chunks:
            self.sha256.update(chunk)
            self.size += len(chunk)
            yield chunk


async def _stream_pdf_zip(plan: _ZipPlan) -> AsyncIterator[bytes]:
    """The archive, one chunk at a time, ending with its index.json.

    Memory stays at roughly one storage chunk however big the archive
    gets: each PDF is copied from storage into the response as it
    arrives, and the ASGI server's flow control holds the copy back to
    the pace the client downloads at.

    A PDF that can't be opened is left out, listed in the index's
    ``missing`` and in _MISSING_FILES.txt. One that fails partway
    through (after its resumes are used up) can't be taken back out of
    bytes already sent, so the stream is aborted and the browser reports
    the download as failed rather than saving a corrupt archive.
    """
    zs = ZipStream()
    used: set[str] = set()
    missing: list[str] = []
    index = plan.index
    entries = {it.id: it for it in index.items}
    async with storage.object_client() as client:
        for f in plan.files:
            name = _safe_zip_name(f.filename, f"{f.attachment_id.hex[:8]}.pdf")
            if not name.lower().endswith(".pdf"):
                name += ".pdf"
            arcname = _dedupe_name(
                f"{f.folder}/{name}" if f.folder else name, used
            )
            tally = _Tally()
            async with AsyncExitStack() as stack:
                try:
                    size, chunks = await stack.enter_async_context(
                        storage.stream_object(f.storage_key, client)
                    )
                except Exception as exc:
                    used.discard(arcname)
                    missing.append(
                        f"{f.folder}/{f.filename}" if f.folder else f.filename
                    )
                    index.missing.append(
                        ArchiveMissing(item_id=f.item_id, filename=f.filename)
                    )
                    log.warning(
                        "pdf-zip: skipping attachment %s (%s) key=%s: %s",
                        f.attachment_id,
                        f.filename,
                        f.storage_key,
                        exc,
                    )
                    continue
                try:
                    async for out in zs.add(arcname, size, tally.wrap(chunks)):
                        yield out
                except Exception:
                    log.exception(
                        "pdf-zip: aborting; attachment %s (%s) key=%s failed "
                        "mid-stream",
                        f.attachment_id,
                        f.filename,
                        f.storage_key,
                    )
                    raise
            entries[f.item_id].files.append(
                ArchiveFile(
                    path=arcname,
                    filename=f.filename,
                    content_type=f.content_type,
                    size=tally.size,
                    sha256=tally.sha256.hexdigest(),
                    version=f.version,
                )
            )

    if missing:
        yield zs.add_bytes(
            "_MISSING_FILES.txt",
            (
                "These PDFs could not be fetched from storage and were "
                "left out of this archive:\n\n"
                + "\n".join(sorted(missing))
                + "\n"
            ).encode(),
        )
    # Last, so it describes what actually made it in.
    yield zs.add_bytes(
        "index.json", index.model_dump_json(indent=2).encode()
    )
    yield zs.close()


_ITEM_QUERY = Query(description="Item ids to bundle; repeat the param per id.")
_COLLECTION_QUERY = Query(
    description=(
        "Bundle every item filed under this collection or any of its "
        "subcollections (one ZIP folder per subcollection), instead of "
        "naming ids."
    )
)
_WHOLE_SPACE_QUERY = Query(
    description=(
        "Bundle every document of the space: all its collections as "
        "folders, and what is filed in none at the archive root."
    )
)


class PdfZipSummary(BaseModel):
    items: int
    files: int


@router.get("/api/spaces/{slug}/attachments-zip/summary")
async def summarize_attachments_zip(
    slug: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    item: Annotated[list[uuid.UUID] | None, _ITEM_QUERY] = None,
    collection: Annotated[uuid.UUID | None, _COLLECTION_QUERY] = None,
    whole_space: Annotated[bool, _WHOLE_SPACE_QUERY] = False,
) -> PdfZipSummary:
    """What `attachments-zip` would bundle for the same parameters,
    without fetching anything from storage.

    The SPA asks this first and then hands the archive itself to the
    browser as a plain download, which streams it to disk with the
    browser's own progress UI. A navigation can't report an error
    readably, so the cases worth a message -- nothing to download, a
    bad selection -- are caught here instead.
    """
    space = await _resolve_space(db, user, slug)
    plan = await _plan_pdf_zip(db, space, item, collection, whole_space)
    return PdfZipSummary(items=len(plan.items), files=len(plan.files))


@router.get("/api/spaces/{slug}/attachments-zip")
async def download_attachments_zip(
    slug: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    item: Annotated[list[uuid.UUID] | None, _ITEM_QUERY] = None,
    collection: Annotated[uuid.UUID | None, _COLLECTION_QUERY] = None,
    whole_space: Annotated[bool, _WHOLE_SPACE_QUERY] = False,
) -> StreamingResponse:
    """Bundle the PDFs of a selection of documents, a collection, or a
    whole space into one ZIP, with an ``index.json`` (format
    ``shelf.archive``, see ``archive_format``) that makes it
    re-importable: each document's metadata, tags and collections, and
    the collection tree. Flat for a selection; one folder per collection
    otherwise. Used by the library's bulk-select "Download PDFs", by
    "Download PDFs" on a collection, and by "Download space".

    Streamed: the first bytes go out as soon as the first PDF is opened,
    and nothing is held beyond the chunk in flight. Non-PDF attachments
    are skipped; a missing blob is logged and listed in
    _MISSING_FILES.txt rather than failing the whole download. 404 if
    none of the selected items has a PDF."""
    space = await _resolve_space(db, user, slug)
    plan = await _plan_pdf_zip(db, space, item, collection, whole_space)
    if not plan.items:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No matching items")
    if not plan.files:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No PDF attachments in the selected items",
        )

    # Recorded before streaming: the handler returns once the response
    # starts, and what was asked for is what the log is about. A PDF
    # missing from storage shows up in the archive's _MISSING_FILES.txt.
    details: dict[str, object] = {
        "items": len(plan.items),
        "files": len(plan.files),
    }
    if collection is not None:
        coll = await db.get(Collection, collection)
        await record_read(
            db,
            user,
            AuditAction.collection_download,
            space_id=space.id,
            target_type="collection",
            target_id=collection,
            label=coll.name if coll is not None else None,
            details=details,
        )
    elif whole_space:
        await record_read(
            db,
            user,
            AuditAction.space_download,
            space_id=space.id,
            target_type="space",
            target_id=space.id,
            label=space.name,
            details=details,
        )
    else:
        await record_read(
            db,
            user,
            AuditAction.export_zip,
            space_id=space.id,
            target_type="space",
            target_id=space.id,
            label=space.name,
            details=details,
        )

    filename = f"{slug}-pdfs.zip"
    return StreamingResponse(
        _stream_pdf_zip(plan),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # A reverse proxy that buffers the response would hold the
            # whole archive again -- in its memory or on its disk -- and
            # the client would see nothing until it was done.
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-store",
        },
    )
