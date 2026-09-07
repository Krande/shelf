"""Outline / heading-extraction worker (Phase C).

Consumes ``shelf.jobs.attachment.outline`` jobs and produces a new
derivation of the attachment's PDF with PDF bookmarks (``set_toc``)
attached, plus a nested outline_json for the SPA's OutlinePanel.

Engine: a font-heuristic lifted out of the GPU runner. Cluster every
text span by font size, treat the largest-font clusters as heading
candidates, optionally filter for "1.2 …"-style section numbering,
and emit one TOC row per heading. PyMuPDF only — no GPU, no model
weights — so this consumer runs cheaply on the CPU pod alongside
extract / ocr.

Input resolution: the worker prefers the latest ``kind='ocr'``
derivation (so the outline is computed on the post-OCR text layer)
and falls back to ``attachments.storage_key`` when no OCR has run.
The ``parent_storage_key`` column on the new derivation row records
which one was used, so the lineage stays explicit.

Output: a new derived PDF written to a fresh storage key (never
overwrites). The previous inventory remains in storage for
side-by-side comparison — important when iterating on either
algorithm. Reader resolution (latest outline > latest ocr > original)
makes the new row the default view automatically.
"""

from __future__ import annotations

import asyncio
import logging
import re
import statistics
import uuid
from datetime import UTC, datetime
from typing import Any

import obstore

from .. import config
from ..services import storage
from ._db_client import get_db

log = logging.getLogger("shelf.worker.outline")

MIN_TITLE_LEN = 4

# Recognises "1", "1.2", "1.2.3", "A", "A.1", "Annex B", "Appendix C",
# "Chapter 4" — the prefixes that distinguish a real heading from a
# stray large-font sentence. Headings without a section prefix are
# fine too (we fall back to the unfiltered list when no candidates
# match), but when prefixes ARE present the prefix-matching subset
# is strictly higher precision.
_SEC_RE = re.compile(
    r"^\s*("
    r"\d+(?:\.\d+)*\.?"
    r"|[A-Z](?:\.\d+)*\.?"
    r"|(?:Annex|APPENDIX|Appendix|Chapter|CHAPTER)\s+[A-Z0-9]+"
    r")\s+(.+?)$"
)


def _engine_label() -> str:
    """Versioned identifier persisted to ``outline_engine`` so the UI
    can show which build handled this run."""
    return f"pymupdf-fontheuristic shelf/{config.settings.image_tag}"


async def outline_attachment(attachment_id: str | uuid.UUID) -> None:
    """Build an outline for one attachment and write a new derivation."""
    aid = uuid.UUID(str(attachment_id))
    db = get_db()
    att = await db.get_attachment(aid)
    if att is None:
        log.warning("attachment %s not found; skipping outline", aid)
        return
    if att.uploaded_at is None:
        log.warning("attachment %s not uploaded; skipping outline", aid)
        return
    if att.content_type != "application/pdf":
        log.warning(
            "attachment %s is %s; skipping outline",
            aid,
            att.content_type,
        )
        return

    existing_proc = await db.get_processing(aid)
    if existing_proc and existing_proc.outline_status == "cancelled":
        log.info("outline cancelled before pickup for %s; skipping", aid)
        return

    await db.upsert_processing(
        aid,
        {"outline_status": "running", "outline_engine": _engine_label()},
    )

    # Prefer the latest OCR'd version as input — it's the one whose
    # text layer is freshest and whose bookmarks the user will see.
    # Fall back to the attachment's original storage_key when no OCR
    # derivation exists yet.
    parent_key = att.storage_key
    latest_ocr = await db.latest_derivation(aid, "ocr")
    if latest_ocr is not None:
        parent_key = latest_ocr.storage_key

    body = await _fetch_body(parent_key)
    new_body, outline_json = await asyncio.to_thread(_build_outline, body)

    # Cancel-after-running mirror of the OCR worker: drop the result
    # if the user asked for cancel mid-compute. Outline runs are
    # short, but the same shape keeps the cancel UX consistent.
    check = await db.get_processing(aid)
    if check and check.outline_status != "running":
        log.info(
            "outline result discarded for %s (status=%s); cancelled mid-run",
            aid,
            check.outline_status,
        )
        return

    if new_body is None:
        # No headings detected — don't write an empty derivation, just
        # mark done with outline_json=[] so the SPA shows "no outline".
        # The upstream PDF stays the current view.
        await db.upsert_processing(
            aid,
            {
                "outline_status": "done",
                "outline_engine": _engine_label(),
                "outline_completed_at": datetime.now(UTC),
                "outline_json": [],
            },
        )
        log.info("outline complete for %s; no headings detected", aid)
        return

    new_key = storage.derived_key(att.storage_key, "outline")
    await _put_body(new_key, new_body)
    await db.insert_derivation(
        aid,
        kind="outline",
        storage_key=new_key,
        parent_storage_key=parent_key,
        engine=_engine_label(),
    )

    await db.upsert_processing(
        aid,
        {
            "outline_status": "done",
            "outline_engine": _engine_label(),
            "outline_completed_at": datetime.now(UTC),
            "outline_json": outline_json,
        },
    )
    log.info(
        "outline complete for %s; %d top-level entries, parent=%s",
        aid,
        len(outline_json),
        parent_key,
    )


