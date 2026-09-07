"""Admin endpoints for the PDF post-extraction pipeline.

Surfaces the ``attachment_processing`` table to the SPA: per-bucket
counts (needs OCR / needs outline / OCR queued / outline running …),
a paginated listing, and POST endpoints that manually re-enqueue
a row through OCR (Phase B) or outline generation (Phase C).

The extract worker writes the metric columns automatically; this
module is the operator's escape hatch for "extract worker says
needs_ocr is true but never re-fired" or "I want to force a fresh
outline pass on a doc that already has one."

Auth model: same as ``extraction.py`` — every read/write is scoped
to spaces the caller owns.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import get_current_user
from ..db import get_session
from ..models import Attachment, AttachmentProcessing, Item, Space, User
from ..services import queue, storage

router = APIRouter(tags=["processing"])

PDF_CONTENT_TYPE = "application/pdf"

# Processing-bucket keys. Same vocabulary as the schema's *_status
# columns plus aggregate views. ``not_assessed`` covers attachments
# that don't have a processing row yet (extract worker hasn't run).
ProcessingFilter = Literal[
    "needs_ocr",
    "needs_outline",
    "ocr_queued",
    "ocr_running",
    "ocr_done",
    "ocr_failed",
    "outline_queued",
    "outline_running",
    "outline_done",
    "outline_failed",
    "not_assessed",
    "all",
]


class ProcessingStats(BaseModel):
    """Counts of attachments in each processing bucket. Mutually-
    exclusive ones (e.g. ocr_queued vs ocr_done) sum to total assessed
    rows; needs_ocr / needs_outline overlap any state."""

    needs_ocr: int
    needs_outline: int
    ocr_untouched: int
    ocr_queued: int
    ocr_running: int
    ocr_done: int
    ocr_failed: int
    outline_untouched: int
    outline_queued: int
    outline_running: int
    outline_done: int
    outline_failed: int
    assessed: int
    not_assessed: int
    total_pdfs: int


class ProcessingAttachment(BaseModel):
    id: uuid.UUID
    item_id: uuid.UUID
    item_title: str | None
    filename: str
    page_count: int | None
    text_chars: int | None
    chars_per_page: float | None
    alpha_ratio: float | None
    replacement_char_ratio: float | None
    toc_entry_count: int | None
    needs_ocr: bool
    needs_outline: bool
    ocr_status: str
    ocr_engine: str | None
    ocr_completed_at: datetime | None
    outline_status: str
    outline_engine: str | None
    outline_completed_at: datetime | None
    assessed_at: datetime | None
    progress_done: int | None
    progress_total: int | None


class TriggerResult(BaseModel):
    attachment_id: uuid.UUID
    job: Literal["ocr", "outline"]
    enqueued: bool


def _scoped_pdf_attachments() -> Any:
    return (
        select(Attachment, Item.data["title"].astext.label("item_title"))
        .join(Item, Item.id == Attachment.item_id)
        .join(Space, Space.id == Item.space_id)
    )


def _scope_filter(stmt: Any, user_id: uuid.UUID) -> Any:
    return stmt.where(
        Space.owner_id == user_id,
        Attachment.content_type == PDF_CONTENT_TYPE,
        Attachment.uploaded_at.is_not(None),
    )


@router.get("/api/me/processing/stats", response_model=ProcessingStats)
async def stats(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ProcessingStats:
    """Counts of PDF attachments in each processing bucket. The
    join is left so attachments without an ``attachment_processing``
    row land in ``not_assessed`` and the user knows their backfill
    is incomplete."""
    # `case` against status for granular OCR/outline buckets. NULL
    # join (no processing row) maps to "untouched" too — that's the
    # default value the worker would write on first contact.
    ocr_bucket = case(
        (AttachmentProcessing.ocr_status == "queued", "queued"),
        (AttachmentProcessing.ocr_status == "running", "running"),
        (AttachmentProcessing.ocr_status == "done", "done"),
        (AttachmentProcessing.ocr_status == "failed", "failed"),
        else_="untouched",
    )
    outline_bucket = case(
        (AttachmentProcessing.outline_status == "queued", "queued"),
        (AttachmentProcessing.outline_status == "running", "running"),
        (AttachmentProcessing.outline_status == "done", "done"),
        (AttachmentProcessing.outline_status == "failed", "failed"),
        else_="untouched",
    )
    stmt = (
        select(
            func.count(Attachment.id).label("total"),
            func.count(AttachmentProcessing.attachment_id).label("assessed"),
            func.coalesce(
                func.sum(
                    case(
                        (AttachmentProcessing.needs_ocr.is_(True), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("needs_ocr"),
            func.coalesce(
                func.sum(
                    case(
                        (AttachmentProcessing.needs_outline.is_(True), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("needs_outline"),
            func.coalesce(
                func.sum(case((ocr_bucket == "untouched", 1), else_=0)),
                0,
            ).label("ocr_untouched"),
            func.coalesce(
                func.sum(case((ocr_bucket == "queued", 1), else_=0)),
                0,
            ).label("ocr_queued"),
            func.coalesce(
                func.sum(case((ocr_bucket == "running", 1), else_=0)),
                0,
            ).label("ocr_running"),
            func.coalesce(
                func.sum(case((ocr_bucket == "done", 1), else_=0)),
                0,
            ).label("ocr_done"),
            func.coalesce(
                func.sum(case((ocr_bucket == "failed", 1), else_=0)),
                0,
            ).label("ocr_failed"),
            func.coalesce(
                func.sum(case((outline_bucket == "untouched", 1), else_=0)),
                0,
            ).label("outline_untouched"),
            func.coalesce(
                func.sum(case((outline_bucket == "queued", 1), else_=0)),
                0,
            ).label("outline_queued"),
            func.coalesce(
                func.sum(case((outline_bucket == "running", 1), else_=0)),
                0,
            ).label("outline_running"),
            func.coalesce(
                func.sum(case((outline_bucket == "done", 1), else_=0)),
                0,
            ).label("outline_done"),
            func.coalesce(
                func.sum(case((outline_bucket == "failed", 1), else_=0)),
                0,
            ).label("outline_failed"),
        )
        .select_from(Attachment)
        .join(Item, Item.id == Attachment.item_id)
        .join(Space, Space.id == Item.space_id)
        .outerjoin(
            AttachmentProcessing,
            AttachmentProcessing.attachment_id == Attachment.id,
        )
        .where(
            Space.owner_id == user.id,
            Attachment.content_type == PDF_CONTENT_TYPE,
            Attachment.uploaded_at.is_not(None),
        )
    )
    row = (await db.execute(stmt)).one()
    total = int(row.total or 0)
    assessed = int(row.assessed or 0)
    return ProcessingStats(
        needs_ocr=int(row.needs_ocr),
        needs_outline=int(row.needs_outline),
        ocr_untouched=int(row.ocr_untouched),
        ocr_queued=int(row.ocr_queued),
        ocr_running=int(row.ocr_running),
        ocr_done=int(row.ocr_done),
        ocr_failed=int(row.ocr_failed),
        outline_untouched=int(row.outline_untouched),
        outline_queued=int(row.outline_queued),
        outline_running=int(row.outline_running),
        outline_done=int(row.outline_done),
        outline_failed=int(row.outline_failed),
        assessed=assessed,
        not_assessed=total - assessed,
        total_pdfs=total,
    )


@router.get(
    "/api/me/processing/attachments",
    response_model=list[ProcessingAttachment],
)
async def list_attachments(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    bucket: Annotated[ProcessingFilter, Query()] = "all",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ProcessingAttachment]:
    """Paginated list of PDF attachments + their processing row.

    Filter via ``bucket`` to match the home-screen counts. ``all``
    returns every PDF including unassessed ones (with their
    processing fields nulled out)."""
    stmt = (
        select(
            Attachment,
            Item.data["title"].astext.label("item_title"),
            AttachmentProcessing,
        )
        .join(Item, Item.id == Attachment.item_id)
        .join(Space, Space.id == Item.space_id)
        .outerjoin(
            AttachmentProcessing,
            AttachmentProcessing.attachment_id == Attachment.id,
        )
    )
    stmt = _scope_filter(stmt, user.id)

    if bucket == "needs_ocr":
        stmt = stmt.where(AttachmentProcessing.needs_ocr.is_(True))
    elif bucket == "needs_outline":
        stmt = stmt.where(AttachmentProcessing.needs_outline.is_(True))
    elif bucket == "ocr_queued":
        stmt = stmt.where(AttachmentProcessing.ocr_status == "queued")
    elif bucket == "ocr_running":
        stmt = stmt.where(AttachmentProcessing.ocr_status == "running")
    elif bucket == "ocr_done":
        stmt = stmt.where(AttachmentProcessing.ocr_status == "done")
    elif bucket == "ocr_failed":
        stmt = stmt.where(AttachmentProcessing.ocr_status == "failed")
    elif bucket == "outline_queued":
        stmt = stmt.where(AttachmentProcessing.outline_status == "queued")
    elif bucket == "outline_running":
        stmt = stmt.where(AttachmentProcessing.outline_status == "running")
    elif bucket == "outline_done":
        stmt = stmt.where(AttachmentProcessing.outline_status == "done")
    elif bucket == "outline_failed":
        stmt = stmt.where(AttachmentProcessing.outline_status == "failed")
    elif bucket == "not_assessed":
        stmt = stmt.where(AttachmentProcessing.attachment_id.is_(None))
    # else "all": no extra filter

    stmt = stmt.order_by(Attachment.uploaded_at.desc()).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).all()
    out: list[ProcessingAttachment] = []
    for att, item_title, proc in rows:
        out.append(
            ProcessingAttachment(
                id=att.id,
                item_id=att.item_id,
                item_title=item_title,
                filename=att.filename,
                page_count=proc.page_count if proc else None,
                text_chars=proc.text_chars if proc else None,
                chars_per_page=proc.chars_per_page if proc else None,
                alpha_ratio=proc.alpha_ratio if proc else None,
                replacement_char_ratio=(
                    proc.replacement_char_ratio if proc else None
                ),
                toc_entry_count=proc.toc_entry_count if proc else None,
                needs_ocr=bool(proc.needs_ocr) if proc else False,
                needs_outline=bool(proc.needs_outline) if proc else False,
                ocr_status=proc.ocr_status if proc else "untouched",
                ocr_engine=proc.ocr_engine if proc else None,
                ocr_completed_at=proc.ocr_completed_at if proc else None,
                outline_status=proc.outline_status if proc else "untouched",
                outline_engine=proc.outline_engine if proc else None,
                outline_completed_at=(
                    proc.outline_completed_at if proc else None
                ),
                assessed_at=proc.assessed_at if proc else None,
                progress_done=proc.progress_done if proc else None,
                progress_total=proc.progress_total if proc else None,
            )
        )
    return out


async def _resolve_pdf(
    db: AsyncSession, user: User, attachment_id: uuid.UUID
) -> Attachment:
    """Auth + content-type gate shared by both manual triggers."""
    att = await db.get(Attachment, attachment_id)
    if att is None or att.uploaded_at is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    item = await db.get(Item, att.item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    space = await db.get(Space, item.space_id)
    if space is None or space.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    if att.content_type != PDF_CONTENT_TYPE:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Only PDF attachments support post-extraction processing",
        )
    return att


async def _set_status_queued(
    db: AsyncSession, aid: uuid.UUID, field: Literal["ocr", "outline"]
) -> None:
    """UPSERT ``attachment_processing`` to mark ``{field}_status='queued'``.

    The row may not exist yet (extract worker hasn't run); create
    a stub so the listing can show "manually triggered" before the
    worker even gets there."""
    now = datetime.now(UTC)
    # Explicitly dict[str, Any]: inferred from the initialisers these would be
    # dict[str, UUID | datetime], and the status strings assigned below would
    # not fit.
    base: dict[str, Any] = {"attachment_id": aid, "created_at": now, "updated_at": now}
    set_: dict[str, Any] = {"updated_at": now}
    if field == "ocr":
        base["ocr_status"] = "queued"
        set_["ocr_status"] = "queued"
        # Don't pre-fill the engine label here — the worker writes its
        # own versioned identifier when the run actually starts (see
        # shelf.worker.ocr._engine_label). Pre-filling with a stale
        # tag would mislead the UI between trigger and pickup.
    else:
        base["outline_status"] = "queued"
        set_["outline_status"] = "queued"
    await db.execute(
        pg_insert(AttachmentProcessing)
        .values(**base)
        .on_conflict_do_update(
            index_elements=[AttachmentProcessing.attachment_id],
            set_=set_,
        )
    )


@router.post(
    "/api/attachments/{attachment_id}/processing/ocr",
    response_model=TriggerResult,
)
async def trigger_ocr(
    attachment_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> TriggerResult:
    """Force-enqueue an OCR job for one attachment, bypassing the
    automatic ``needs_ocr`` heuristic. Use this to retry a
    ``failed`` row, or to re-OCR a doc whose previous run didn't
    actually fix the text layer (handwriting, exotic scripts)."""
    att = await _resolve_pdf(db, user, attachment_id)
    await _set_status_queued(db, att.id, "ocr")
    await db.commit()
    enqueued = await queue.publish_ocr(att.id)
    return TriggerResult(attachment_id=att.id, job="ocr", enqueued=enqueued)


class RestoreOriginalResult(BaseModel):
    attachment_id: uuid.UUID
    restored: bool


class CancelRequest(BaseModel):
    job: Literal["ocr", "outline"]


class CancelResult(BaseModel):
    attachment_id: uuid.UUID
    job: Literal["ocr", "outline"]
    previous_status: str
    cancelled: bool


@router.post(
    "/api/attachments/{attachment_id}/processing/restore_original",
    response_model=RestoreOriginalResult,
)
async def restore_original(
    attachment_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> RestoreOriginalResult:
    """Copy ``<storage_key>.original`` back over the live blob.

    This is the escape hatch for "OCR or another worker rewrote the
    PDF and made it worse." The original is preserved exactly once,
    so a restore is always to the as-uploaded state — not to the
    most-recent-pre-mutation state.

    After the restore we reset ocr_status / outline_status to
    ``untouched`` and republish an extract job so search + per-page
    rows reconverge on the original bytes. The original sibling
    object is *not* deleted — a user might want to redo the same
    restore again after another bad worker pass.
    """
    att = await _resolve_pdf(db, user, attachment_id)
    try:
        ok = await storage.restore_from_original(att.storage_key)
    except Exception as e:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"restore failed: {e}",
        ) from e
    if not ok:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No preserved original for this attachment",
        )

    now = datetime.now(UTC)
    await db.execute(
        pg_insert(AttachmentProcessing)
        .values(
            attachment_id=att.id,
            ocr_status="untouched",
            outline_status="untouched",
            ocr_completed_at=None,
            outline_completed_at=None,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[AttachmentProcessing.attachment_id],
            set_={
                "ocr_status": "untouched",
                "outline_status": "untouched",
                "ocr_completed_at": None,
                "outline_completed_at": None,
                "ocr_engine": None,
                "outline_engine": None,
                "outline_json": None,
                "updated_at": now,
            },
        )
    )
    await db.commit()

    # Re-run extraction so text_content / per-page rows reflect the
    # restored bytes. We don't auto-trigger OCR here — if the doc
    # genuinely needs it the heuristic will fire again on extract;
    # if the user is restoring because OCR was wrong, they don't
    # want a fresh OCR run yet.
    await queue.publish_extract(att.id)
    return RestoreOriginalResult(attachment_id=att.id, restored=True)


@router.post(
    "/api/attachments/{attachment_id}/processing/cancel",
    response_model=CancelResult,
)
async def cancel_processing(
    attachment_id: uuid.UUID,
    payload: CancelRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> CancelResult:
    """Mark a queued or running OCR / outline job as cancelled.

    Semantics depend on what the worker is doing right now:

    * ``queued`` — the worker hasn't picked the message up yet. It
      will read the cancelled status when it does and short-circuit
      without any heavy work.
    * ``running`` — the worker is mid-Tesseract/Marker call. Python
      threadpools can't interrupt synchronous C code; the thread
      runs to completion but the worker's post-write status check
      drops the result. The row goes ``cancelled`` immediately
      from the user's perspective; the wasted CPU is the cost of
      ``cancel`` not being a real interrupt.
    * anything else (``untouched`` / ``done`` / ``failed`` /
      ``cancelled``) — there's nothing in flight so the call is a
      no-op; we still return 200 with ``cancelled=False`` so the
      SPA can confirm and refresh.
    """
    att = await _resolve_pdf(db, user, attachment_id)
    proc = await db.get(AttachmentProcessing, att.id)
    field = "ocr_status" if payload.job == "ocr" else "outline_status"
    prev = getattr(proc, field) if proc else "untouched"
    if prev not in ("queued", "running"):
        return CancelResult(
            attachment_id=att.id,
            job=payload.job,
            previous_status=prev,
            cancelled=False,
        )
    now = datetime.now(UTC)
    await db.execute(
        pg_insert(AttachmentProcessing)
        .values(
            attachment_id=att.id,
            **{field: "cancelled"},
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[AttachmentProcessing.attachment_id],
            set_={field: "cancelled", "updated_at": now},
        )
    )
    await db.commit()
    return CancelResult(
        attachment_id=att.id,
        job=payload.job,
        previous_status=prev,
        cancelled=True,
    )


@router.post(
    "/api/attachments/{attachment_id}/processing/ocr_gpu",
    response_model=TriggerResult,
)
async def trigger_ocr_gpu(
    attachment_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> TriggerResult:
    """Force-enqueue a GPU-tier OCR job (olmOCR / Qwen2.5-VL-7B).

    Same shape as the Tesseract trigger but routes to the GPU
    consumer. Use this for hard documents where Tesseract's text
    layer is sparse or wrong: complex layouts, embedded math,
    multi-column papers, scans with mixed languages — the kinds of
    PDFs vision-language OCR handles that classical OCR doesn't.

    The row's ``ocr_status`` field is shared between engines, so
    Cancel and Restore continue to work whichever engine is active.
    """
    att = await _resolve_pdf(db, user, attachment_id)
    await _set_status_queued(db, att.id, "ocr")
    await db.commit()
    enqueued = await queue.publish_ocr_gpu(att.id)
    return TriggerResult(
        attachment_id=att.id, job="ocr", enqueued=enqueued
    )


@router.post(
    "/api/attachments/{attachment_id}/processing/outline",
    response_model=TriggerResult,
)
async def trigger_outline(
    attachment_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> TriggerResult:
    """Force-enqueue an outline-generation job. The Phase C worker
    consumes this; if no outline worker is running the row stays
    ``outline_status='queued'`` durably so the GPU pod picks it up
    when it comes online."""
    att = await _resolve_pdf(db, user, attachment_id)
    await _set_status_queued(db, att.id, "outline")
    await db.commit()
    enqueued = await queue.publish_outline(att.id)
    return TriggerResult(
        attachment_id=att.id, job="outline", enqueued=enqueued
    )
