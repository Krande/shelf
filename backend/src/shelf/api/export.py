"""Bibliographic export — single-item and bulk.

Wraps the renderers in services/export_renderers behind two HTTP
endpoints. The bulk endpoint reuses the list-items filter surface
(?q=, ?tag=, ?collection=, ?status=) so the user can scope an export
to whatever filtered view they had in the UI.
"""

import io
import json
import logging
import re
import uuid
import zipfile
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


async def _collection_item_folders(
    db: AsyncSession, space_id: uuid.UUID, collection_id: uuid.UUID
) -> dict[uuid.UUID, str]:
    """Items filed under one of this space's collections or any of its
    subcollections, each mapped to the ZIP folder it belongs in.

    The collection's own documents sit at the archive root and each
    subcollection becomes a folder, so the download mirrors the tree in
    the rail. A collection that only groups subcollections -- no
    documents of its own -- still downloads everything below it rather
    than coming back empty. An item filed in several places in the tree
    is written once, under the shallowest of them.

    A collection belonging to some other space is a 404 rather than an
    empty archive, for the same reason the item listing rejects one: a
    filter that silently matches nothing reads as "there is nothing
    here".
    """
    owner_space_id = (
        await db.execute(
            select(Collection.space_id).where(Collection.id == collection_id)
        )
    ).scalar_one_or_none()
    if owner_space_id != space_id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No such collection in this space"
        )
    # The whole space's tree in one query and walked here: it's small,
    # and the walk needs the names to build folder paths anyway.
    children: dict[uuid.UUID, list[tuple[uuid.UUID, str]]] = {}
    for cid, parent_id, name in (
        await db.execute(
            select(Collection.id, Collection.parent_id, Collection.name).where(
                Collection.space_id == space_id
            )
        )
    ).all():
        if parent_id is not None:
            children.setdefault(parent_id, []).append((cid, name))
    folders: dict[uuid.UUID, str] = {collection_id: ""}
    stack = [collection_id]
    while stack:
        parent = stack.pop()
        for cid, name in children.get(parent, []):
            if cid in folders:
                continue
            seg = _safe_zip_name(name, cid.hex[:8])
            folders[cid] = f"{folders[parent]}/{seg}" if folders[parent] else seg
            stack.append(cid)

    rows = await db.execute(
        select(Item.id, ItemCollection.collection_id)
        .join(ItemCollection, ItemCollection.item_id == Item.id)
        .where(
            ItemCollection.collection_id.in_(list(folders)),
            Item.space_id == space_id,
            Item.deleted_at.is_(None),
        )
        .order_by(Item.updated_at.desc())
    )

    def depth(folder: str) -> int:
        return folder.count("/") + 1 if folder else 0

    placed: dict[uuid.UUID, str] = {}
    for item_id, cid in rows.all():
        prev = placed.get(item_id)
        if prev is None or depth(folders[cid]) < depth(prev):
            placed[item_id] = folders[cid]
    return placed


@dataclass
class _ZipFile:
    """One PDF the archive will hold, resolved before streaming starts.

    Plain values rather than ORM rows, so nothing in the stream can
    reach back into the session.
    """

    item_id: uuid.UUID
    title: str
    attachment_id: uuid.UUID
    filename: str
    storage_key: str
    folder: str


@dataclass
class _ZipPlan:
    items: list[Item]
    files: list[_ZipFile]


