"""GPU-tier OCR worker (olmOCR-2 / Qwen2.5-VL-7B).

Same shape as ``shelf.worker.ocr`` (the Tesseract path) but spawns
``shelf.worker.olmocr_runner`` instead of the ocrmypdf CLI. Reuses
the same ``ocr_status`` field on ``attachment_processing`` so a
single Cancel button works whichever engine is active; the
``ocr_engine`` label is what tells you which engine handled a given
row (``olmocr/<ver> shelf/sha-…`` vs ``ocrmypdf/<ver> shelf/sha-…``).

The runner does the actual ML work in a child Python process so a
SIGTERM cleanly kills it (torch + transformers can't be interrupted
in-process). Progress comes back via the runner's stderr in the
same format ocrmypdf uses, so the existing parser populates
``progress_done`` / ``progress_total`` for free.

This module is import-safe on the CPU pod: the heavy ML deps live
inside ``olmocr_runner``, not here. Only pods that select the
``ocr-gpu`` consumer (via ``SHELF_WORKER_CONSUMERS=ocr-gpu``) run
the runner; other pods import this module purely to satisfy the
worker registry's loader and never invoke its body.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

import obstore

from .. import config
from ..services import queue, storage
from ._db_client import get_db

log = logging.getLogger("shelf.worker.olmocr")

CANCEL_POLL_INTERVAL = 2.0
CANCEL_GRACE_SECONDS = 10.0  # ML runners need a moment to free CUDA mem
PROGRESS_DB_INTERVAL = 2.0
ERROR_TAIL_BYTES = 2000

# Per-attachment runner logs survive on disk for post-mortem. Successful
# and cancelled runs are cleaned up; failed-run logs are kept so the
# operator can `cat` them after the worker has moved on. /tmp is fine —
# the volume is small (a few hundred KB per run, mostly tqdm overwrites)
# and we don't need cross-restart persistence.
_RUNNER_LOG_DIR = Path(
    os.environ.get("SHELF_OLMOCR_LOG_DIR", "/tmp/shelf-olmocr")
)

# Same parser shape as ocr.py — the runner is the one that has to
# match this format on stderr (it does; see olmocr_runner.process_pdf).
_PAGE_LINE_RE = re.compile(r"^\s+(\d+)\s+\S")
_TOTAL_LINE_RE = re.compile(r"^Parsing\s+(\d+)\s+pages\s")

_RUNNING_PROCS: dict[uuid.UUID, asyncio.subprocess.Process] = {}


def _engine_label() -> str:
    try:
        from importlib.metadata import version

        ver = version("olmocr")
    except Exception:
        ver = "?"
    return f"olmocr/{ver} shelf/{config.settings.image_tag}"


async def olmocr_attachment(attachment_id: str | uuid.UUID) -> None:
    """Run olmOCR-2 against one attachment and republish extraction."""
    aid = uuid.UUID(str(attachment_id))
    db = get_db()
    att = await db.get_attachment(aid)
    if att is None:
        log.warning("attachment %s not found; skipping ocr-gpu", aid)
        return
    if att.uploaded_at is None:
        log.warning("attachment %s not uploaded; skipping ocr-gpu", aid)
        return
    if att.content_type != "application/pdf":
        log.warning(
            "attachment %s is %s; skipping ocr-gpu",
            aid,
            att.content_type,
        )
        return

    existing_proc = await db.get_processing(aid)
    if existing_proc and existing_proc.ocr_status == "cancelled":
        log.info("ocr-gpu cancelled before pickup for %s; skipping", aid)
        return

    await db.upsert_processing(
        aid,
        {"ocr_status": "running", "ocr_engine": _engine_label()},
    )
    # OCR always runs against the attachment's original PDF, never
    # against a previous OCR pass — re-running OCR is meant to
    # produce a fresh comparison candidate, not a chain of OCRs on
    # OCRs. The previous OCR derivation row stays intact for the UI.
    parent_key = att.storage_key

    body = await _fetch_body(parent_key)
    new_body = await _run_olmocr_async(aid, body)
    if new_body is None:
        log.info("ocr-gpu cancelled mid-run for %s; result discarded", aid)
        return

    # Late-cancel check — see ocr.py for the same reasoning.
    check = await db.get_processing(aid)
    if check and check.ocr_status != "running":
        log.info(
            "ocr-gpu result discarded for %s (status=%s); cancelled late",
            aid,
            check.ocr_status,
        )
        return

    new_key = storage.derived_key(att.storage_key, "ocr")
    await _put_body(new_key, new_body)
    await db.insert_derivation(
        aid,
        kind="ocr",
        storage_key=new_key,
        parent_storage_key=parent_key,
        engine=_engine_label(),
    )

    now = datetime.now(UTC)
    await db.upsert_processing(
        aid,
        {
            "ocr_status": "done",
            "ocr_engine": _engine_label(),
            "ocr_completed_at": now,
        },
        clear=["progress_done", "progress_total"],
    )

    # Chain into outline + extract. Outline runs on the CPU pod
    # (font heuristic, no GPU) and produces its own derivation.
    # Extract re-reads the OCR'd text into the searchable index.
    await db.upsert_processing(aid, {"outline_status": "queued"})
    await queue.publish_outline(aid)
    await queue.publish_extract(aid)
    log.info(
        "ocr-gpu complete for %s; outline + re-extract enqueued", aid
    )


async def mark_failed_terminal(attachment_id: str | uuid.UUID) -> None:
    aid = uuid.UUID(str(attachment_id))
    try:
        await get_db().upsert_processing(
            aid,
            {"ocr_status": "failed", "ocr_engine": _engine_label()},
            clear=["progress_done", "progress_total"],
        )
    except Exception:
        log.exception("ocr-gpu mark_failed_terminal failed for %s", aid)


async def _run_olmocr_async(
    aid: uuid.UUID, body: bytes
) -> bytes | None:
    """Spawn ``python -m shelf.worker.olmocr_runner`` as a child
    process so SIGTERM is a real interrupt (vs trying to cancel
    Qwen.generate from the parent's event loop, which can't work).
    Returns the OCR'd PDF bytes or None on cancel."""
    with (
        tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as inp,
        tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as out,
    ):
        inp_path = inp.name
        out_path = out.name
        inp.write(body)

    cmd = [
        sys.executable,
        "-m",
        "shelf.worker.olmocr_runner",
        inp_path,
        out_path,
    ]

    _RUNNER_LOG_DIR.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    log_path = _RUNNER_LOG_DIR / f"{aid}.log"

    try:
        # buffering=0 → every chunk hits disk so a SIGKILL leaves us
        # with the partial log instead of an empty file. Open in
        # append mode so retries (delivery 2/3) accumulate rather than
        # clobber — handy when comparing successive failures. Both
        # operations are tiny (open() of a /tmp file, mkdir of an
        # existing dir) and only happen once per OCR job — to_thread
        # would be more ceremony than payoff.
        with open(log_path, "ab", buffering=0) as logf:  # noqa: ASYNC230
            logf.write(
                f"\n=== {datetime.now(UTC).isoformat()} aid={aid} ===\n".encode()
            )
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _RUNNING_PROCS[aid] = proc
            cancel_task = asyncio.create_task(_cancel_poll(aid))
            progress_task = asyncio.create_task(
                _read_stderr_with_progress(aid, proc, logf)
            )
            try:
                rc = await proc.wait()
            finally:
                cancel_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await cancel_task
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await progress_task
                _RUNNING_PROCS.pop(aid, None)

        if rc is not None and rc < 0:
            log_path.unlink(missing_ok=True)
            return None
        if rc != 0:
            tail = _read_log_tail(log_path)
            raise RuntimeError(
                f"olmocr_runner exit={rc} (full log: {log_path}): "
                f"...{tail}"
            )

        log_path.unlink(missing_ok=True)
        return await asyncio.to_thread(_read_file_sync, out_path)
    finally:
        for p in (inp_path, out_path):
            try:
                os.unlink(p)
            except OSError:
                pass


