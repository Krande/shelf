"""Convert legacy Zotero rows into the shapes Shelf's writer expects.

UUIDv5 is seeded from the Zotero 8-char ``key`` columns, so re-runs of
the importer hit the same target IDs and upsert in place. The
namespace string carries the table name to keep namespaces from
colliding (e.g. an item key and a tag key never produce the same UUID
even on the off-chance the strings happen to collide).
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable
from typing import Any

import warnings

from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

# Some Zotero notes are nothing but a single URL string. BeautifulSoup
# warns "this looks like a URL, did you mean to fetch it?" — irrelevant
# here because we're parsing stored note bodies, not URLs to fetch.
warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

# Top-level namespace for everything this importer creates. Stable
# across runs so re-running the importer is idempotent.
SHELF_IMPORT_NS = uuid.uuid5(uuid.NAMESPACE_URL, "shelf-import:zotero")


def _ns(table: str, key: str) -> uuid.UUID:
    return uuid.uuid5(SHELF_IMPORT_NS, f"{table}:{key}")


def item_uuid(zotero_key: str) -> uuid.UUID:
    return _ns("item", zotero_key)


def attachment_uuid(zotero_key: str) -> uuid.UUID:
    """Attachments are first-class items in Zotero (each has its own
    8-char key); we keep that as the seed so a re-run finds the same
    Shelf attachment row."""
    return _ns("attachment", zotero_key)


def collection_uuid(zotero_key: str) -> uuid.UUID:
    return _ns("collection", zotero_key)


def note_uuid(zotero_key: str) -> uuid.UUID:
    return _ns("note", zotero_key)


def annotation_uuid(zotero_key: str) -> uuid.UUID:
    return _ns("annotation", zotero_key)


# ── Items ───────────────────────────────────────────────────────────────────


def build_items(
    items: list[dict[str, Any]],
    item_data: list[dict[str, Any]],
    item_creators: list[dict[str, Any]],
    creators: list[dict[str, Any]],
    item_types: dict[int, str],
    fields: dict[int, str],
    creator_types: dict[int, str],
) -> list[dict[str, Any]]:
    """One Shelf-shaped item dict per regular Zotero item.

    Attachments and notes are also rows in the legacy ``items`` table
    but they're handled by separate transforms (``build_attachments`` /
    ``build_notes``); we filter them out here by item type name.
    """
    creator_by_id = {c["creatorID"]: c for c in creators}

    data_by_item: dict[int, dict[str, Any]] = {}
    for d in item_data:
        field_name = fields.get(d["fieldID"])
        if not field_name:
            continue
        data_by_item.setdefault(d["itemID"], {})[field_name] = d["value"]

    creators_by_item: dict[int, list[dict[str, Any]]] = {}
    for ic in sorted(item_creators, key=lambda r: (r["itemID"], r["orderIndex"])):
        c = creator_by_id.get(ic["creatorID"])
        if not c:
            continue
        ctype = creator_types.get(ic["creatorTypeID"], "author")
        # ``fieldMode`` 1 in Zotero means single-field name (used for
        # institutions, e.g. "World Health Organization") — we store
        # it as ``name`` rather than first/last to match Zotero's
        # standard creators serialisation.
        if c.get("fieldMode") == 1:
            entry: dict[str, Any] = {
                "creatorType": ctype,
                "name": c.get("lastName") or c.get("firstName") or "",
            }
        else:
            entry = {
                "creatorType": ctype,
                "firstName": c.get("firstName") or "",
                "lastName": c.get("lastName") or "",
            }
        creators_by_item.setdefault(ic["itemID"], []).append(entry)

    out: list[dict[str, Any]] = []
    for it in items:
        type_name = item_types.get(it["itemTypeID"], "document")
        # Skip child rows that the legacy schema also stores in `items`.
        if type_name in {"attachment", "note", "annotation"}:
            continue
        data = dict(data_by_item.get(it["itemID"], {}))
        creators_list = creators_by_item.get(it["itemID"], [])
        if creators_list:
            data["creators"] = creators_list
        out.append(
            {
                "id": item_uuid(it["key"]),
                "legacy_item_id": it["itemID"],
                "legacy_key": it["key"],
                "item_type": type_name,
                "data": data,
                "created_at": it["dateAdded"],
                "updated_at": it["dateModified"],
            }
        )
    return out


# ── Attachments ─────────────────────────────────────────────────────────────


_STORAGE_PREFIX = re.compile(r"^storage:")


def build_attachments(
    attachments: list[dict[str, Any]],
    items_by_legacy_id: dict[int, dict[str, Any]],
    storage_files: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Filter to ``IMPORTED_*`` rows that have a stored blob, and
    resolve the parent item via ``sourceItemID``. Standalone
    attachments (no parent) get a synthetic placeholder item — we
    materialise that in the writer, not here, because it needs a
    Space ID we don't have at transform time."""
    out: list[dict[str, Any]] = []
    for a in attachments:
        link_mode = a.get("linkMode")
        if link_mode not in {"IMPORTED_FILE", "IMPORTED_URL"}:
            continue
        # storageHash on the row, OR storageFileID joined in from
        # storageFileItems, both ultimately resolve to the bucket key.
        legacy_hash: str | None = a.get("storageHash")
        legacy_size: int | None = None
        legacy_filename: str | None = None
        if a.get("storageFileID") is not None:
            sf = storage_files.get(a["storageFileID"])
            if sf:
                legacy_hash = legacy_hash or sf["hash"]
                legacy_size = sf["size"]
                legacy_filename = sf["filename"]
        if legacy_size is None and a.get("size") is not None:
            legacy_size = a["size"]
        if not legacy_hash:
            # No stored blob (e.g. linkMode IMPORTED_URL with the file
            # never synced). Skip — there's nothing to copy.
            continue
        if legacy_filename is None and a.get("path"):
            path_str = a["path"]
            if isinstance(path_str, bytes | bytearray):
                path_str = path_str.decode("utf-8", "replace")
            legacy_filename = _STORAGE_PREFIX.sub("", path_str)

        parent_legacy_id = a.get("sourceItemID")
        out.append(
            {
                "id": attachment_uuid(a["key"]),
                "legacy_item_id": a["itemID"],
                "legacy_key": a["key"],
                "parent_legacy_id": parent_legacy_id,
                "filename": legacy_filename or f"{a['key']}.bin",
                "content_type": a.get("mimeType") or "application/octet-stream",
                "size_bytes": legacy_size,
                "legacy_hash": legacy_hash,
            }
        )
    return out


