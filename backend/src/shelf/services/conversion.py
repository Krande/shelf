"""Which uploads shelf can render to PDF, and where an attachment's PDF is.

Everything downstream of an upload — text extraction, OCR, the outline
pass, the reader, the bulk PDF download — works on a PDF. A PDF upload
*is* one. Anything this module recognises is rendered to one by the
worker's ``convert`` consumer (``shelf.worker.convert``), which stores
the result as an ``attachment_derivation`` row of kind ``convert`` and
leaves the upload itself untouched at ``attachments.storage_key``: the
download button still hands back the ``.docx`` someone put in.

Two routes, chosen by the file's extension (or its declared type when
the name has none):

* ``office`` — word-processor documents, presentations and
  spreadsheets, sent to Gotenberg's LibreOffice route.
* ``image`` — raster images, wrapped one page per frame by PyMuPDF in
  the worker itself. The result has no text layer, so extraction flags
  it for OCR and the existing pipeline takes it from there.
"""

from __future__ import annotations

import uuid
from pathlib import PurePosixPath
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Attachment, AttachmentDerivation

PDF_CONTENT_TYPE = "application/pdf"

Route = Literal["office", "image"]

# Derivation kind the convert worker writes.
CONVERT_KIND = "convert"

# Extension → (route, canonical content type). The content type is what
# a browser that sends none (or ``application/octet-stream``) should have
# said; it is only used to recognise a file, never to rewrite the row.
_FORMATS: dict[str, tuple[Route, str]] = {
    # Word processing
    ".docx": (
        "office",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    ".doc": ("office", "application/msword"),
    ".odt": ("office", "application/vnd.oasis.opendocument.text"),
    ".rtf": ("office", "application/rtf"),
    # Presentations
    ".pptx": (
        "office",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ),
    ".ppt": ("office", "application/vnd.ms-powerpoint"),
    ".ppsx": (
        "office",
        "application/vnd.openxmlformats-officedocument.presentationml.slideshow",
    ),
    ".pps": ("office", "application/vnd.ms-powerpoint"),
    ".odp": ("office", "application/vnd.oasis.opendocument.presentation"),
    # Spreadsheets
    ".xlsx": (
        "office",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    ".xls": ("office", "application/vnd.ms-excel"),
    ".ods": ("office", "application/vnd.oasis.opendocument.spreadsheet"),
    # Images
    ".png": ("image", "image/png"),
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".tif": ("image", "image/tiff"),
    ".tiff": ("image", "image/tiff"),
    ".bmp": ("image", "image/bmp"),
    ".gif": ("image", "image/gif"),
}

_BY_CONTENT_TYPE: dict[str, Route] = {ct: route for route, ct in _FORMATS.values()}
# Spellings browsers and tools send besides the canonical ones above.
_BY_CONTENT_TYPE.update(
    {
        "text/rtf": "office",
        "application/x-rtf": "office",
        "image/jpg": "image",
        "image/x-ms-bmp": "image",
    }
)

# For the SPA's file picker and the CLI: every extension shelf accepts as
# a document, PDF first.
SUPPORTED_EXTENSIONS: tuple[str, ...] = (".pdf", *_FORMATS)


def extension(filename: str) -> str:
    return PurePosixPath(filename.replace("\\", "/")).suffix.lower()


# Types that say nothing about the file; for these the name decides.
_GENERIC_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream"})


def _bare_type(content_type: str | None) -> str:
    return (content_type or "").split(";")[0].strip().lower()


def is_pdf(content_type: str | None, filename: str = "") -> bool:
    """A PDF upload — by declared type, or by name when the uploader sent
    a generic type (archive imports and CLI uploads may carry
    ``application/octet-stream``). A specific non-PDF type wins over the
    name."""
    ct = _bare_type(content_type)
    if ct == PDF_CONTENT_TYPE:
        return True
    return ct in _GENERIC_TYPES and extension(filename) == ".pdf"


def route_for(filename: str, content_type: str | None) -> Route | None:
    """How to render this upload to PDF, or None if shelf can't (or it is
    already a PDF). The extension wins over the declared type: browsers
    report ``""`` or ``application/octet-stream`` for plenty of office
    formats, but rarely get the name wrong."""
    if is_pdf(content_type, filename):
        return None
    fmt = _FORMATS.get(extension(filename))
    if fmt is not None:
        return fmt[0]
    return _BY_CONTENT_TYPE.get(_bare_type(content_type))


def extension_for_content_type(content_type: str | None) -> str | None:
    """The extension a renderer should be told for a file whose name has
    none — the first one ``_FORMATS`` lists for that type."""
    ct = _bare_type(content_type)
    for ext, (_route, canonical) in _FORMATS.items():
        if canonical == ct:
            return ext
    return None


def is_convertible(att: Attachment) -> bool:
    return route_for(att.filename, att.content_type) is not None


async def latest_convert_keys(
    db: AsyncSession, attachment_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Newest ``convert`` derivation's storage key per attachment, for
    the attachments that have one."""
    if not attachment_ids:
        return {}
    rows = await db.execute(
        select(AttachmentDerivation.attachment_id, AttachmentDerivation.storage_key)
        .where(
            AttachmentDerivation.attachment_id.in_(attachment_ids),
            AttachmentDerivation.kind == CONVERT_KIND,
        )
        .order_by(AttachmentDerivation.created_at.desc())
    )
    out: dict[uuid.UUID, str] = {}
    for aid, key in rows.all():
        out.setdefault(aid, key)
    return out


async def pdf_base_key(db: AsyncSession, att: Attachment) -> str | None:
    """The PDF everything else is derived from: the upload itself for a
    PDF, its newest rendering for a converted file, None when there is
    no PDF (yet, or ever)."""
    if is_pdf(att.content_type, att.filename):
        return att.storage_key
    return (await latest_convert_keys(db, [att.id])).get(att.id)
