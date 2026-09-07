"""Token-auth API for worker pods running outside the cluster.

The CPU worker (in-cluster) talks to Postgres directly because it
shares the cluster's network and trust boundary. Workers running on
external hosts (the GPU box that holds the NVIDIA card, future
contributor compute) shouldn't have direct DB credentials — too
much trust to grant a host that lives outside ops's threat model.

This module exposes the minimum DB surface area those workers
actually need:

* ``GET  /api/v1/worker/attachments/{id}``  — the metadata workers
  consult before fetching the blob (storage_key, content_type,
  uploaded_at).
* ``GET  /api/v1/worker/processing/{id}``   — the current state of
  the processing row, used by the cancel-poll loop and for the
  pre-pickup short-circuit.
* ``POST /api/v1/worker/processing/{id}``   — partial UPSERT of the
  fields workers write (status, engine label, completed_at,
  progress_*, original_preserved_at). Payload is a strict subset of
  the model; unknown keys are rejected so a malicious / stale
  worker can't smuggle writes into other columns.

Auth: bearer token with the ``worker`` scope. The public token-mint
endpoint refuses to issue these; operators provision them via
``python -m shelf.admin.mint_worker_token``.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.tokens import require_worker_token
from ..db import get_session
from ..models import (
    ApiToken,
    Attachment,
    AttachmentDerivation,
    AttachmentProcessing,
)

router = APIRouter(tags=["worker"], prefix="/api/v1/worker")


class WorkerAttachmentDTO(BaseModel):
    """Slim view of an Attachment for the worker. Excludes the
    extracted text body (workers re-fetch via storage anyway and
    text_content can be megabytes — no point shipping it)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_id: uuid.UUID
    storage_key: str
    filename: str
    content_type: str
    size_bytes: int | None
    uploaded_at: datetime | None


class WorkerProcessingDTO(BaseModel):
    """Mirrors the ``AttachmentProcessing`` columns workers read.
    Endpoint returns 404 when no row exists so the caller can
    distinguish "fresh attachment" from "row in default state."""

    model_config = ConfigDict(from_attributes=True)

    attachment_id: uuid.UUID
    page_count: int | None
    text_chars: int | None
    needs_ocr: bool
    needs_outline: bool
    ocr_status: str
    ocr_engine: str | None
    ocr_completed_at: datetime | None
    outline_status: str
    outline_engine: str | None
    outline_completed_at: datetime | None
    outline_json: Any | None
    progress_done: int | None
    progress_total: int | None
    original_preserved_at: datetime | None


# Whitelist of column names a worker is allowed to UPSERT. Anything
# outside this set is a 400 — unknown columns can't be written, and
# the columns workers shouldn't touch (page_count, text_chars,
# assessed_at, anything Phase A's extract worker owns) are simply
# absent.
_WRITABLE_FIELDS = frozenset(
    {
        "ocr_status",
        "ocr_engine",
        "ocr_completed_at",
        "outline_status",
        "outline_engine",
        "outline_completed_at",
        "outline_json",
        "progress_done",
        "progress_total",
        "original_preserved_at",
    }
)


class WorkerProcessingUpsert(BaseModel):
    """Partial UPSERT payload — every field optional, only keys
    present in the JSON are written. Use the model's strict mode so
    unknown fields raise rather than silently no-op."""

    model_config = ConfigDict(extra="forbid")

    ocr_status: str | None = None
    ocr_engine: str | None = None
    ocr_completed_at: datetime | None = None
    outline_status: str | None = None
    outline_engine: str | None = None
    outline_completed_at: datetime | None = None
    outline_json: Any | None = None
    progress_done: int | None = None
    progress_total: int | None = None
    original_preserved_at: datetime | None = None
    # Sentinel: the worker explicitly *clears* a field by sending its
    # name in this list rather than `null` (which Pydantic treats the
    # same as "field omitted"). E.g. ["progress_done", "progress_total"]
    # to wipe progress on a terminal state transition.
    clear: list[str] = []


