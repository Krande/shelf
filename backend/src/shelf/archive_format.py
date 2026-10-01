"""The ``index.json`` of a Shelf archive: format ``shelf.archive``.

An archive is the ZIP that "Download PDFs" produces -- the PDFs, laid
out one folder per collection, plus this index, which is what makes the
archive re-importable: it carries each document's metadata, tags and
collection memberships, and the collection tree itself. The PDFs are the
payload; the index is the authority on what they are. A reader looks
files up by the ``path`` the index gives, never by guessing from folder
or file names.

These models are the specification. ``docs/archive-format.md`` explains
them and ``docs/schemas/shelf-archive-1.schema.json`` is generated from
them (a test keeps the two in step).

Versioning, so archives written today stay importable and archives
written by a newer Shelf fail clearly rather than half-import:

- ``version`` is ``"MAJOR.MINOR"``.
- Within a major version, a new minor may only *add* optional fields.
  Nothing is removed, renamed, retyped or given a new meaning.
- A reader rejects a major version it doesn't know, accepts any minor of
  one it does, and ignores fields it doesn't recognise, at every level.
- Anything a minor bump can't express is a new major version.

Every model therefore ignores unknown fields, and every field added
after 1.0 must have a default.
"""

import re
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

FORMAT: Literal["shelf.archive"] = "shelf.archive"
# What this Shelf writes.
VERSION = "1.0"
# The major versions this Shelf can read.
SUPPORTED_MAJORS = frozenset({1})

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)$")


class _Model(BaseModel):
    # Forward compatibility: a field added by a later minor version is
    # ignored, not rejected.
    model_config = ConfigDict(extra="ignore")


class ArchiveSpace(_Model):
    id: uuid.UUID
    slug: str
    name: str


class ArchiveSource(_Model):
    """Where the archive was made. Informational; an import never
    depends on it."""

    space: ArchiveSpace | None = None


class ArchiveCollection(_Model):
    """One collection of the exported tree.

    ``id`` and ``parent_id`` are the source's ids and only mean anything
    inside this archive: they tie items to collections and collections
    to each other. ``parent_id`` is null for a top-level collection of
    the archive -- the exported collection, or each of a space's
    top-level ones -- and otherwise names another collection in the list.
    """

    id: uuid.UUID
    parent_id: uuid.UUID | None = None
    name: str = Field(min_length=1)
    description: str | None = None
    # The ZIP folder holding the PDFs filed directly here, one segment
    # per level: "" for an exported collection (it is the archive root),
    # its name for a space's top-level collection. Two sibling
    # collections with the same name share a folder.
    folder: str = ""


class ArchiveTag(_Model):
    name: str = Field(min_length=1)
    color: str | None = None


class ArchiveFile(_Model):
    """One PDF in the ZIP."""

    # Where the bytes are in the ZIP, "/"-separated. Unique per archive.
    path: str = Field(min_length=1)
    # The attachment's own filename, which ``path`` may have had to
    # sanitise or disambiguate.
    filename: str = Field(min_length=1)
    content_type: str = "application/pdf"
    # Of the bytes in the ZIP. Shelf always writes both; an importer
    # uses them to recognise a file it already has.
    size: int | None = Field(default=None, ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    # Which version of the attachment these bytes are: "original", or
    # the derivation kind ("ocr", "outline") that superseded it.
    # Informational; an import stores the bytes as a new original.
    version: str = "original"


class ArchiveItem(_Model):
    """One document: its metadata, and the files that belong to it."""

    id: uuid.UUID
    # The document this one was first imported from, carried along so a
    # chain of exports and imports can still recognise it. Null when it
    # was created where it was exported from.
    origin_id: uuid.UUID | None = None
    item_type: str = Field(min_length=1)
    # Shelf's item metadata as stored: Zotero-style keys (``title``,
    # ``creators``, ``date``, ...) plus the per-type fields. Opaque to
    # the format; an import stores it unchanged.
    data: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    # Every collection of this archive the document is filed in. Its
    # PDFs appear once in the ZIP, under the shallowest of them.
    collection_ids: list[uuid.UUID] = Field(default_factory=list)
    files: list[ArchiveFile] = Field(default_factory=list)


class ArchiveMissing(_Model):
    """A PDF that belonged in the archive but couldn't be read from
    storage when it was made."""

    item_id: uuid.UUID
    filename: str


class ArchiveIndex(_Model):
    model_config = ConfigDict(
        extra="ignore",
        title="Shelf archive index (shelf.archive 1.x)",
        json_schema_extra={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": (
                "https://github.com/Krande/shelf/blob/main/docs/schemas/"
                "shelf-archive-1.schema.json"
            ),
        },
    )

    format: Literal["shelf.archive"]
    version: str = Field(pattern=_VERSION_RE.pattern)
    created_at: datetime
    # The software that wrote the archive, e.g. "shelf 0.17.0".
    generator: str | None = None
    source: ArchiveSource | None = None
    # What was exported: "collection" (one collection and everything
    # below it), "space" (every collection of a space, plus documents
    # filed in none), or "items" (a selection of documents, no tree).
    # Informational; the collections and memberships say the rest.
    scope: str = "items"
    # The exported collection, for scope "collection"; otherwise null.
    root_collection_id: uuid.UUID | None = None
    collections: list[ArchiveCollection] = Field(default_factory=list)
    tags: list[ArchiveTag] = Field(default_factory=list)
    items: list[ArchiveItem] = Field(default_factory=list)
    missing: list[ArchiveMissing] = Field(default_factory=list)


