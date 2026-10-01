"""Importing a Shelf archive -- the ZIP "Download PDFs" produces.

The import is split between server and browser so the archive's bytes
never pass through this process. The browser opens the ZIP, reads its
``index.json`` (see ``archive_format``) and posts it here; this builds
the collection tree, the items and their tags and memberships in one
transaction and answers with the PDFs the browser should upload. Those
go straight to object storage through the ordinary attachment upload,
so a multi-GB archive costs this server only metadata.

Re-importing is safe, and is how an interrupted import is finished:

    collections   matched by name under the same parent (the target,
                  for the archive's top level), so the tree merges into
                  one already there instead of growing a second copy
    items         an item is already here when its id, or the id it was
                  originally imported from, matches an item in this
                  space. It is not duplicated or edited -- only filed
                  into the archive's collections. New items record their
                  origin, which is what lets the next import spot them.
    files         a PDF is already there when the item has an uploaded
                  attachment with the same SHA-256 or the same filename;
                  only the others are asked for

    notes,        when the archive includes them, go onto the items this
    revisions     import created -- never onto ones already here, which is
                  what keeps a re-import from doubling them. Notes are
                  authored by the importer, visibility kept. Revisions
                  join the instance's family for (body, designation).
    annotations   ride along with each upload and are posted by the
                  browser to the new attachment, so they arrive exactly
                  when their PDF does
"""

import re
import uuid
from itertools import batched
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..archive_format import (
    ArchiveCollection,
    ArchiveFormatError,
    ArchiveIndex,
    parse_index,
)
from ..auth.deps import get_current_user
from ..auth.spaces import SPACE_ROLE_EDITOR, require_space_role
from ..db import get_session
from ..models import (
    VISIBILITIES,
    VISIBILITY_PRIVATE,
    AnnotationKind,
    Attachment,
    Collection,
    Item,
    ItemCollection,
    ItemTag,
    Note,
    Space,
    StandardFamily,
    StandardRevision,
    Tag,
    User,
)
from ..services.audit import AuditAction, record

router = APIRouter(tags=["archive"])

# Ids per IN (...) list; asyncpg caps a statement at 32767 parameters.
_IN_BATCH = 5000


class ArchiveImportRequest(BaseModel):
    # The archive's index.json, as decoded JSON. Validated here, not by
    # the request model, so a version this Shelf can't read gets its own
    # message rather than a wall of field errors.
    index: Any
    # A collection in this space to import under; null imports at the
    # top level.
    collection_id: uuid.UUID | None = None


class UploadAnnotation(BaseModel):
    """An archived annotation, shaped for
    ``POST /api/attachments/{id}/annotations/bulk``."""

    kind: str
    page_number: int
    rects: list[list[float]]
    color: str
    text: str | None
    visibility: str


class ArchiveUpload(BaseModel):
    """A PDF in the archive the browser should upload."""

    path: str
    item_id: uuid.UUID
    filename: str
    content_type: str
    size: int | None
    # To post to the new attachment once it's uploaded. They ride with
    # the upload because an annotation needs the attachment's id, which
    # only exists once the browser has registered it -- and so they're
    # restored exactly when the PDF is, never twice.
    annotations: list[UploadAnnotation] = []


class ArchiveImportResponse(BaseModel):
    # The archive's format version ("0.0" for a pre-versioning one).
    version: str
    collections_created: int
    collections_existing: int
    items_created: int
    items_existing: int
    # PDFs skipped because the item already has them.
    files_existing: int
    notes_created: int
    revisions_linked: int
    uploads: list[ArchiveUpload]


