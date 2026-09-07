"""Bibliographic export — single-item and bulk.

Wraps the renderers in services/export_renderers behind two HTTP
endpoints. The bulk endpoint reuses the list-items filter surface
(?q=, ?tag=, ?collection=, ?status=) so the user can scope an export
to whatever filtered view they had in the UI.
"""

import io
import logging
import re
import uuid
import zipfile
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import get_current_user
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
from ..services.export_renderers import (
    attachment_zip_path,
    render_bibtex,
    render_csl_json,
    render_zotero_rdf,
)
from .attachments import _resolve_version

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
    if space is None or space.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Space not found")
    return space


async def _resolve_item(db: AsyncSession, user: User, item_id: uuid.UUID) -> Item:
    item = await db.get(Item, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    space = await db.get(Space, item.space_id)
    if space is None or space.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
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
    if format is ExportFormat.rdf and bundle:
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
    if format is ExportFormat.rdf and bundle:
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


@router.get("/api/spaces/{slug}/attachments-zip")
async def download_attachments_zip(
    slug: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    item: Annotated[
        list[uuid.UUID] | None,
        Query(description="Item ids to bundle; repeat the param per id."),
    ] = None,
) -> Response:
    """Bundle every PDF attachment of the selected items into one flat
    ZIP. Used by the library's bulk-select "Download PDFs" action.
    Non-PDF attachments are skipped; a missing blob is logged and left
    out rather than failing the whole download. 404 if none of the
    selected items yields a PDF."""
    space = await _resolve_space(db, user, slug)
    if not item:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "at least one item id is required"
        )
    # De-dupe while preserving the caller's selection order so ZIP
    # entries come out in a predictable sequence.
    seen: set[uuid.UUID] = set()
    ids: list[uuid.UUID] = []
    for iid in item:
        if iid not in seen:
            seen.add(iid)
            ids.append(iid)

    rows = await db.execute(
        select(Item).where(
            Item.space_id == space.id,
            Item.deleted_at.is_(None),
            Item.id.in_(ids),
        )
    )
    items_by_id = {i.id: i for i in rows.scalars().all()}
    ordered = [items_by_id[i] for i in ids if i in items_by_id]
    if not ordered:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No matching items")

    att_rows = await db.execute(
        select(Attachment).where(
            Attachment.item_id.in_([i.id for i in ordered])
        )
    )
    atts_by_item: dict[uuid.UUID, list[Attachment]] = {}
    for att in att_rows.scalars().all():
        if _is_pdf(att):
            atts_by_item.setdefault(att.item_id, []).append(att)

    buf = io.BytesIO()
    used: set[str] = set()
    written = 0
    missing: list[str] = []
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for it in ordered:
            for att in atts_by_item.get(it.id, []):
                name = _safe_zip_name(att.filename, f"{att.id.hex[:8]}.pdf")
                if not name.lower().endswith(".pdf"):
                    name += ".pdf"
                arcname = _dedupe_name(name, used)
                # Fetch the same "current best" blob the single-file
                # download serves (latest outline > latest OCR >
                # original). Reading ``storage_key`` directly would miss
                # every attachment whose original was superseded by a
                # derivation — its original object may no longer exist.
                storage_key, _ = await _resolve_version(db, att, None)
                try:
                    body = await storage.read_object(storage_key)
                except Exception as exc:
                    # A missing blob shouldn't sink the whole download —
                    # but don't drop it silently either: record it so the
                    # ZIP and a response header tell the user what's gone.
                    used.discard(arcname)
                    missing.append(att.filename)
                    log.warning(
                        "pdf-zip: skipping attachment %s (%s) key=%s: %s",
                        att.id,
                        att.filename,
                        storage_key,
                        exc,
                    )
                    continue
                z.writestr(arcname, body)
                written += 1
        if missing:
            listing = "\n".join(sorted(missing))
            z.writestr(
                "_MISSING_FILES.txt",
                "These PDFs could not be fetched from storage and were "
                "left out of this archive:\n\n" + listing + "\n",
            )

    if written == 0:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No PDF attachments could be fetched for the selected items",
        )

    filename = f"{slug}-pdfs.zip"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if missing:
        # Surfaced to the SPA so it can warn instead of the loss being
        # invisible until the user counts the files.
        headers["X-Shelf-Skipped"] = str(len(missing))
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers=headers,
    )