async def mark_failed_terminal(attachment_id: str | uuid.UUID) -> None:
    """Set ``outline_status='failed'`` after JetStream's redelivery
    cap is reached. Best-effort — failures here just log."""
    aid = uuid.UUID(str(attachment_id))
    try:
        await get_db().upsert_processing(
            aid,
            {
                "outline_status": "failed",
                "outline_engine": _engine_label(),
            },
        )
    except Exception:
        log.exception("outline mark_failed_terminal failed for %s", aid)


# ── Engine: PyMuPDF font heuristic ──────────────────────────────────────────


def _build_outline(
    body: bytes,
) -> tuple[bytes | None, list[dict[str, Any]]]:
    """Compute the outline from PDF bytes.

    Returns ``(new_pdf_bytes, nested_outline_json)``. The first element
    is None when no headings were detected — the caller skips writing
    a fresh derivation in that case to avoid an empty re-upload.
    """
    import fitz

    doc = fitz.open("pdf", body)
    try:
        toc = _generate_toc(doc)
        if not toc:
            return None, []
        toc = _normalize_toc_levels(toc)
        try:
            doc.set_toc(toc)
        except Exception as e:
            # Best-effort: a normalised TOC should always be valid, but
            # if PyMuPDF rejects it for any reason we still return the
            # nested JSON for the SPA. The PDF stays bookmark-less.
            log.warning(
                "set_toc rejected outline (%s: %s); saving without it",
                type(e).__name__,
                e,
            )
            return None, _build_nested(toc)
        new_body = doc.tobytes(garbage=4, deflate=True)
        return new_body, _build_nested(toc)
    finally:
        doc.close()


def _extract_spans(doc: Any) -> list[dict[str, Any]]:
    """Pull every text span from every page along with font metadata."""
    import fitz

    out: list[dict[str, Any]] = []
    for idx in range(len(doc)):
        page = doc[idx]
        height = page.rect.height
        blocks = page.get_text(
            "dict", flags=fitz.TEXT_PRESERVE_WHITESPACE
        )["blocks"]
        for block in blocks:
            for line in block.get("lines", []):
                merged = "".join(s["text"] for s in line.get("spans", []))
                if not merged.strip():
                    continue
                first = line["spans"][0]
                out.append(
                    {
                        "text": merged.strip(),
                        "size": round(first["size"], 1),
                        "bold": bool(first["flags"] & 16),
                        "page": idx + 1,
                        "y": (
                            line["bbox"][1] / height if height else 0.0
                        ),
                    }
                )
    return out


