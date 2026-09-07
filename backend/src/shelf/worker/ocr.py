"""Re-OCR worker (Phase B).

Consumes ``shelf.jobs.attachment.ocr`` jobs that the extract worker
publishes when its quality heuristics flag a PDF for re-OCR.

Pipeline:

    fetch original PDF → ocrmypdf --redo-ocr (Tesseract) → write back
    over the same storage key → publish a fresh extract job so the
    body-text + per-page rows refresh against the new searchable PDF.

Why ``--redo-ocr`` rather than ``--force-ocr``: redo-ocr strips any
existing invisible-text layer and OCRs the rasterised pages, which is
exactly the "we have garbage OCR, replace it" path. force-ocr also
re-rasterises every page (loses the original vector quality on born-
digital PDFs) and is overkill for our trigger.

The output PDF is overwritten in place. The presigned-download URL
clients hold remains valid because we keep the same storage key —
only the bytes change.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from datetime import UTC, datetime

import obstore

from .. import config
from ..services import queue, storage
from ._db_client import get_db

log = logging.getLogger("shelf.worker.ocr")


OCR_LANGUAGE = os.environ.get("SHELF_OCR_LANGUAGE", "eng")
OCR_TIMEOUT_SECONDS = int(os.environ.get("SHELF_OCR_TIMEOUT_SECONDS", "1800"))
# Cap on the number of Tesseract subprocesses ocrmypdf spawns. The
# library's default is os.cpu_count(), which on a k8s worker reads
# the *host's* CPU count rather than the container's CPU quota — so
# on a many-core node the worker happily spawns 16+ tesseracts at
# ~300 MB each and OOMKills itself. Pin to 2 by default: enough for
# real parallelism on a 2-vCPU pod, low enough that 2 GB of RAM
# carries even a heavy real-world doc. Override via the env var if
# the pod is sized for more.
OCR_JOBS = int(os.environ.get("SHELF_OCR_JOBS", "2"))

# Path to the ocrmypdf CLI. Resolved at import so we fail fast if
# the container is mis-built (pip install puts it on PATH; if it
# isn't there, every OCR job would fail with the same error).
OCRMYPDF_BIN = shutil.which("ocrmypdf") or "ocrmypdf"

# How often the cancel-poll loop checks the DB for a cancel signal
# while the subprocess is running. 2s is short enough to feel
# responsive, long enough that a 10-minute OCR run only adds ~300
# extra DB reads.
CANCEL_POLL_INTERVAL = 2.0
# Window between SIGTERM and SIGKILL when cancelling. ocrmypdf's
# orchestrator usually exits within a second of SIGTERM; the longer
# tail handles the case where it's mid-tesseract-spawn.
CANCEL_GRACE_SECONDS = 5.0

# In-flight OCR subprocesses by attachment id. The cancel-poll task
# uses this to look up the right Process handle to terminate. Keyed
# per-pod (in-memory only) — that's fine because OCR_FETCH_BATCH=1
# means each pod runs at most one OCR job at a time, and the cancel
# signal flows through the DB which any pod can read.
_RUNNING_PROCS: dict[uuid.UUID, asyncio.subprocess.Process] = {}

# Minimum interval between DB writes from the progress reader.
# Big PDFs emit a per-page log line every fraction of a second; we
# don't want a row update on each. 2s lines up the progress refresh
# rate with the cancel-poll cadence so the UI sees both at once.
PROGRESS_DB_INTERVAL = 2.0

# ocrmypdf's default-verbosity stderr emits lines like:
#     1 redoing OCR
#     2 redoing OCR
#   123 redoing OCR
# right when each page starts processing. We track the largest page
# number we've seen as the "current page" — pages aren't necessarily
# completed in order (concurrent workers) but the highest-seen
# matches what a human-shaped progress bar wants to display.
_PAGE_LINE_RE = re.compile(r"^\s+(\d+)\s+\S")
# Total page count is announced once before processing starts.
_TOTAL_LINE_RE = re.compile(r"^Parsing\s+(\d+)\s+pages\s")


def _engine_label() -> str:
    """Versioned identifier persisted to ``ocr_engine`` so the UI can
    show "this run was handled by sha-XXXXX with ocrmypdf 17.4.2"."""
    try:
        import ocrmypdf

        ver = getattr(ocrmypdf, "__version__", "?")
    except Exception:
        ver = "?"
    return f"ocrmypdf/{ver} shelf/{config.settings.image_tag}"


async def ocr_attachment(attachment_id: str | uuid.UUID) -> None:
    """Run OCRmyPDF against one attachment and republish extraction.

    Caller is the worker loop; exceptions propagate so the loop can
    decide between nak-with-retry and ack-with-permanent-fail.
    """
    aid = uuid.UUID(str(attachment_id))
    db = get_db()
    att = await db.get_attachment(aid)
    if att is None:
        log.warning("attachment %s not found; skipping ocr", aid)
        return
    if att.uploaded_at is None:
        log.warning("attachment %s not uploaded; skipping ocr", aid)
        return
    if att.content_type != "application/pdf":
        log.warning(
            "attachment %s is %s; skipping ocr",
            aid,
            att.content_type,
        )
        return

    # Honour an in-flight cancel request: if the user hit Cancel
    # between the message being published and the worker picking
    # it up, ocr_status will be 'cancelled' here. Short-circuit
    # before doing any heavy work.
    existing_proc = await db.get_processing(aid)
    if existing_proc and existing_proc.ocr_status == "cancelled":
        log.info("ocr cancelled before pickup for %s; skipping", aid)
        return

    # Mark running so the admin UI can show progress.
    await db.upsert_processing(
        aid,
        {"ocr_status": "running", "ocr_engine": _engine_label()},
    )
    # OCR always runs against the attachment's original PDF, never a
    # previous OCR pass — re-running is for producing a fresh
    # comparison candidate, not chaining OCRs.
    parent_key = att.storage_key

    body = await _fetch_body(parent_key)

    # Run ocrmypdf as a child process so a cancel can SIGTERM it
    # mid-flight. _run_ocrmypdf_async tracks the Process handle in
    # _RUNNING_PROCS and runs a sibling poll task that watches the
    # DB for ocr_status='cancelled' — when seen, it terminates the
    # subprocess. Returns None if the run was cancelled (no bytes
    # to write), otherwise the OCR'd body.
    new_body = await _run_ocrmypdf_async(aid, body)
    if new_body is None:
        log.info("ocr cancelled mid-run for %s; result discarded", aid)
        return

    # Belt-and-braces re-check: even if the subprocess wasn't
    # cancelled (it might have finished before the poll task fired),
    # honour a cancel that arrived in the window between the
    # subprocess returning and us writing storage.
    check = await db.get_processing(aid)
    if check and check.ocr_status != "running":
        log.info(
            "ocr result discarded for %s (status=%s); cancelled late",
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
    # Clear progress columns on terminal state so the SPA doesn't
    # keep showing the last-seen page count after the run finishes.
    await db.upsert_processing(
        aid,
        {
            "ocr_status": "done",
            "ocr_engine": _engine_label(),
            "ocr_completed_at": now,
        },
        clear=["progress_done", "progress_total"],
    )

    # Chain into outline + extract. Outline runs as its own job so
    # an outline glitch can't discard the OCR work. Extract refreshes
    # text_content + per-page rows against the new OCR'd PDF.
    await db.upsert_processing(aid, {"outline_status": "queued"})
    await queue.publish_outline(aid)
    await queue.publish_extract(aid)
    log.info(
        "ocr complete for %s; outline + re-extract enqueued", aid
    )


async def mark_failed_terminal(attachment_id: str | uuid.UUID) -> None:
    """Set ``ocr_status='failed'`` after JetStream's redelivery cap
    is reached. Best-effort — failures here just log."""
    aid = uuid.UUID(str(attachment_id))
    try:
        await get_db().upsert_processing(
            aid,
            {"ocr_status": "failed", "ocr_engine": _engine_label()},
            clear=["progress_done", "progress_total"],
        )
    except Exception:
        log.exception("ocr mark_failed_terminal failed for %s", aid)


async def _run_ocrmypdf_async(
    aid: uuid.UUID, body: bytes
) -> bytes | None:
    """Run the ocrmypdf CLI as a child process so we can SIGTERM it
    on cancel. Returns the OCR'd PDF bytes, or None if the run was
    cancelled mid-flight (caller discards and bails).

    Why a subprocess and not the in-process Python API: the Python
    API runs synchronously inside our event loop's thread pool and
    can't be interrupted — Tesseract is C code. A child process can
    be killed by PID. ocrmypdf's CLI surfaces every option we need;
    we lose no functionality.
    """
    with (
        tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as inp,
        tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as out,
    ):
        inp_path = inp.name
        out_path = out.name
        inp.write(body)

    cmd = [
        OCRMYPDF_BIN,
        "--redo-ocr",
        "-l",
        OCR_LANGUAGE,
        "--output-type",
        "pdf",
        "--tesseract-timeout",
        str(OCR_TIMEOUT_SECONDS),
        "--jobs",
        str(OCR_JOBS),
        inp_path,
        out_path,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _RUNNING_PROCS[aid] = proc
        cancel_task = asyncio.create_task(_cancel_poll(aid))
        progress_task = asyncio.create_task(
            _read_stderr_with_progress(aid, proc)
        )
        try:
            rc = await proc.wait()
        finally:
            cancel_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await cancel_task
            # Don't cancel the progress reader — let it drain whatever
            # stderr the subprocess flushed before exiting so we still
            # have an error tail on non-zero exit.
            stderr_tail = ""
            with contextlib.suppress(asyncio.CancelledError, Exception):
                stderr_tail = await progress_task
            _RUNNING_PROCS.pop(aid, None)

        # SIGTERM on Linux gives the child exit code -15; on a
        # graceful cancel we treat that as "cancelled, no result"
        # rather than a failure.
        if rc is not None and rc < 0:
            return None
        if rc != 0:
            # ocrmypdf maps these to specific exit codes:
            #   2  bad input file (encrypted, malformed …)
            #   6  digital signature would be invalidated
            # Both are "permanent for this input" — surface as
            # RuntimeError so the worker's terminal-fail branch
            # short-circuits the redelivery loop.
            raise RuntimeError(
                f"ocrmypdf exit={rc}: {stderr_tail.strip()[:2000]}"
            )

        # Reading via to_thread keeps the blocking open() off the
        # event loop. The output PDF can be hundreds of MB after
        # OCR; sync reads here would stall fetches on the other
        # consumer.
        return await asyncio.to_thread(_read_file_sync, out_path)
    finally:
        for p in (inp_path, out_path):
            try:
                os.unlink(p)
            except OSError:
                pass


def _read_file_sync(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


async def _read_stderr_with_progress(
    aid: uuid.UUID, proc: asyncio.subprocess.Process
) -> str:
    """Stream-read ocrmypdf's stderr, persist live progress to the
    DB, and return the last few KB for error reporting on non-zero
    exit.

    Two reasons we don't use ``proc.communicate()`` here: (1) we
    want progress as it happens, not in a final lump; (2) we have
    to actively drain stderr or the OS pipe buffer fills up and
    blocks ocrmypdf on its writes — silent deadlock.
    """
    if proc.stderr is None:
        return ""
    pages_seen = 0
    total: int | None = None
    last_db_write = 0.0
    tail: list[str] = []
    try:
        async for raw in proc.stderr:
            line = raw.decode("utf-8", errors="replace").rstrip()
            tail.append(line)
            if len(tail) > 200:
                del tail[:50]

            # Total page count comes through once near the end of
            # the rasterise stage. If we know it, the SPA shows a
            # bounded "23 / 198" instead of "23".
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
        # Final flush so the row reflects the last page.
        if pages_seen > 0 or total is not None:
            await _write_progress(aid, pages_seen, total)
    except Exception:
        log.exception("progress reader for %s failed", aid)
    return "\n".join(tail[-50:])


async def _write_progress(
    aid: uuid.UUID, done: int, total: int | None
) -> None:
    """Best-effort UPSERT of progress fields. Failures don't affect
    the OCR run — progress is a UI nicety, not a correctness
    signal."""
    try:
        fields: dict[str, object] = {"progress_done": done}
        if total is not None:
            fields["progress_total"] = total
        await get_db().upsert_processing(aid, fields)
    except Exception:
        log.exception("write_progress failed for %s", aid)


async def _cancel_poll(aid: uuid.UUID) -> None:
    """Watch ``attachment_processing.ocr_status`` while a subprocess
    is running and SIGTERM it the moment a cancel signal lands.

    The poll lives as long as the subprocess is in _RUNNING_PROCS;
    the caller cancels this task once the subprocess exits. We
    re-poll periodically rather than e.g. listening on a NATS
    subject because the DB is the durable signal — the cancel API
    writes the row in the API pod's connection, the worker (which
    might be a different pod) reads it from any session."""
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
                "cancel signal seen for %s; SIGTERM ocrmypdf pid=%s",
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
                    "ocrmypdf pid=%s still alive after SIGTERM grace; "
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


async def _fetch_body(storage_key: str) -> bytes:
    res = await obstore.get_async(storage.get_store(), storage_key)
    buf = await res.bytes_async()
    return bytes(buf)


async def _put_body(storage_key: str, body: bytes) -> None:
    await obstore.put_async(storage.get_store(), storage_key, body)