def _read_log_tail(path: Path, n: int = ERROR_TAIL_BYTES) -> str:
    """Read the last `n` bytes of the runner log so the RuntimeError
    surfaces the actual failure, not the start of the tqdm spam."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            sz = f.tell()
            f.seek(max(0, sz - n))
            data = f.read()
        return data.decode("utf-8", errors="replace").strip()
    except Exception:
        return "<could not read runner log>"


def _read_file_sync(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


async def _cancel_poll(aid: uuid.UUID) -> None:
    while True:
        try:
            await asyncio.sleep(CANCEL_POLL_INTERVAL)
            row = await get_db().get_processing(aid)
            if row is None or row.ocr_status != "cancelled":
                continue
            proc = _RUNNING_PROCS.get(aid)
            if proc is None or proc.returncode is not None:
                return
            log.info(
                "cancel signal seen for %s; SIGTERM olmocr_runner pid=%s",
                aid,
                proc.pid,
            )
            try:
                proc.terminate()
            except ProcessLookupError:
                return
            try:
                await asyncio.wait_for(
                    proc.wait(), timeout=CANCEL_GRACE_SECONDS
                )
            except TimeoutError:
                log.warning(
                    "olmocr_runner pid=%s still alive after SIGTERM grace; "
                    "escalating to SIGKILL",
                    proc.pid,
                )
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
            return
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("cancel-poll iteration failed for %s", aid)


async def _read_stderr_with_progress(
    aid: uuid.UUID, proc: asyncio.subprocess.Process, logf: IO[bytes]
) -> None:
    """Tee the runner's stderr to `logf` and parse it for progress.
    `logf` is opened with buffering=0 so each write hits disk; that
    way a hard kill still leaves us with the partial transcript."""
    if proc.stderr is None:
        return
    pages_seen = 0
    total: int | None = None
    last_db_write = 0.0
    try:
        async for raw in proc.stderr:
            try:
                logf.write(raw)
            except Exception:
                log.exception("runner-log write failed for %s", aid)

            line = raw.decode("utf-8", errors="replace").rstrip()
            tm = _TOTAL_LINE_RE.match(line)
            if tm and total is None:
                total = int(tm.group(1))
                await _write_progress(aid, pages_seen, total)
                last_db_write = time.monotonic()
                continue

            pm = _PAGE_LINE_RE.match(line)
            if pm:
                page_num = int(pm.group(1))
                if page_num > pages_seen:
                    pages_seen = page_num
                    now = time.monotonic()
                    if now - last_db_write >= PROGRESS_DB_INTERVAL:
                        await _write_progress(aid, pages_seen, total)
                        last_db_write = now
        if pages_seen > 0 or total is not None:
            await _write_progress(aid, pages_seen, total)
    except Exception:
        log.exception("progress reader for %s failed", aid)


async def _write_progress(
    aid: uuid.UUID, done: int, total: int | None
) -> None:
    try:
        fields: dict[str, object] = {"progress_done": done}
        if total is not None:
            fields["progress_total"] = total
        await get_db().upsert_processing(aid, fields)
    except Exception:
        log.exception("write_progress failed for %s", aid)


async def _fetch_body(storage_key: str) -> bytes:
    res = await obstore.get_async(storage.get_store(), storage_key)
    buf = await res.bytes_async()
    return bytes(buf)


async def _put_body(storage_key: str, body: bytes) -> None:
    await obstore.put_async(storage.get_store(), storage_key, body)