class ArchiveFormatError(ValueError):
    """The index can't be imported: not one, a version this Shelf can't
    read, or internally inconsistent. The message is for the user."""


def parse_index(raw: Any) -> ArchiveIndex:
    """Validate a decoded ``index.json`` into an ``ArchiveIndex``.

    Also reads the unversioned list that Shelf 0.16.2 wrote, which named
    each PDF's path, title, item and folder and nothing more.
    """
    if isinstance(raw, list):
        return _from_legacy(raw)
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        raise ArchiveFormatError("Not a Shelf archive index")
    version = raw.get("version")
    match = _VERSION_RE.match(version) if isinstance(version, str) else None
    if match is None:
        raise ArchiveFormatError(f"Unreadable archive version {version!r}")
    major = int(match.group(1))
    if major not in SUPPORTED_MAJORS:
        supported = ", ".join(f"{m}.x" for m in sorted(SUPPORTED_MAJORS))
        raise ArchiveFormatError(
            f"Archive format {version} can't be read by this Shelf "
            f"(it reads {supported}); upgrade Shelf to import it"
        )
    try:
        index = ArchiveIndex.model_validate(raw)
    except ValidationError as e:
        raise ArchiveFormatError(f"Invalid archive index: {e}") from e
    _check_references(index)
    return index


def _check_references(index: ArchiveIndex) -> None:
    """The cross-references a schema can't express."""
    colls = {c.id: c for c in index.collections}
    if len(colls) != len(index.collections):
        raise ArchiveFormatError("Duplicate collection id in archive")
    for c in index.collections:
        if c.parent_id is not None and c.parent_id not in colls:
            raise ArchiveFormatError(
                f"Collection {c.name!r} names a parent not in the archive"
            )
    # Every chain must end at a root; a cycle never does.
    for c in index.collections:
        seen: set[uuid.UUID] = set()
        cur: ArchiveCollection | None = c
        while cur is not None and cur.parent_id is not None:
            if cur.id in seen:
                raise ArchiveFormatError("Collection tree has a cycle")
            seen.add(cur.id)
            cur = colls[cur.parent_id]
    if (
        index.root_collection_id is not None
        and index.root_collection_id not in colls
    ):
        raise ArchiveFormatError("Root collection is not in the archive")
    ids: set[uuid.UUID] = set()
    paths: set[str] = set()
    for it in index.items:
        if it.id in ids:
            raise ArchiveFormatError(f"Duplicate item id {it.id} in archive")
        ids.add(it.id)
        for cid in it.collection_ids:
            if cid not in colls:
                raise ArchiveFormatError(
                    f"Item {it.id} is filed in a collection not in the archive"
                )
        for f in it.files:
            if f.path in paths:
                raise ArchiveFormatError(f"Two files share the path {f.path!r}")
            paths.add(f.path)


# Stable ids for the folders of a legacy archive, so importing the same
# one twice derives the same tree.
_LEGACY_NS = uuid.UUID("6f1d3c2e-0b7a-4c41-9a43-2f0e5b8d7a10")


def _from_legacy(raw: list[Any]) -> ArchiveIndex:
    """Shelf 0.16.2's index: ``[{path, title, item_id, collection?}]``.

    Each folder becomes a collection. The exported collection's own name
    wasn't recorded, so its documents and subfolders land directly in
    whatever the import targets.
    """
    try:
        entries = [_LegacyEntry.model_validate(e) for e in raw]
    except ValidationError as e:
        raise ArchiveFormatError(f"Invalid archive index: {e}") from e

    collections: dict[str, ArchiveCollection] = {}

    def folder_id(folder: str) -> uuid.UUID | None:
        if not folder:
            return None
        if folder not in collections:
            parent, _, name = folder.rpartition("/")
            collections[folder] = ArchiveCollection(
                id=uuid.uuid5(_LEGACY_NS, folder),
                parent_id=folder_id(parent),
                name=name,
                folder=folder,
            )
        return collections[folder].id

    items: dict[uuid.UUID, ArchiveItem] = {}
    for entry in entries:
        cid = folder_id(entry.collection or "")
        it = items.setdefault(
            entry.item_id,
            ArchiveItem(
                id=entry.item_id,
                item_type="document",
                data={"title": entry.title} if entry.title else {},
            ),
        )
        if cid is not None and cid not in it.collection_ids:
            it.collection_ids.append(cid)
        it.files.append(
            ArchiveFile(path=entry.path, filename=entry.path.rpartition("/")[2])
        )
    index = ArchiveIndex(
        format=FORMAT,
        version="0.0",
        # Not recorded by that version.
        created_at=datetime(1970, 1, 1, tzinfo=UTC),
        collections=list(collections.values()),
        items=list(items.values()),
    )
    _check_references(index)
    return index


class _LegacyEntry(_Model):
    path: str = Field(min_length=1)
    title: str = ""
    item_id: uuid.UUID
    collection: str | None = None
