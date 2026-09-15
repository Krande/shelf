"""File attachment endpoints.

Upload flow (three steps, no proxy through the API):
  1. Client POSTs filename + content_type + size_bytes; backend creates
     the row, computes a storage key, and returns it together with a
     presigned PUT URL. `uploaded_at` is null at this point.
  2. Client uploads the file body straight to object storage with that
     URL.
  3. Client POSTs `/api/attachments/{id}/complete` so the backend
     stamps `uploaded_at`. Without this step the row stays pending
     forever; a periodic HEAD-check pass can prune true orphans.

Inline / proxy uploads (only used by /api/v1/upload) set
`uploaded_at` in the same transaction as the put_async call.
"""

import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import get_current_user
from ..auth.spaces import SPACE_ROLE_EDITOR, SPACE_ROLE_VIEWER, require_space_role
from ..db import get_session
from ..models import (
    Attachment,
    AttachmentDerivation,
    AttachmentPage,
    AttachmentProcessing,
    Item,
    Space,
    User,
)
from ..services import extraction, storage
from ..services.storage import attachment_storage_key

log = logging.getLogger(__name__)

router = APIRouter(tags=["attachments"])


class AttachmentRegister(BaseModel):
    filename: str
    content_type: str
    size_bytes: int | None = None


class AttachmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int | None
    uploaded_at: datetime | None = None


class AttachmentRegisterResponse(BaseModel):
    attachment: AttachmentResponse
    upload_url: str


class AttachmentDownloadResponse(BaseModel):
    url: str


class AttachmentPageDimsRow(BaseModel):
    page: int
    width: float | None
    height: float | None


class AttachmentPageDimsResponse(BaseModel):
    """Per-page dimensions in PDF user-space (1 pt = 1/72 inch) at
    scale=1. The reader uses this to seed its virtualizer height map
    in one HTTP call instead of opening every page client-side at
    load time. Empty list when the extract worker hasn't run yet —
    the reader falls back to a sample-first-page baseline."""

    pages: list[AttachmentPageDimsRow]


class AttachmentProcessingResponse(BaseModel):
    """Quality assessment + downstream-worker state for one
    attachment. Phase A (current) only fills the metric / flag
    columns; the *_status fields stay 'untouched' until the OCR
    and outline workers ship."""

    model_config = ConfigDict(from_attributes=True)

    attachment_id: uuid.UUID
    assessed_at: datetime | None = None
    page_count: int | None = None
    text_chars: int | None = None
    replacement_char_ratio: float | None = None
    alpha_ratio: float | None = None
    chars_per_page: float | None = None
    toc_entry_count: int | None = None
    needs_ocr: bool = False
    needs_outline: bool = False
    ocr_status: str = "untouched"
    ocr_engine: str | None = None
    ocr_completed_at: datetime | None = None
    outline_status: str = "untouched"
    outline_engine: str | None = None
    outline_completed_at: datetime | None = None
    original_preserved_at: datetime | None = None
    progress_done: int | None = None
    progress_total: int | None = None


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


async def _resolve_attachment(
    db: AsyncSession,
    user: User,
    attachment_id: uuid.UUID,
    minimum: str = SPACE_ROLE_VIEWER,
) -> Attachment:
    att = await db.get(Attachment, attachment_id)
    if att is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    # Re-using the item resolver keeps the auth check in one place.
    await _resolve_item(db, user, att.item_id, minimum)
    return att