@router.get(
    "/attachments/{attachment_id}",
    response_model=WorkerAttachmentDTO,
)
async def get_attachment(
    attachment_id: uuid.UUID,
    _token: Annotated[ApiToken, Depends(require_worker_token)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Attachment:
    att = await db.get(Attachment, attachment_id)
    if att is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    return att


@router.get(
    "/processing/{attachment_id}",
    response_model=WorkerProcessingDTO,
)
async def get_processing(
    attachment_id: uuid.UUID,
    _token: Annotated[ApiToken, Depends(require_worker_token)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> AttachmentProcessing:
    """Read the processing row. Returns 404 (not the default-shape
    response the SPA endpoint uses) so the caller can short-circuit
    its cancel-poll without keeping a stale row alive."""
    proc = await db.get(AttachmentProcessing, attachment_id)
    if proc is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Processing row not found"
        )
    return proc


@router.post(
    "/processing/{attachment_id}",
    response_model=WorkerProcessingDTO,
)
async def upsert_processing(
    attachment_id: uuid.UUID,
    payload: WorkerProcessingUpsert,
    _token: Annotated[ApiToken, Depends(require_worker_token)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> AttachmentProcessing:
    """Partial UPSERT.

    Only fields explicitly present in the JSON are written; any
    field listed in ``clear`` is forced to NULL even if also passed
    as a value (clear wins). The attachment must exist (FK) but the
    processing row may not — we'll create it.
    """
    att = await db.get(Attachment, attachment_id)
    if att is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Attachment not found"
        )

    bad_clear = [c for c in payload.clear if c not in _WRITABLE_FIELDS]
    if bad_clear:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"clear contains non-writable field(s): {', '.join(bad_clear)}",
        )

    payload_dict = payload.model_dump(exclude_unset=True, exclude={"clear"})
    fields = {k: v for k, v in payload_dict.items() if k in _WRITABLE_FIELDS}
    for c in payload.clear:
        fields[c] = None

    if not fields:
        # Nothing to do — return current state if any.
        existing = await db.get(AttachmentProcessing, attachment_id)
        if existing is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Empty UPSERT against a non-existent processing row",
            )
        return existing

    now = datetime.now(UTC)  # row-level created_at/updated_at
    values = {
        "attachment_id": attachment_id,
        "created_at": now,
        "updated_at": now,
        **fields,
    }
    update = {**fields, "updated_at": now}
    await db.execute(
        pg_insert(AttachmentProcessing)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[AttachmentProcessing.attachment_id],
            set_=update,
        )
    )
    await db.commit()

    proc = await db.get(AttachmentProcessing, attachment_id)
    assert proc is not None
    return proc


# ── Derivations (multi-version derived PDFs) ────────────────────────────────


class WorkerDerivationDTO(BaseModel):
    """Lineage record for a derived PDF — the response shape the
    HTTP-mode worker rebuilds its DerivationDTO dataclass from."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    attachment_id: uuid.UUID
    kind: str
    storage_key: str
    parent_storage_key: str
    engine: str
    created_at: datetime


class WorkerDerivationCreate(BaseModel):
    """Strict body for POST /derivations/{aid} — workers can only
    write rows for the attachment in the URL, can only set the four
    business columns, and must send all of them. ``id`` and
    ``created_at`` are server-assigned."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    storage_key: str
    parent_storage_key: str
    engine: str


@router.post(
    "/derivations/{attachment_id}",
    response_model=WorkerDerivationDTO,
    status_code=status.HTTP_201_CREATED,
)
async def create_derivation(
    attachment_id: uuid.UUID,
    payload: WorkerDerivationCreate,
    _token: Annotated[ApiToken, Depends(require_worker_token)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> AttachmentDerivation:
    """Insert a derivation row pointing at a freshly-written derived
    PDF. The blob itself is uploaded by the worker via S3 directly;
    this endpoint only persists the lineage record."""
    att = await db.get(Attachment, attachment_id)
    if att is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Attachment not found"
        )
    row = AttachmentDerivation(
        attachment_id=attachment_id,
        kind=payload.kind,
        storage_key=payload.storage_key,
        parent_storage_key=payload.parent_storage_key,
        engine=payload.engine,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.get(
    "/derivations/{attachment_id}/latest",
    response_model=WorkerDerivationDTO,
)
async def latest_derivation(
    attachment_id: uuid.UUID,
    _token: Annotated[ApiToken, Depends(require_worker_token)],
    db: Annotated[AsyncSession, Depends(get_session)],
    kind: Annotated[str, Query(min_length=1)],
) -> AttachmentDerivation:
    """Return the most recent derivation row of ``kind`` for this
    attachment. 404 when none exist — caller falls back to the
    attachment's original storage_key."""
    row = (
        await db.execute(
            select(AttachmentDerivation)
            .where(
                AttachmentDerivation.attachment_id == attachment_id,
                AttachmentDerivation.kind == kind,
            )
            .order_by(AttachmentDerivation.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No derivation of that kind"
        )
    return row
