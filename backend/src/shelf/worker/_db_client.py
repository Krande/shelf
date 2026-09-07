"""Storage / DB access abstraction shared by the worker modules.

Two implementations behind one interface:

* ``SqlBackend`` — the original code path. Uses ``session_factory()``
  to talk to Postgres directly. The CPU worker pod (in-cluster, on
  the trusted network) uses this.
* ``HttpBackend`` — calls ``/api/v1/worker/*`` over HTTPS with a
  worker-scoped bearer token. The GPU worker (on a host outside the
  cluster) uses this so it never holds a Postgres connection string.

Backend selection is one env var so the same image can run in either
mode without a code change:

* ``SHELF_WORKER_BACKEND=sql``  (default) — direct DB, current behaviour.
* ``SHELF_WORKER_BACKEND=api``  — call the API. Requires also:
    ``SHELF_API_BASE_URL``     — e.g. https://shelf.example.com
    ``SHELF_WORKER_API_TOKEN`` — minted via mint_worker_token.

The interface is deliberately small — only the operations all four
worker modules actually share. Anything outside this surface
(per-page row replacement in extract.py, attachment text writes,
queue.publish_*) keeps using its own helpers; those workers are
in-cluster only and don't need brokering.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass
class AttachmentDTO:
    """Subset of the Attachment row workers consult before fetching
    the blob. Mirrors ``shelf.models.Attachment``'s field names so
    handlers can drop in either backend transparently."""

    id: uuid.UUID
    item_id: uuid.UUID
    storage_key: str
    filename: str
    content_type: str
    size_bytes: int | None
    uploaded_at: datetime | None


@dataclass
class DerivationDTO:
    """One row from ``attachment_derivation`` — the lineage record
    for a derived PDF (OCR'd, outlined, etc.)."""

    id: uuid.UUID
    attachment_id: uuid.UUID
    kind: str
    storage_key: str
    parent_storage_key: str
    engine: str
    created_at: datetime


@dataclass
class ProcessingDTO:
    """Subset of AttachmentProcessing — every column workers read."""

    attachment_id: uuid.UUID
    page_count: int | None = None
    text_chars: int | None = None
    needs_ocr: bool = False
    needs_outline: bool = False
    ocr_status: str = "untouched"
    ocr_engine: str | None = None
    ocr_completed_at: datetime | None = None
    outline_status: str = "untouched"
    outline_engine: str | None = None
    outline_completed_at: datetime | None = None
    outline_json: Any | None = None
    progress_done: int | None = None
    progress_total: int | None = None
    original_preserved_at: datetime | None = None


class WorkerDB(Protocol):
    """The interface each worker module talks to.

    Implementations are async; the SQL one is a thin wrapper over
    ``session_factory()``, the HTTP one shells out to httpx. Both
    return the same dataclasses so the calling code is ignorant of
    the backend.
    """

    async def get_attachment(
        self, aid: uuid.UUID
    ) -> AttachmentDTO | None:
        ...

    async def get_processing(
        self, aid: uuid.UUID
    ) -> ProcessingDTO | None:
        ...

    async def upsert_processing(
        self,
        aid: uuid.UUID,
        fields: dict[str, Any] | None = None,
        clear: list[str] | None = None,
    ) -> None:
        """Partial UPSERT. Keys present in ``fields`` are written;
        column names listed in ``clear`` are forced to NULL."""
        ...

    async def insert_derivation(
        self,
        aid: uuid.UUID,
        kind: str,
        storage_key: str,
        parent_storage_key: str,
        engine: str,
    ) -> DerivationDTO:
        """Append a derivation row pointing at a freshly-written
        derived PDF. Returns the row so the caller can log it."""
        ...

    async def latest_derivation(
        self, aid: uuid.UUID, kind: str
    ) -> DerivationDTO | None:
        """Newest ``attachment_derivation`` row for this attachment of
        the given kind, or None if no derivation of that kind exists."""
        ...


# ── SQL backend — direct DB ─────────────────────────────────────────────────


class SqlBackend:
    async def get_attachment(
        self, aid: uuid.UUID
    ) -> AttachmentDTO | None:
        from ..db import session_factory
        from ..models import Attachment

        async with session_factory() as db:
            row = await db.get(Attachment, aid)
            if row is None:
                return None
            return AttachmentDTO(
                id=row.id,
                item_id=row.item_id,
                storage_key=row.storage_key,
                filename=row.filename,
                content_type=row.content_type,
                size_bytes=row.size_bytes,
                uploaded_at=row.uploaded_at,
            )

    async def get_processing(
        self, aid: uuid.UUID
    ) -> ProcessingDTO | None:
        from ..db import session_factory
        from ..models import AttachmentProcessing

        async with session_factory() as db:
            row = await db.get(AttachmentProcessing, aid)
            if row is None:
                return None
            return ProcessingDTO(
                attachment_id=row.attachment_id,
                page_count=row.page_count,
                text_chars=row.text_chars,
                needs_ocr=bool(row.needs_ocr),
                needs_outline=bool(row.needs_outline),
                ocr_status=row.ocr_status,
                ocr_engine=row.ocr_engine,
                ocr_completed_at=row.ocr_completed_at,
                outline_status=row.outline_status,
                outline_engine=row.outline_engine,
                outline_completed_at=row.outline_completed_at,
                outline_json=row.outline_json,
                progress_done=row.progress_done,
                progress_total=row.progress_total,
                original_preserved_at=row.original_preserved_at,
            )

    async def upsert_processing(
        self,
        aid: uuid.UUID,
        fields: dict[str, Any] | None = None,
        clear: list[str] | None = None,
    ) -> None:
        from datetime import UTC
        from datetime import datetime as _dt

        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from ..db import session_factory
        from ..models import AttachmentProcessing

        merged: dict[str, Any] = dict(fields or {})
        for c in clear or []:
            merged[c] = None
        if not merged:
            return
        now = _dt.now(UTC)
        async with session_factory() as db:
            await db.execute(
                pg_insert(AttachmentProcessing)
                .values(
                    attachment_id=aid,
                    created_at=now,
                    updated_at=now,
                    **merged,
                )
                .on_conflict_do_update(
                    index_elements=[AttachmentProcessing.attachment_id],
                    set_={**merged, "updated_at": now},
                )
            )
            await db.commit()

    async def insert_derivation(
        self,
        aid: uuid.UUID,
        kind: str,
        storage_key: str,
        parent_storage_key: str,
        engine: str,
    ) -> DerivationDTO:
        from datetime import UTC
        from datetime import datetime as _dt

        from ..db import session_factory
        from ..models import AttachmentDerivation

        now = _dt.now(UTC)
        row = AttachmentDerivation(
            attachment_id=aid,
            kind=kind,
            storage_key=storage_key,
            parent_storage_key=parent_storage_key,
            engine=engine,
            created_at=now,
        )
        async with session_factory() as db:
            db.add(row)
            await db.commit()
            await db.refresh(row)
        return DerivationDTO(
            id=row.id,
            attachment_id=row.attachment_id,
            kind=row.kind,
            storage_key=row.storage_key,
            parent_storage_key=row.parent_storage_key,
            engine=row.engine,
            created_at=row.created_at,
        )

    async def latest_derivation(
        self, aid: uuid.UUID, kind: str
    ) -> DerivationDTO | None:
        from sqlalchemy import select

        from ..db import session_factory
        from ..models import AttachmentDerivation

        async with session_factory() as db:
            row = (
                await db.execute(
                    select(AttachmentDerivation)
                    .where(
                        AttachmentDerivation.attachment_id == aid,
                        AttachmentDerivation.kind == kind,
                    )
                    .order_by(AttachmentDerivation.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return DerivationDTO(
                id=row.id,
                attachment_id=row.attachment_id,
                kind=row.kind,
                storage_key=row.storage_key,
                parent_storage_key=row.parent_storage_key,
                engine=row.engine,
                created_at=row.created_at,
            )


# ── HTTP backend — calls /api/v1/worker/* ───────────────────────────────────


class HttpBackend:
    """Calls the ``/api/v1/worker/*`` endpoints with a worker-scope
    bearer token. Reads config from env at construction time so the
    backend can be cached as a module singleton.
    """

    def __init__(
        self, base_url: str, token: str, *, timeout: float = 30.0
    ) -> None:
        if not base_url:
            raise RuntimeError(
                "SHELF_API_BASE_URL is required when "
                "SHELF_WORKER_BACKEND=api"
            )
        if not token:
            raise RuntimeError(
                "SHELF_WORKER_API_TOKEN is required when "
                "SHELF_WORKER_BACKEND=api"
            )
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = timeout

    async def get_attachment(
        self, aid: uuid.UUID
    ) -> AttachmentDTO | None:
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.get(
                f"{self._base}/api/v1/worker/attachments/{aid}",
                headers=self._headers,
            )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return _attachment_from_json(r.json())

    async def get_processing(
        self, aid: uuid.UUID
    ) -> ProcessingDTO | None:
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.get(
                f"{self._base}/api/v1/worker/processing/{aid}",
                headers=self._headers,
            )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return _processing_from_json(r.json())

    async def upsert_processing(
        self,
        aid: uuid.UUID,
        fields: dict[str, Any] | None = None,
        clear: list[str] | None = None,
    ) -> None:
        import httpx

        body: dict[str, Any] = dict(fields or {})
        body = _serialize_for_json(body)
        if clear:
            body["clear"] = list(clear)
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.post(
                f"{self._base}/api/v1/worker/processing/{aid}",
                headers=self._headers,
                json=body,
            )
        r.raise_for_status()

    async def insert_derivation(
        self,
        aid: uuid.UUID,
        kind: str,
        storage_key: str,
        parent_storage_key: str,
        engine: str,
    ) -> DerivationDTO:
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.post(
                f"{self._base}/api/v1/worker/derivations/{aid}",
                headers=self._headers,
                json={
                    "kind": kind,
                    "storage_key": storage_key,
                    "parent_storage_key": parent_storage_key,
                    "engine": engine,
                },
            )
        r.raise_for_status()
        return _derivation_from_json(r.json())

    async def latest_derivation(
        self, aid: uuid.UUID, kind: str
    ) -> DerivationDTO | None:
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.get(
                f"{self._base}/api/v1/worker/derivations/{aid}/latest",
                headers=self._headers,
                params={"kind": kind},
            )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return _derivation_from_json(r.json())


def _attachment_from_json(d: dict[str, Any]) -> AttachmentDTO:
    return AttachmentDTO(
        id=uuid.UUID(d["id"]),
        item_id=uuid.UUID(d["item_id"]),
        storage_key=d["storage_key"],
        filename=d["filename"],
        content_type=d["content_type"],
        size_bytes=d["size_bytes"],
        uploaded_at=_parse_dt(d["uploaded_at"]),
    )


def _derivation_from_json(d: dict[str, Any]) -> DerivationDTO:
    return DerivationDTO(
        id=uuid.UUID(d["id"]),
        attachment_id=uuid.UUID(d["attachment_id"]),
        kind=d["kind"],
        storage_key=d["storage_key"],
        parent_storage_key=d["parent_storage_key"],
        engine=d["engine"],
        created_at=datetime.fromisoformat(d["created_at"]),
    )


def _processing_from_json(d: dict[str, Any]) -> ProcessingDTO:
    return ProcessingDTO(
        attachment_id=uuid.UUID(d["attachment_id"]),
        page_count=d.get("page_count"),
        text_chars=d.get("text_chars"),
        needs_ocr=bool(d.get("needs_ocr", False)),
        needs_outline=bool(d.get("needs_outline", False)),
        ocr_status=d.get("ocr_status", "untouched"),
        ocr_engine=d.get("ocr_engine"),
        ocr_completed_at=_parse_dt(d.get("ocr_completed_at")),
        outline_status=d.get("outline_status", "untouched"),
        outline_engine=d.get("outline_engine"),
        outline_completed_at=_parse_dt(d.get("outline_completed_at")),
        outline_json=d.get("outline_json"),
        progress_done=d.get("progress_done"),
        progress_total=d.get("progress_total"),
        original_preserved_at=_parse_dt(d.get("original_preserved_at")),
    )


def _parse_dt(v: str | None) -> datetime | None:
    if v is None:
        return None
    # FastAPI serialises datetime as ISO 8601 with offset; fromisoformat
    # handles that on 3.11+.
    return datetime.fromisoformat(v)


def _serialize_for_json(d: dict[str, Any]) -> dict[str, Any]:
    """Convert datetimes to ISO strings; UUIDs to strings. Pydantic
    on the server side parses them back."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, uuid.UUID):
            out[k] = str(v)
        else:
            out[k] = v
    return out


# ── Singleton selection ─────────────────────────────────────────────────────


_db: WorkerDB | None = None


def get_db() -> WorkerDB:
    """Return the configured backend, instantiating on first call."""
    global _db
    if _db is not None:
        return _db
    backend = os.environ.get("SHELF_WORKER_BACKEND", "sql").strip().lower()
    if backend == "api":
        _db = HttpBackend(
            base_url=os.environ.get("SHELF_API_BASE_URL", ""),
            token=os.environ.get("SHELF_WORKER_API_TOKEN", ""),
        )
    elif backend == "sql":
        _db = SqlBackend()
    else:
        raise RuntimeError(
            f"SHELF_WORKER_BACKEND must be 'sql' or 'api', got {backend!r}"
        )
    return _db


def reset_db() -> None:
    """Used by tests + for rebuilding the singleton after env changes."""
    global _db
    _db = None