async def _plan_pdf_zip(
    db: AsyncSession,
    space: Space,
    item: list[uuid.UUID] | None,
    collection: uuid.UUID | None,
) -> _ZipPlan:
    """Everything the archive needs from the database, in a handful of
    bulk queries, so streaming touches storage only.

    That split matters beyond speed: the response outlives the request
    handler, and a download that held a database session open for as
    long as a slow client takes to pull a few GB would starve the pool.
    """
    if collection is not None and item:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "pass item ids or a collection, not both",
        )
    if collection is None and not item:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "at least one item id, or a collection, is required",
        )

    if collection is not None:
        # Resolved here rather than by the caller listing the collection
        # and sending an id per item: a folder with a few hundred
        # documents would otherwise build a query string long enough to
        # be refused, and the membership rule stays in one place.
        item_folders = await _collection_item_folders(db, space.id, collection)
    else:
        # De-dupe while preserving the caller's selection order so ZIP
        # entries come out in a predictable sequence.
        assert item is not None
        item_folders = {iid: "" for iid in item}
    ids = list(item_folders)

    rows = await db.execute(
        select(Item).where(
            Item.space_id == space.id,
            Item.deleted_at.is_(None),
            Item.id.in_(ids),
        )
    )
    items_by_id = {i.id: i for i in rows.scalars().all()}
    ordered = [items_by_id[i] for i in ids if i in items_by_id]
    if collection is not None:
        # Folder by folder, root first, rather than interleaved by
        # recency; sorted() is stable, so each folder keeps that order.
        ordered.sort(key=lambda it: item_folders[it.id])
    if not ordered:
        return _ZipPlan(items=[], files=[])

    att_rows = await db.execute(
        select(Attachment)
        .where(Attachment.item_id.in_([i.id for i in ordered]))
        .order_by(Attachment.created_at)
    )
    atts_by_item: dict[uuid.UUID, list[Attachment]] = {}
    for att in att_rows.scalars().all():
        if _is_pdf(att):
            atts_by_item.setdefault(att.item_id, []).append(att)

    # The same "current best" blob the single-file download serves
    # (latest outline > latest OCR > original). Reading ``storage_key``
    # directly would miss every attachment whose original was superseded
    # by a derivation -- its original object may no longer exist.
    pdfs = [a for it in ordered for a in atts_by_item.get(it.id, [])]
    keys = await current_versions(db, pdfs)
    return _ZipPlan(
        items=ordered,
        files=[
            _ZipFile(
                item_id=it.id,
                title=item_label(it),
                attachment_id=att.id,
                filename=att.filename,
                storage_key=keys[att.id][0],
                folder=item_folders[it.id],
            )
            for it in ordered
            for att in atts_by_item.get(it.id, [])
        ],
    )


async def _stream_pdf_zip(
    files: list[_ZipFile], *, with_collections: bool
) -> AsyncIterator[bytes]:
    """The archive, one chunk at a time.

    Memory stays at roughly one storage chunk however big the archive
    gets: each PDF is copied from storage into the response as it
    arrives, and the ASGI server's flow control holds the copy back to
    the pace the client downloads at.

    A PDF that can't be opened is left out and listed in
    _MISSING_FILES.txt. One that fails partway through (after its
    resumes are used up) can't be taken back out of bytes already sent,
    so the stream is aborted and the browser reports the download as
    failed rather than saving a corrupt archive.
    """
    zs = ZipStream()
    used: set[str] = set()
    index: list[dict[str, str]] = []
    missing: list[str] = []
    async with storage.object_client() as client:
        for f in files:
            name = _safe_zip_name(f.filename, f"{f.attachment_id.hex[:8]}.pdf")
            if not name.lower().endswith(".pdf"):
                name += ".pdf"
            arcname = _dedupe_name(
                f"{f.folder}/{name}" if f.folder else name, used
            )
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
                    log.warning(
                        "pdf-zip: skipping attachment %s (%s) key=%s: %s",
                        f.attachment_id,
                        f.filename,
                        f.storage_key,
                        exc,
                    )
                    continue
                try:
                    async for out in zs.add(arcname, size, chunks):
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
            entry = {
                "path": arcname,
                "title": f.title,
                "item_id": str(f.item_id),
            }
            if with_collections:
                entry["collection"] = f.folder
            index.append(entry)

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
    # Last, so it lists what actually made it in.
    yield zs.add_bytes(
        "index.json",
        json.dumps(index, indent=2, ensure_ascii=False).encode(),
    )
    yield zs.close()


_ITEM_QUERY = Query(description="Item ids to bundle; repeat the param per id.")
_COLLECTION_QUERY = Query(
    description=(
        "Bundle every item filed under this collection or any of its "
        "subcollections (one ZIP folder per subcollection), instead of "
        "naming ids. Mutually exclusive with `item`."
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
    plan = await _plan_pdf_zip(db, space, item, collection)
    return PdfZipSummary(items=len(plan.items), files=len(plan.files))


@router.get("/api/spaces/{slug}/attachments-zip")
async def download_attachments_zip(
    slug: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    item: Annotated[list[uuid.UUID] | None, _ITEM_QUERY] = None,
    collection: Annotated[uuid.UUID | None, _COLLECTION_QUERY] = None,
) -> StreamingResponse:
    """Bundle every PDF attachment of the selected items into one ZIP --
    flat for an item selection, one folder per subcollection for a
    collection -- with an ``index.json`` listing each file's title and
    item. Used by the library's bulk-select "Download PDFs" action and by
    "Download PDFs" on a collection.

    Streamed: the first bytes go out as soon as the first PDF is opened,
    and nothing is held beyond the chunk in flight. Non-PDF attachments
    are skipped; a missing blob is logged and listed in
    _MISSING_FILES.txt rather than failing the whole download. 404 if
    none of the selected items has a PDF."""
    space = await _resolve_space(db, user, slug)
    plan = await _plan_pdf_zip(db, space, item, collection)
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
        _stream_pdf_zip(plan.files, with_collections=collection is not None),
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
