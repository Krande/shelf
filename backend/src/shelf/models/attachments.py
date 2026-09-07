import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, deferred, mapped_column

from .base import UUIDPK, Base, Timestamps, utcnow


class ExtractionStatus(StrEnum):
    """State of the PDF body-text extraction pipeline.

    `pending` is set when the upload completes; the worker rewrites
    the row to one of the terminal states. `empty` is the "looks
    like a scanned PDF — needs OCR" bucket and is the listing the
    UI surfaces under "PDFs without text".
    """

    pending = "pending"
    extracted = "extracted"
    empty = "empty"
    failed = "failed"
    skipped = "skipped"  # non-PDF content type


class Attachment(UUIDPK, Timestamps, Base):
    """A file attached to an item.

    Lifecycle: register → PUT to bucket → complete.

    `uploaded_at` is null while the row is registered but the object
    hasn't been confirmed; clients (or a periodic HEAD-check pass)
    set it to mark the attachment ready. Inline /api/v1/upload sets
    it as part of the same request because the proxy actually wrote
    the bytes. Existing rows from before the column existed are
    backfilled so they don't appear pending.

    Full-text columns (`text_content`, `text_chars`, `extracted_at`,
    `extraction_status`) are written by the extraction worker after
    the upload completes. The Postgres-side `tsv` GENERATED column +
    GIN index back the ?scope=fulltext search path; we don't model it
    here because it's never read or written from Python.
    """

    __tablename__ = "attachments"

    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), nullable=False
    )
    storage_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    content_type: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extracted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    extraction_status: Mapped[str | None] = mapped_column(String, nullable=True)
    # Postgres GENERATED ALWAYS column. `Computed(... persisted=True)`
    # tells SQLAlchemy not to include `tsv` in INSERT/UPDATE statements
    # — Postgres rejects writes to generated columns. Deferred so the
    # bytes don't load on every selectinload.
    tsv: Mapped[Any] = deferred(
        mapped_column(
            "tsv",
            TSVECTOR,
            Computed(
                "to_tsvector('english', coalesce(text_content,''))",
                persisted=True,
            ),
            nullable=True,
        )
    )


class AttachmentProcessing(Timestamps, Base):
    """Post-extraction quality + downstream-worker state.

    Phase A (current): the extract worker writes the metrics columns
    plus the two ``needs_*`` flags. Phase B/C consumers read those
    flags, kick off OCR or outline generation, and set the
    ``*_status`` / ``outline_json`` fields. The 1:1 split keeps the
    quality assessment cheap to query and the result blobs out of
    the hot ``attachments`` row.
    """

    __tablename__ = "attachment_processing"

    attachment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("attachments.id", ondelete="CASCADE"), primary_key=True
    )
    assessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    replacement_char_ratio: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    alpha_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    chars_per_page: Mapped[float | None] = mapped_column(Float, nullable=True)
    toc_entry_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    needs_ocr: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    needs_outline: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    ocr_status: Mapped[str] = mapped_column(
        String, nullable=False, default="untouched"
    )
    ocr_engine: Mapped[str | None] = mapped_column(String, nullable=True)
    ocr_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    outline_status: Mapped[str] = mapped_column(
        String, nullable=False, default="untouched"
    )
    outline_engine: Mapped[str | None] = mapped_column(String, nullable=True)
    outline_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    outline_json: Mapped[Any] = mapped_column(JSONB, nullable=True)
    # Set the moment we copy the live blob to ``<storage_key>.original``
    # — either eagerly on upload completion or lazily before the first
    # OCR rewrite. A non-null value is the SPA's "show Restore" gate.
    original_preserved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Live progress for the currently-running worker (OCR or outline).
    # Nullable: only populated while a job is in flight, only for
    # workers that emit progress. The OCR worker tails ocrmypdf's
    # stderr to update these every ~2s.
    progress_done: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_total: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AttachmentDerivation(Base):
    """One derived PDF (OCR'd, outlined, …) for an attachment.

    Rows accumulate as workers run, so we keep a full history per
    ``(attachment_id, kind)`` rather than overwriting a single
    pointer. The reader uses the most-recent row of each kind to
    resolve "current view" (outline > ocr > original), but the UI
    also exposes the full list so the user can flip between
    versions to compare results from different engines or builds.

    ``parent_storage_key`` is the input bytes this derivation was
    produced from. Walking the chain via that pointer reconstructs
    the lineage back to the original (which is always at
    ``attachments.storage_key`` in the new world). Stored as a
    plain key string rather than an FK to another derivation so a
    derivation rooted on the original needs no special-case row;
    the parent is just the attachment's storage_key.
    """

    __tablename__ = "attachment_derivation"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )
    attachment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("attachments.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    storage_key: Mapped[str] = mapped_column(
        String, nullable=False, unique=True
    )
    parent_storage_key: Mapped[str] = mapped_column(String, nullable=False)
    engine: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class AttachmentPage(Base):
    """One row per PDF page, populated by the extraction worker.

    Powers the per-page snippet endpoint: the page-scoped `tsv` GIN
    index lets a single websearch_to_tsquery match return ranked rows
    with their `page_number` already attached, so the UI can deep-link
    each snippet straight to that page in the reader.

    Cascades on attachment delete; no separate id — the (attachment_id,
    page_number) pair is the natural primary key.
    """

    __tablename__ = "attachment_pages"

    attachment_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("attachments.id", ondelete="CASCADE"),
        primary_key=True,
    )
    page_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Page dimensions in PDF user-space (1 pt = 1/72 inch) at scale=1.
    # The reader seeds its virtualizer height map from these instead
    # of parsing every page client-side at load time. Nullable for
    # rows written before the worker started capturing dims.
    width_pts: Mapped[float | None] = mapped_column(Float, nullable=True)
    height_pts: Mapped[float | None] = mapped_column(Float, nullable=True)
    tsv: Mapped[Any] = deferred(
        mapped_column(
            "tsv",
            TSVECTOR,
            Computed(
                "to_tsvector('english', coalesce(text,''))",
                persisted=True,
            ),
            nullable=True,
        )
    )
