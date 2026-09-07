"""Pure-function heuristics for "is this PDF's text any good?"

These run inside the extract worker after it pulls per-page text out
of the PDF and they decide whether to flag the attachment for re-OCR
(Phase B) or outline-generation (Phase C). Keeping them dependency-
free + side-effect-free makes them trivial to unit-test and to share
between the worker and any future admin UI.

Thresholds are tuned for English typed prose; multilingual or
formula-heavy docs will skew lower on alpha_ratio without actually
being broken. We err on the side of flagging false positives —
re-OCR is idempotent and an outline is always nice to have.
"""

from __future__ import annotations

from dataclasses import dataclass

# A doc with at least this many pages is heavy enough that lacking a
# table of contents is a real navigation problem. Below this we don't
# bother flagging — the user can scroll.
OUTLINE_MIN_PAGES = 30
# Embedded TOC has fewer than this many entries on a long doc → run
# heading detection.
OUTLINE_MIN_ENTRIES = 3

# More replacement chars (�) than this fraction of the body
# usually means broken CID maps or missing fonts.
OCR_MAX_REPLACEMENT_RATIO = 0.01
# Fraction of characters that look like normal text (letters, digits,
# whitespace, common punctuation). Anything below this is likely
# garbage encoding.
OCR_MIN_ALPHA_RATIO = 0.70
# A typed page is ~1500-3500 chars; below this we treat the page as
# "no usable OCR." Combined with a non-trivial page count the doc is
# almost certainly a scan that needs re-OCR.
OCR_MIN_CHARS_PER_PAGE = 100

# Set of "looks like normal-text" characters. Includes ASCII
# letters/digits, most punctuation, whitespace, and the Latin-1
# extended range so accented Western European text still passes.
_OK_PUNCT = set(" \t\n\r.,;:!?'\"()[]{}-_/\\@#$%&*+=<>|`~^")
# En dash (U+2013) + em dash (U+2014) — common in typeset text.
_OK_PUNCT.add(chr(0x2013))
_OK_PUNCT.add(chr(0x2014))


def _alpha_ratio(text: str) -> float:
    if not text:
        return 0.0
    ok = 0
    for ch in text:
        if ch.isalnum() or ch in _OK_PUNCT or ch.isspace() or 0x00C0 <= ord(ch) <= 0x024F:
            ok += 1
    return ok / len(text)


def _replacement_ratio(text: str) -> float:
    if not text:
        return 0.0
    return text.count("�") / len(text)


@dataclass(frozen=True)
class TextQuality:
    text_chars: int
    page_count: int
    chars_per_page: float
    replacement_char_ratio: float
    alpha_ratio: float
    needs_ocr: bool


def assess_text_quality(text: str, page_count: int) -> TextQuality:
    """Compute the quality metrics + ``needs_ocr`` flag for one PDF.

    ``page_count`` is the parsed PDF's page count, which can be > 0
    even when ``text`` is empty (image-only scans). Pass 0 only for
    non-PDF or unparseable files; the function then short-circuits
    to ``needs_ocr=False`` (we have nothing to assess).
    """
    chars = len(text)
    if page_count <= 0:
        return TextQuality(
            text_chars=chars,
            page_count=0,
            chars_per_page=0.0,
            replacement_char_ratio=0.0,
            alpha_ratio=0.0,
            needs_ocr=False,
        )
    chars_per_page = chars / page_count
    repl = _replacement_ratio(text)
    alpha = _alpha_ratio(text) if text else 0.0
    needs_ocr = (
        repl > OCR_MAX_REPLACEMENT_RATIO
        or chars_per_page < OCR_MIN_CHARS_PER_PAGE
        or (chars > 0 and alpha < OCR_MIN_ALPHA_RATIO)
    )
    return TextQuality(
        text_chars=chars,
        page_count=page_count,
        chars_per_page=chars_per_page,
        replacement_char_ratio=repl,
        alpha_ratio=alpha,
        needs_ocr=needs_ocr,
    )


def assess_outline_need(toc_entry_count: int, page_count: int) -> bool:
    """Embedded outline missing or too thin for a doc this long?"""
    return page_count >= OUTLINE_MIN_PAGES and toc_entry_count < OUTLINE_MIN_ENTRIES


def count_pypdf_outline(outline: object) -> int:
    """Total entries in pypdf's nested-list outline. ``Destination``
    instances count as 1; nested lists recurse."""
    if outline is None:
        return 0
    n = 0
    try:
        # `outline` is typed `object` because pypdf hands back a nested list
        # whose element type it doesn't express. The TypeError guard below is
        # the real check; this silences the static one.
        for entry in outline:  # type: ignore[attr-defined]
            if isinstance(entry, list):
                n += count_pypdf_outline(entry)
            else:
                n += 1
    except TypeError:
        return 0
    return n
