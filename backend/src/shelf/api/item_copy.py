"""Copying an item into another space.

A real copy: a new item row, new attachment rows, and new objects in the
bucket. The two diverge from the moment it lands — editing the copy does
not touch the original, and neither does deleting it.

**Consider inheritance first.** For a document many spaces need to read —
a standard, a company procedure — subscribing to the space that owns it
is better in every way that matters: one copy of the bytes, one place to
correct a mistake, and everyone sees the correction. Copying is for when
the target genuinely wants its own: a project-specific markup of a
drawing, a snapshot taken deliberately, a document moving between teams.

What comes across, and why:

    metadata      yes — it's the item
    attachments   yes, bytes and extracted text, so the copy is
                  searchable immediately rather than after a worker pass
    tags          yes, matched by name into the target space's own tag
                  table, since tags are space-scoped
    collections   no — folders are the target space's own structure, and
                  the source's tree means nothing there. The caller may
                  name one collection *in the target* to file the copy
                  under, which is a choice about where it lands rather
                  than a translation of where it came from.
    notes         no
    annotations   no

The last two are the deliberate ones. Notes and highlights belong to the
people who wrote them, under the visibility they chose (see
`auth/visibility.py`); republishing them into a space those people may
not even be in is not a copy, it's a disclosure.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    Collection,
    Item,
    ItemCollection,
    ItemTag,
    Space,
    StandardRevision,
    Tag,
    User,
)
from ..services import storage

router = APIRouter(tags=["items"])


class CopyItemRequest(BaseModel):
    target_slug: str
    # A collection in the *target* space to file the copy under. Optional
    # — omitted leaves it unfiled, which is where a copy landed before
    # this existed.
    target_collection_id: uuid.UUID | None = None
    # Off means metadata only — useful for seeding a record in a space
    # that will get its own file, and for a quick copy of something with
    # a 300 MB PDF hanging off it.
    include_attachments: bool = True


class CopyItemResponse(BaseModel):
    item_id: str
    space_id: str
    space_slug: str
    attachments_copied: int
    # Echoed back so a caller that filed the copy can confirm where it
    # went without a second request.
    collection_id: str | None
    # True when the source is a revision of a standard and the copy was
    # linked to the same one. Worth surfacing: it means the copy joins
    # that standard's revision history rather than starting a new one.
    linked_to_standard: bool


@router.post(
    "/api/items/{item_id}/copy",
    response_model=CopyItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def copy_item(
    item_id: uuid.UUID,
    payload: CopyItemRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> CopyItemResponse:
    """Copy an item the caller can read into a space they can write to.

    Viewer on the source is enough — copying reads it — and editor on the
    target, because that's where the write lands. An inherited item is
    copyable for the same reason: read access is all it takes.
    """
    source = await db.get(Item, item_id)
    if source is None or source.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    source_space = await db.get(Space, source.space_id)
    await require_space_role(
        db, source_space, user.id, SPACE_ROLE_VIEWER, label="Item not found"
    )

    target = (
        await db.execute(select(Space).where(Space.slug == payload.target_slug))
    ).scalar_one_or_none()
    await require_space_role(
        db, target, user.id, SPACE_ROLE_EDITOR, label="Space not found"
    )
    assert target is not None

    if target.id == source.space_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "That item is already in this space"
        )

    copy = Item(
        space_id=target.id,
        item_type=source.item_type,
        # dict(...) rather than the same object: SQLAlchemy would
        # otherwise hand both rows one JSONB dict, and a later edit to
        # either could be flushed onto both.
        data=dict(source.data) if isinstance(source.data, dict) else source.data,
        created_by=user.id,
    )
    db.add(copy)
    await db.flush()

    if payload.target_collection_id is not None:
        await _file_under(
            db,
            copy_id=copy.id,
            collection_id=payload.target_collection_id,
            target_space_id=target.id,
        )

    await _copy_tags(db, source_id=source.id, copy=copy, target_space_id=target.id)
    linked = await _copy_standard_link(db, source_id=source.id, copy_id=copy.id)

    copied = 0
    if payload.include_attachments:
        copied = await _copy_attachments(
            db, source_id=source.id, copy=copy, target_space_id=target.id, user=user
        )

    await db.commit()
    return CopyItemResponse(
        item_id=str(copy.id),
        space_id=str(target.id),
        space_slug=target.slug,
        attachments_copied=copied,
        collection_id=(
            str(payload.target_collection_id)
            if payload.target_collection_id is not None
            else None
        ),
        linked_to_standard=linked,
    )


async def _file_under(
    db: AsyncSession,
    *,
    copy_id: uuid.UUID,
    collection_id: uuid.UUID,
    target_space_id: uuid.UUID,
) -> None:
    """File the copy under one of the target space's collections.

    The collection has to belong to the target, not to the source and
    not to a space the target merely inherits from: an inherited folder
    is read-only here, and filing into it would be a write to somebody
    else's structure.
    """
    owner_space_id = (
        await db.execute(
            select(Collection.space_id).where(Collection.id == collection_id)
        )
    ).scalar_one_or_none()
    if owner_space_id != target_space_id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No such collection in the target space",
        )
    db.add(ItemCollection(item_id=copy_id, collection_id=collection_id))


async def _copy_tags(
    db: AsyncSession,
    *,
    source_id: uuid.UUID,
    copy: Item,
    target_space_id: uuid.UUID,
) -> None:
    """Reproduce the source's tags in the target space, by name.

    Tags are per-space rows, so "the same tag" across spaces means the
    same name. An existing tag in the target is reused — matching is
    case-insensitive because `tags.name` is CITEXT — and a missing one is
    created, colour included.
    """
    names = (
        await db.execute(
            select(Tag.name, Tag.color)
            .join(ItemTag, ItemTag.tag_id == Tag.id)
            .where(ItemTag.item_id == source_id)
        )
    ).all()
    if not names:
        return

    existing = {
        tag.name.casefold(): tag
        for tag in (
            await db.execute(
                select(Tag).where(Tag.space_id == target_space_id)
            )
        ).scalars().all()
    }

    for name, color in names:
        tag = existing.get(name.casefold())
        if tag is None:
            tag = Tag(space_id=target_space_id, name=name, color=color)
            db.add(tag)
            await db.flush()
            existing[name.casefold()] = tag
        db.add(ItemTag(item_id=copy.id, tag_id=tag.id))


async def _copy_standard_link(
    db: AsyncSession, *, source_id: uuid.UUID, copy_id: uuid.UUID
) -> bool:
    """Point the copy at the same standard family, if the source had one.

    Families are instance-wide, so this is the row that makes the copy
    show up in the revision dropdown alongside its original rather than
    looking like an unrelated PDF with a similar name.
    """
    revision = await db.get(StandardRevision, source_id)
    if revision is None:
        return False
    db.add(
        StandardRevision(
            item_id=copy_id,
            family_id=revision.family_id,
            label=revision.label,
            issued_on=revision.issued_on,
            superseded=revision.superseded,
        )
    )
    return True


async def _copy_attachments(
    db: AsyncSession,
    *,
    source_id: uuid.UUID,
    copy: Item,
    target_space_id: uuid.UUID,
    user: User,
) -> int:
    """Duplicate each attachment row and its object in the bucket.

    The bytes are copied server-side (S3 CopyObject and its equivalents)
    rather than pulled through this process, so a large PDF costs a
    request rather than a download and an upload.

    A failure aborts the whole copy with a 502: half an item — metadata
    present, the PDF that is the actual content missing — is worse than
    none, and the caller can retry. Objects already written in this pass
    are left behind as orphans; `cleanup-orphans` doesn't reach them
    because no row points at them, which is a known and small cost
    against the alternative of deleting bytes during error handling.

    Rows still pending an upload are copied as rows only. There is
    nothing in the bucket to copy yet, and dropping them would lose the
    filename someone is mid-way through uploading.
    """
    attachments = (
        await db.execute(
            select(Attachment).where(Attachment.item_id == source_id)
        )
    ).scalars().all()

    copied = 0
    for att in attachments:
        new_att = Attachment(
            id=uuid.uuid4(),
            item_id=copy.id,
            storage_key="",  # replaced below; the column is NOT NULL
            filename=att.filename,
            content_type=att.content_type,
            size_bytes=att.size_bytes,
            # Same bytes, so necessarily the same hash. Recomputing would
            # mean downloading what we're about to server-side copy, and
            # leaving it null would make the copy look like a different
            # file to anything matching on content identity.
            sha256=att.sha256,
            uploaded_at=att.uploaded_at,
            created_by=user.id,
            # Carried over so the copy is searchable straight away rather
            # than sitting textless until a worker gets to it.
            text_content=att.text_content,
            text_chars=att.text_chars,
            extracted_at=att.extracted_at,
            extraction_status=att.extraction_status,
        )
        new_att.storage_key = storage.attachment_storage_key(
            target_space_id, copy.id, new_att.id
        )
        db.add(new_att)

        if att.uploaded_at is not None:
            try:
                await storage.copy_object(att.storage_key, new_att.storage_key)
            except Exception as e:
                await db.rollback()
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY,
                    f"Could not copy {att.filename!r} in the object store: {e}",
                ) from e
            copied += 1

        # Page text, so ?scope=fulltext and the reader's per-page snippets
        # work on the copy without re-extracting.
        for page in (
            await db.execute(
                select(AttachmentPage).where(
                    AttachmentPage.attachment_id == att.id
                )
            )
        ).scalars().all():
            db.add(
                AttachmentPage(
                    attachment_id=new_att.id,
                    page_number=page.page_number,
                    text=page.text,
                    width_pts=page.width_pts,
                    height_pts=page.height_pts,
                )
            )

    return copied