# ── Notes ───────────────────────────────────────────────────────────────────


def build_notes(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for n in notes:
        html = n.get("note") or ""
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        out.append(
            {
                "id": note_uuid(n["key"]),
                "legacy_item_id": n["itemID"],
                "parent_legacy_id": n.get("sourceItemID"),
                "content_html": html,
                "content_text": text,
            }
        )
    return out


# ── Annotations ─────────────────────────────────────────────────────────────

_SHELF_KINDS = {"highlight": "highlight", "note": "note"}


def build_annotations(annotations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Zotero stores rects as ``[x1,y1,x2,y2]`` (lower-left, upper-
    right) in PDF user space; Shelf wants ``[x,y,w,h]``. Page index is
    0-based in Zotero, 1-based in Shelf."""
    out: list[dict[str, Any]] = []
    for a in annotations:
        kind = _SHELF_KINDS.get(a.get("type") or "")
        if not kind:
            # 'image', 'ink', 'underline', 'text' — Shelf doesn't model
            # these yet. Skip rather than coerce.
            continue
        try:
            position = json.loads(a["position"]) if a.get("position") else {}
        except (TypeError, ValueError):
            position = {}
        page_index = int(position.get("pageIndex", 0))
        rects: list[list[float]] = []
        for r in position.get("rects") or []:
            if len(r) >= 4:
                x1, y1, x2, y2 = r[0], r[1], r[2], r[3]
                rects.append([float(x1), float(y1), float(x2 - x1), float(y2 - y1)])
        if not rects:
            # Note pins in Zotero often live in ``position.rects`` as a
            # single-point box; if the rect list ends up empty fall
            # back to a 1x1 dot at the origin so the row is still
            # well-formed.
            rects = [[0.0, 0.0, 1.0, 1.0]]
        color = a.get("color") or ""
        if color and not color.startswith("#"):
            color = "#" + color
        out.append(
            {
                "id": annotation_uuid(a["key"]),
                "parent_attachment_legacy_id": a["parentItemID"],
                "kind": kind,
                "page_number": page_index + 1,
                "rects": rects,
                "color": color or "#ffd400",
                "text": a.get("text") or None,
            }
        )
    return out


# ── Collections ─────────────────────────────────────────────────────────────


def build_collections(collections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in collections:
        out.append(
            {
                "id": collection_uuid(c["key"]),
                "legacy_id": c["collectionID"],
                "parent_legacy_id": c.get("parentCollectionID"),
                "name": c["collectionName"],
            }
        )
    return out


def build_item_collections(
    rows: Iterable[dict[str, Any]],
) -> list[tuple[int, int]]:
    return [(r["itemID"], r["collectionID"]) for r in rows]


# ── Tags ────────────────────────────────────────────────────────────────────


def build_tags(tags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in tags:
        out.append(
            {
                "legacy_id": t["tagID"],
                "name": t["name"],
            }
        )
    return out


def build_item_tags(rows: Iterable[dict[str, Any]]) -> list[tuple[int, int]]:
    return [(r["itemID"], r["tagID"]) for r in rows]