def _detect_headings(
    spans: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], int]]:
    """Cluster spans by font size; the largest few clusters are the
    heading levels. Returns ``[(span, level), …]`` with level 1 ==
    largest font (h1)."""
    if not spans:
        return []
    sizes = [s["size"] for s in spans]
    median = statistics.median(sizes)
    candidates = [s for s in spans if s["size"] > median + 0.5]
    if not candidates:
        return []
    distinct = sorted({s["size"] for s in candidates}, reverse=True)
    rank = {sz: idx + 1 for idx, sz in enumerate(distinct[:6])}
    return [(s, rank[s["size"]]) for s in candidates if s["size"] in rank]


def _is_valid_section_num(num: str, _m: re.Match[str]) -> bool:
    """Reject false-positive section numbers (units, dates, …)."""
    bad = {"e.g", "i.e", "vs", "etc"}
    if num.lower().rstrip(".") in bad:
        return False
    if num.replace(".", "").isdigit() and len(num) >= 4:
        return False
    return True


def _filter_short_titles(
    headings: list[tuple[dict[str, Any], int]],
) -> list[tuple[dict[str, Any], int]]:
    return [
        (s, lv) for s, lv in headings if len(s["text"]) >= MIN_TITLE_LEN
    ]


def _generate_toc(doc: Any) -> list[list[Any]]:
    """Return a TOC tree in the shape ``doc.set_toc`` expects:
    ``[[level, title, page], …]`` (PyMuPDF flat-list-with-levels)."""
    spans = _extract_spans(doc)
    raw = _detect_headings(spans)
    raw = _filter_short_titles(raw)

    # Section-number filter: prefer entries that look like "1.2 Foo"
    # over plain large-font sentences when the doc has any.
    numbered: list[tuple[dict[str, Any], int]] = []
    plain: list[tuple[dict[str, Any], int]] = []
    for s, lv in raw:
        m = _SEC_RE.match(s["text"])
        if m and _is_valid_section_num(m.group(1), m):
            numbered.append((s, lv))
        else:
            plain.append((s, lv))
    chosen = numbered or plain

    toc: list[list[Any]] = []
    for s, lv in chosen:
        toc.append([lv, s["text"][:200], s["page"]])
    return toc


def _normalize_toc_levels(toc: list[list[Any]]) -> list[list[Any]]:
    """PyMuPDF's set_toc requires the first row to be level 1 and each
    subsequent row's level to be at most prev+1. _detect_headings picks
    its levels from font-size clusters, so a doc whose chosen headings
    happen to fall into clusters {2, 3, 5} (no level-1 headings, gap
    between 3 and 5) would otherwise hit "bad hierarchy level". Compress
    the level column onto a contiguous 1..N range while preserving the
    relative ordering."""
    if not toc:
        return toc
    out: list[list[Any]] = []
    prev = 0
    for lv, title, page in toc:
        new_lv = max(1, min(lv, prev + 1))
        out.append([new_lv, title, page])
        prev = new_lv
    return out


def _build_nested(toc: list[list[Any]]) -> list[dict[str, Any]]:
    """Stack-based tree assembly from PyMuPDF flat-list-with-levels.
    Output shape is what the SPA's OutlinePanel renders:

        [{"title": "...", "page": N, "items": [...]}, ...]
    """
    if not toc:
        return []
    root: list[dict[str, Any]] = []
    stack: list[tuple[int, list[dict[str, Any]]]] = [(0, root)]
    for lv, title, page in toc:
        while stack and stack[-1][0] >= lv:
            stack.pop()
        if not stack:
            stack = [(0, root)]
        node: dict[str, Any] = {
            "title": title,
            "page": page,
            "items": [],
        }
        stack[-1][1].append(node)
        stack.append((lv, node["items"]))
    return root


# ── I/O helpers ─────────────────────────────────────────────────────────────


async def _fetch_body(storage_key: str) -> bytes:
    res = await obstore.get_async(storage.get_store(), storage_key)
    buf = await res.bytes_async()
    return bytes(buf)


async def _put_body(storage_key: str, body: bytes) -> None:
    await obstore.put_async(storage.get_store(), storage_key, body)