@router.post(
    "/api/spaces/{slug}/archive-import",
    response_model=ArchiveImportResponse,
)
async def import_archive(
    slug: str,
    payload: ArchiveImportRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ArchiveImportResponse:
    space = (
        await db.execute(select(Space).where(Space.slug == slug))
    ).scalar_one_or_none()
    await require_space_role(
        db, space, user.id, SPACE_ROLE_EDITOR, label="Space not found"
    )
    assert space is not None

    if payload.collection_id is not None:
        owner = (
            await db.execute(
                select(Collection.space_id).where(
                    Collection.id == payload.collection_id
                )
            )
        ).scalar_one_or_none()
        if owner != space.id:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "No such collection in this space"
            )

    try:
        index = parse_index(payload.index)
    except ArchiveFormatError as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)
        ) from e

    coll_map, colls_created = await _import_collections(
        db, space.id, index.collections, payload.collection_id
    )
    targets, created = await _import_items(db, space.id, index, user)
    await _file_items(db, index, targets, coll_map, payload.collection_id)
    await _tag_items(db, space.id, index, targets, created)
    notes_created = _add_notes(db, index, targets, created, user)
    revisions_linked = await _link_revisions(db, index, targets, created)
    uploads, files_existing = await _uploads(db, index, targets, created)

    details = {
        "version": index.version,
        "collections_created": colls_created,
        "items_created": len(created),
        "items_existing": len(targets) - len(created),
        "files_to_upload": len(uploads),
        "files_existing": files_existing,
        "notes_created": notes_created,
        "revisions_linked": revisions_linked,
    }
    target_name = None
    if payload.collection_id is not None:
        target_name = await db.scalar(
            select(Collection.name).where(Collection.id == payload.collection_id)
        )
    record(
        db,
        user,
        AuditAction.archive_import,
        space_id=space.id,
        target_type="collection" if payload.collection_id else "space",
        target_id=payload.collection_id or space.id,
        label=target_name or space.name,
        details=details,
    )
    await db.commit()
    return ArchiveImportResponse(
        version=index.version,
        collections_created=colls_created,
        collections_existing=len(index.collections) - colls_created,
        items_created=len(created),
        items_existing=len(targets) - len(created),
        files_existing=files_existing,
        notes_created=notes_created,
        revisions_linked=revisions_linked,
        uploads=uploads,
    )


async def _import_collections(
    db: AsyncSession,
    space_id: uuid.UUID,
    archive: list[ArchiveCollection],
    target: uuid.UUID | None,
) -> tuple[dict[uuid.UUID, uuid.UUID], int]:
    """Archive collection id -> this space's collection, creating what
    isn't there. Matching is by exact name under the same parent, so
    importing twice merges rather than duplicates. Two sibling
    collections of the same name in the archive both map to one."""
    existing = (
        await db.execute(
            select(Collection)
            .where(Collection.space_id == space_id)
            .order_by(Collection.position)
        )
    ).scalars().all()
    by_parent_name: dict[tuple[uuid.UUID | None, str], uuid.UUID] = {}
    sibling_count: dict[uuid.UUID | None, int] = {}
    for c in existing:
        by_parent_name.setdefault((c.parent_id, c.name), c.id)
        sibling_count[c.parent_id] = sibling_count.get(c.parent_id, 0) + 1

    children: dict[uuid.UUID | None, list[ArchiveCollection]] = {}
    for ac in archive:
        children.setdefault(ac.parent_id, []).append(ac)

    mapping: dict[uuid.UUID, uuid.UUID] = {}
    created = 0
    # Parents first, whatever order the archive lists them in.
    queue = [(ac, target) for ac in children.get(None, [])]
    while queue:
        ac, parent = queue.pop(0)
        name = ac.name.strip() or "Untitled"
        found = by_parent_name.get((parent, name))
        if found is None:
            position = sibling_count.get(parent, 0)
            coll = Collection(
                space_id=space_id,
                parent_id=parent,
                name=name,
                description=(ac.description or "").strip() or None,
                position=position,
            )
            db.add(coll)
            await db.flush()
            sibling_count[parent] = position + 1
            by_parent_name[(parent, name)] = coll.id
            found = coll.id
            created += 1
        mapping[ac.id] = found
        queue.extend((child, found) for child in children.get(ac.id, []))
    return mapping, created