@router.get(
    "/api/items/{item_id}/attachments",
    response_model=list[AttachmentResponse],
)
async def list_attachments(
    item_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[Attachment]:
    await _resolve_item(db, user, item_id)
    result = await db.execute(
        select(Attachment)
        .where(Attachment.item_id == item_id)
        .order_by(Attachment.created_at)
    )
    return list(result.scalars().all())


@router.post(
    "/api/items/{item_id}/attachments",
    response_model=AttachmentRegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_attachment(
    item_id: uuid.UUID,
    payload: AttachmentRegister,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> AttachmentRegisterResponse:
    item = await _resolve_item(db, user, item_id, SPACE_ROLE_EDITOR)
    att_id = uuid.uuid4()
    storage_key = attachment_storage_key(item.space_id, item.id, att_id)
    att = Attachment(
        id=att_id,
        item_id=item.id,
        storage_key=storage_key,
        filename=payload.filename,
        content_type=payload.content_type,
        size_bytes=payload.size_bytes,
        created_by=user.id,
    )
    db.add(att)
    await db.commit()
    await db.refresh(att)
    upload_url = await storage.presign_upload(storage_key)
    return AttachmentRegisterResponse(
        attachment=AttachmentResponse.model_validate(att),
        upload_url=upload_url,
    )


class AttachmentDerivationResponse(BaseModel):
    """One derived PDF the reader can switch to. ``parent_storage_key``
    is opaque to the SPA — used only as a stable identifier when the UI
    draws the lineage chain. The derivation's actual content is fetched
    via /download?version=<id>."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    parent_storage_key: str
    engine: str
    created_at: datetime


class AttachmentDerivationListResponse(BaseModel):
    """Returns every derivation plus a synthetic ``original`` row for
    the attachment's untouched bytes, so the UI builds the dropdown
    from a single response. ``current_version`` is the id of the row
    the reader's default-best resolution would pick (or "original" if
    no derivations exist)."""

    derivations: list[AttachmentDerivationResponse]
    current_version: str


async def _resolve_version(
    db: AsyncSession,
    attachment: Attachment,
    version: str | None,
) -> tuple[str, str]:
    """Pick the storage key for the requested ``version``.

    ``version`` is one of:
      - None → current best (latest outline > latest ocr > original)
      - "original" → ``attachments.storage_key``
      - a derivation UUID → that row's storage_key (404 on miss /
        wrong attachment)

    Returns ``(storage_key, version_label)`` where ``version_label`` is
    the same string format the SPA uses to identify the current view.
    """
    if version == "original":
        return attachment.storage_key, "original"
    if version is not None:
        try:
            vid = uuid.UUID(version)
        except ValueError as e:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "version must be 'original' or a derivation id",
            ) from e
        row = await db.get(AttachmentDerivation, vid)
        if row is None or row.attachment_id != attachment.id:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Derivation not found"
            )
        return row.storage_key, str(row.id)

    # Default: latest outline > latest ocr > original.
    for kind in ("outline", "ocr"):
        latest = (
            await db.execute(
                select(AttachmentDerivation)
                .where(
                    AttachmentDerivation.attachment_id == attachment.id,
                    AttachmentDerivation.kind == kind,
                )
                .order_by(AttachmentDerivation.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest is not None:
            return latest.storage_key, str(latest.id)
    return attachment.storage_key, "original"


@router.get(
    "/api/attachments/{attachment_id}/download",
    response_model=AttachmentDownloadResponse,
)
async def download_attachment(
    attachment_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    version: Annotated[
        str | None,
        Query(
            description=(
                "Which version to fetch. Omit for the current best "
                "(latest outline > latest OCR > original). Pass "
                "'original' for the untouched upload, or a derivation "
                "UUID to fetch a specific run."
            ),
        ),
    ] = None,
) -> AttachmentDownloadResponse:
    att = await _resolve_attachment(db, user, attachment_id)
    storage_key, _ = await _resolve_version(db, att, version)
    url = await storage.presign_download(storage_key)
    return AttachmentDownloadResponse(url=url)


@router.get("/api/attachments/{attachment_id}/file")
async def stream_attachment(
    attachment_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    version: Annotated[str | None, Query()] = None,
) -> StreamingResponse:
    """Proxy-stream the attachment with a Content-Disposition header so
    the browser saves it under the user-visible filename rather than the
    opaque storage key. Used by the explicit "Download" action; the
    reader still uses /download (presigned URL) to fetch bytes
    direct-from-storage.

    Signs for the server-side endpoint, not the browser-facing one: this
    process performs the GET itself, so the URL never leaves the server."""
    att = await _resolve_attachment(db, user, attachment_id)
    storage_key, _ = await _resolve_version(db, att, version)
    presigned = await storage.presign_download_internal(storage_key)

    client = httpx.AsyncClient(follow_redirects=True, timeout=60.0)
    req = client.build_request("GET", presigned)
    upstream = await client.send(req, stream=True)
    if upstream.status_code != 200:
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Failed to fetch from storage"
        )

    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    # RFC 6266: filename* with UTF-8 percent-encoding covers non-ASCII
    # filenames; filename= gives a safe ASCII fallback for old clients.
    ascii_name = att.filename.encode("ascii", "replace").decode("ascii")
    disposition = (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(att.filename)}"
    )
    headers = {"Content-Disposition": disposition}
    content_length = upstream.headers.get("content-length")
    if content_length:
        headers["Content-Length"] = content_length
    return StreamingResponse(
        body(),
        media_type=att.content_type or "application/octet-stream",
        headers=headers,
    )


@router.get(
    "/api/attachments/{attachment_id}/derivations",
    response_model=AttachmentDerivationListResponse,
)
async def list_derivations(
    attachment_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> AttachmentDerivationListResponse:
    """List every derivation for one attachment so the reader can
    show a version dropdown. Ordered newest-first across all kinds —
    the UI groups by kind for display."""
    att = await _resolve_attachment(db, user, attachment_id)
    rows = (
        await db.execute(
            select(AttachmentDerivation)
            .where(AttachmentDerivation.attachment_id == att.id)
            .order_by(AttachmentDerivation.created_at.desc())
        )
    ).scalars().all()
    _, current = await _resolve_version(db, att, None)
    return AttachmentDerivationListResponse(
        derivations=[
            AttachmentDerivationResponse.model_validate(r) for r in rows
        ],
        current_version=current,
    )


@router.get(
    "/api/attachments/{attachment_id}/page-dims",
    response_model=AttachmentPageDimsResponse,
)
async def get_page_dims(
    attachment_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> AttachmentPageDimsResponse:
    """Per-page width/height for the reader's virtualizer."""
    att = await _resolve_attachment(db, user, attachment_id)
    rows = (
        await db.execute(
            select(
                AttachmentPage.page_number,
                AttachmentPage.width_pts,
                AttachmentPage.height_pts,
            )
            .where(AttachmentPage.attachment_id == att.id)
            .order_by(AttachmentPage.page_number.asc())
        )
    ).all()
    return AttachmentPageDimsResponse(
        pages=[
            AttachmentPageDimsRow(page=r[0], width=r[1], height=r[2]) for r in rows
        ]
    )


@router.get(
    "/api/attachments/{attachment_id}/processing",
    response_model=AttachmentProcessingResponse,
)
async def get_processing(
    attachment_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> AttachmentProcessingResponse:
    """Phase A diagnostic: quality metrics + downstream-worker flags
    for one attachment. Returns a 'never assessed' shape (all fields
    null/false/'untouched') if the extract worker hasn't run yet
    against this row — keeps the endpoint friendly to clients
    polling during ingestion."""
    att = await _resolve_attachment(db, user, attachment_id)
    proc = await db.get(AttachmentProcessing, att.id)
    if proc is None:
        return AttachmentProcessingResponse(attachment_id=att.id)
    return AttachmentProcessingResponse.model_validate(proc)


@router.post(
    "/api/attachments/{attachment_id}/complete",
    response_model=AttachmentResponse,
)
async def complete_attachment(
    attachment_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Attachment:
    """SPA-side parity with /api/v1/uploads/{id}/complete: the browser
    PUTs the body to the presigned URL, then calls this so the row
    stops looking pending."""
    att = await _resolve_attachment(db, user, attachment_id, SPACE_ROLE_EDITOR)
    if att.uploaded_at is None:
        att.uploaded_at = datetime.now(UTC)
        await extraction.mark_and_enqueue(att)
        # Eagerly snapshot the just-uploaded blob to its `.original`
        # sibling so any later worker rewrite (OCR, future passes) is
        # reversible. PDF-only — non-PDFs aren't mutated by any worker
        # so the snapshot would be storage cost without payoff. Best-
        # effort: a snapshot failure shouldn't block the upload from
        # being marked complete.
        if att.content_type == "application/pdf":
            try:
                made = await storage.ensure_original(att.storage_key)
            except Exception:
                made = False
                log.exception(
                    "snapshot of original failed for %s; row stays without "
                    "original_preserved_at and Restore stays unavailable",
                    att.id,
                )
            if made:
                now = datetime.now(UTC)
                await db.execute(
                    pg_insert(AttachmentProcessing)
                    .values(
                        attachment_id=att.id,
                        original_preserved_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                    .on_conflict_do_update(
                        index_elements=[AttachmentProcessing.attachment_id],
                        set_={
                            "original_preserved_at": now,
                            "updated_at": now,
                        },
                    )
                )
        await db.commit()
        await db.refresh(att)
    return att


@router.delete(
    "/api/attachments/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_attachment(
    attachment_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    att = await _resolve_attachment(db, user, attachment_id, SPACE_ROLE_EDITOR)
    # Best-effort: if an object isn't there, drop the row anyway so
    # the user can recover from a half-done upload. The processing
    # row + derivation rows cascade via FK; we just have to clean up
    # the underlying S3 blobs ourselves.
    derivation_keys = (
        await db.execute(
            select(AttachmentDerivation.storage_key).where(
                AttachmentDerivation.attachment_id == att.id
            )
        )
    ).scalars().all()
    keys_to_delete = [
        att.storage_key,
        storage.original_key(att.storage_key),
        *derivation_keys,
    ]
    for key in keys_to_delete:
        try:
            await storage.delete_object(key)
        except Exception:
            # The row removal is the user-visible action; logging hooks
            # land when we wire structlog in the broader observability
            # pass.
            pass
    await db.delete(att)
    await db.commit()