async def _import_items(
    db: AsyncSession, space_id: uuid.UUID, index: ArchiveIndex, user: User
) -> tuple[dict[uuid.UUID, Item], set[uuid.UUID]]:
    """Archive item id -> the item it lands as here, and which of those
    were created by this import (by archive id)."""
    keys = {it.id for it in index.items} | {
        it.origin_id for it in index.items if it.origin_id is not None
    }
    here: dict[uuid.UUID, Item] = {}
    # Half a batch: each key is bound twice.
    for batch in batched(keys, _IN_BATCH // 2):
        rows = (
            await db.execute(
                select(Item).where(
                    Item.space_id == space_id,
                    Item.deleted_at.is_(None),
                    or_(Item.id.in_(batch), Item.origin_id.in_(batch)),
                )
            )
        ).scalars().all()
        for row in rows:
            here.setdefault(row.id, row)
            if row.origin_id is not None:
                here.setdefault(row.origin_id, row)

    targets: dict[uuid.UUID, Item] = {}
    created: set[uuid.UUID] = set()
    for it in index.items:
        found = here.get(it.id) or (
            here.get(it.origin_id) if it.origin_id is not None else None
        )
        if found is None:
            found = Item(
                space_id=space_id,
                item_type=it.item_type,
                data=dict(it.data),
                created_by=user.id,
                origin_id=it.origin_id or it.id,
            )
            db.add(found)
            created.add(it.id)
        targets[it.id] = found
    await db.flush()
    return targets, created


async def _file_items(
    db: AsyncSession,
    index: ArchiveIndex,
    targets: dict[uuid.UUID, Item],
    coll_map: dict[uuid.UUID, uuid.UUID],
    target: uuid.UUID | None,
) -> None:
    """Add each item to the archive's collections, keeping whatever it
    is already filed in. An item the archive files nowhere -- every one
    of a selection, a space's unfiled documents -- goes under the import
    target, if there is one."""
    wanted: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for it in index.items:
        item_id = targets[it.id].id
        colls = {coll_map[c] for c in it.collection_ids}
        if not colls and target is not None:
            colls = {target}
        wanted.update((item_id, c) for c in colls)
    if not wanted:
        return
    have: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for batch in batched({i for i, _ in wanted}, _IN_BATCH):
        have.update(
            (
                await db.execute(
                    select(
                        ItemCollection.item_id, ItemCollection.collection_id
                    ).where(ItemCollection.item_id.in_(batch))
                )
            ).tuples()
        )
    for item_id, cid in sorted(wanted - have):
        db.add(ItemCollection(item_id=item_id, collection_id=cid))


async def _tag_items(
    db: AsyncSession,
    space_id: uuid.UUID,
    index: ArchiveIndex,
    targets: dict[uuid.UUID, Item],
    created: set[uuid.UUID],
) -> None:
    """Tags for the items this import created, matched by name into this
    space's tags (case-insensitively, as tags.name is CITEXT) and created
    when missing. An item that was already here keeps its own."""
    colors = {t.name.casefold(): t.color for t in index.tags}
    tags = {
        t.name.casefold(): t
        for t in (
            await db.execute(select(Tag).where(Tag.space_id == space_id))
        ).scalars().all()
    }
    for it in index.items:
        if it.id not in created:
            continue
        for name in dict.fromkeys(n.strip() for n in it.tags if n.strip()):
            tag = tags.get(name.casefold())
            if tag is None:
                tag = Tag(
                    space_id=space_id,
                    name=name,
                    color=colors.get(name.casefold()),
                )
                db.add(tag)
                await db.flush()
                tags[name.casefold()] = tag
            db.add(ItemTag(item_id=targets[it.id].id, tag_id=tag.id))


async def _uploads(
    db: AsyncSession,
    index: ArchiveIndex,
    targets: dict[uuid.UUID, Item],
    created: set[uuid.UUID],
) -> tuple[list[ArchiveUpload], int]:
    """The archive's PDFs that aren't here yet."""
    existing_ids = [targets[it.id].id for it in index.items if it.id not in created]
    have_sha: dict[uuid.UUID, set[str]] = {}
    have_name: dict[uuid.UUID, set[str]] = {}
    # Uploaded ones only: a row still pending is an upload that never
    # finished, and is exactly what a re-run should redo.
    for batch in batched(existing_ids, _IN_BATCH):
        for item_id, sha, filename in (
            await db.execute(
                select(Attachment.item_id, Attachment.sha256, Attachment.filename)
                .where(
                    Attachment.item_id.in_(batch),
                    Attachment.uploaded_at.is_not(None),
                )
            )
        ).tuples():
            if sha:
                have_sha.setdefault(item_id, set()).add(sha)
            have_name.setdefault(item_id, set()).add(filename)

    uploads: list[ArchiveUpload] = []
    skipped = 0
    for it in index.items:
        item_id = targets[it.id].id
        for f in it.files:
            if (f.sha256 and f.sha256 in have_sha.get(item_id, ())) or (
                f.filename in have_name.get(item_id, ())
            ):
                skipped += 1
                continue
            uploads.append(
                ArchiveUpload(
                    path=f.path,
                    item_id=item_id,
                    filename=f.filename,
                    content_type=f.content_type,
                    size=f.size,
                    annotations=[
                        UploadAnnotation(
                            kind=a.kind,
                            page_number=a.page_number,
                            rects=a.rects,
                            color=a.color,
                            text=a.text,
                            visibility=_visibility(a.visibility),
                        )
                        for a in f.annotations
                        # What this Shelf can't represent is dropped
                        # rather than failing the PDF's whole batch.
                        if a.kind in _ANNOTATION_KINDS
                        and a.rects
                        and all(len(r) == 4 for r in a.rects)
                    ],
                )
            )
    return uploads, skipped


_ANNOTATION_KINDS = {k.value for k in AnnotationKind}


def _visibility(value: str) -> str:
    """An archived visibility this Shelf knows, or the most private
    reading of one it doesn't: a note must never become more widely
    readable by being moved."""
    return value if value in VISIBILITIES else VISIBILITY_PRIVATE


def _add_notes(
    db: AsyncSession,
    index: ArchiveIndex,
    targets: dict[uuid.UUID, Item],
    created: set[uuid.UUID],
    user: User,
) -> int:
    """The archive's notes, on the items this import created, authored by
    the person importing -- it is their archive of their notes. An item
    that was already here keeps its own, so re-importing never doubles
    them."""
    count = 0
    for it in index.items:
        if it.id not in created:
            continue
        for n in it.notes:
            note = Note(
                item_id=targets[it.id].id,
                content_html=n.content_html,
                content_text=n.content_text or _text_of(n.content_html),
                author_id=user.id,
                visibility=_visibility(n.visibility),
            )
            if n.created_at is not None:
                note.created_at = n.created_at
            if n.updated_at is not None:
                note.updated_at = n.updated_at
            db.add(note)
            count += 1
    return count


def _text_of(html: str) -> str:
    """Plain text for search, when an archive didn't carry it."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


async def _link_revisions(
    db: AsyncSession,
    index: ArchiveIndex,
    targets: dict[uuid.UUID, Item],
    created: set[uuid.UUID],
) -> int:
    """Make each created item an edition of its standard again.

    Families are instance-wide and keyed by (body, designation), compared
    case-insensitively, so the import joins the family this instance
    already has for that standard, or starts it.
    """
    wanted = [
        (targets[it.id].id, it.revision)
        for it in index.items
        if it.id in created and it.revision is not None
    ]
    if not wanted:
        return 0
    families: dict[tuple[str, str], StandardFamily] = {}
    for item_id, rev in wanted:
        key = (rev.body.casefold(), rev.designation.casefold())
        fam = families.get(key)
        if fam is None:
            fam = (
                await db.execute(
                    select(StandardFamily).where(
                        StandardFamily.body == rev.body,
                        StandardFamily.designation == rev.designation,
                    )
                )
            ).scalar_one_or_none()
            if fam is None:
                fam = StandardFamily(
                    body=rev.body,
                    designation=rev.designation,
                    title=rev.family_title,
                )
                db.add(fam)
                await db.flush()
            families[key] = fam
        db.add(
            StandardRevision(
                item_id=item_id,
                family_id=fam.id,
                label=rev.label,
                issued_on=rev.issued_on,
                superseded=rev.superseded,
            )
        )
    return len(wanted)
