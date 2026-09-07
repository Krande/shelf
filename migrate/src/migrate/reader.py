"""Read-only access to the legacy Zotero MariaDB.

Each `read_*` returns a list of dicts that's small enough to keep in
RAM at the personal-library scale (~1.3k items, 649 attachments). If
the importer is ever pointed at a multi-tenant instance this becomes
a streaming iterator; for now keeping it list-based makes the
orchestrator easier to read.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aiomysql

from .config import settings


@asynccontextmanager
async def _conn(db: str) -> AsyncIterator[aiomysql.Connection]:
    conn = await aiomysql.connect(
        host=settings.legacy_db_host,
        port=settings.legacy_db_port,
        user=settings.legacy_db_user,
        password=settings.legacy_db_password,
        db=db,
        autocommit=True,
        charset="utf8mb4",
    )
    try:
        yield conn
    finally:
        conn.close()


async def _fetch(db: str, sql: str, *params: Any) -> list[dict[str, Any]]:
    async with _conn(db) as c:
        async with c.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, params)
            return list(await cur.fetchall())


async def resolve_user(user_id: int) -> dict[str, Any]:
    rows = await _fetch(
        "zotero_master",
        "SELECT userID, libraryID, username FROM users WHERE userID=%s",
        user_id,
    )
    if not rows:
        raise RuntimeError(f"legacy user {user_id} not found")
    return rows[0]


async def resolve_library(library_id: int) -> dict[str, Any]:
    rows = await _fetch(
        "zotero_master",
        "SELECT libraryID, libraryType, shardID FROM libraries WHERE libraryID=%s",
        library_id,
    )
    if not rows:
        raise RuntimeError(f"legacy library {library_id} not found")
    return rows[0]


async def read_item_types() -> dict[int, str]:
    rows = await _fetch(
        "zotero_master",
        "SELECT itemTypeID, itemTypeName FROM itemTypes",
    )
    return {r["itemTypeID"]: r["itemTypeName"] for r in rows}


async def read_fields() -> dict[int, str]:
    rows = await _fetch(
        "zotero_master",
        "SELECT fieldID, fieldName FROM fields",
    )
    return {r["fieldID"]: r["fieldName"] for r in rows}


async def read_creator_types() -> dict[int, str]:
    rows = await _fetch(
        "zotero_master",
        "SELECT creatorTypeID, creatorTypeName FROM creatorTypes",
    )
    return {r["creatorTypeID"]: r["creatorTypeName"] for r in rows}


async def read_storage_files() -> dict[int, dict[str, Any]]:
    """``storageFileID`` → ``{hash, filename, size, zip}``. The legacy
    bucket is keyed by ``hash`` (MD5 of the original blob)."""
    rows = await _fetch(
        "zotero_master",
        "SELECT storageFileID, hash, filename, size, zip FROM storageFiles",
    )
    return {r["storageFileID"]: r for r in rows}


def _shard_db(shard_id: int) -> str:
    return f"zotero_shard_{shard_id}"


async def read_items(shard_id: int, library_id: int) -> list[dict[str, Any]]:
    return await _fetch(
        _shard_db(shard_id),
        "SELECT itemID, `key`, itemTypeID, dateAdded, dateModified "
        "FROM items WHERE libraryID=%s",
        library_id,
    )


async def read_item_data(shard_id: int, library_id: int) -> list[dict[str, Any]]:
    return await _fetch(
        _shard_db(shard_id),
        "SELECT d.itemID, d.fieldID, d.value "
        "FROM itemData d JOIN items i ON i.itemID=d.itemID "
        "WHERE i.libraryID=%s",
        library_id,
    )


async def read_item_attachments(shard_id: int, library_id: int) -> list[dict[str, Any]]:
    """Includes the ``storageFileItems`` join so we get
    ``storageFileID``/``mtime``/``size`` alongside the legacy
    metadata. Standalone attachments (``sourceItemID`` IS NULL) are
    returned too — the transform layer materialises a synthetic
    parent item for each."""
    return await _fetch(
        _shard_db(shard_id),
        "SELECT i.itemID, i.`key`, ia.sourceItemID, ia.linkMode, ia.mimeType, "
        "       ia.path, ia.storageHash, sfi.storageFileID, sfi.mtime, sfi.size "
        "FROM itemAttachments ia "
        "JOIN items i ON i.itemID=ia.itemID "
        "LEFT JOIN storageFileItems sfi ON sfi.itemID=ia.itemID "
        "WHERE i.libraryID=%s",
        library_id,
    )


async def read_item_notes(shard_id: int, library_id: int) -> list[dict[str, Any]]:
    return await _fetch(
        _shard_db(shard_id),
        "SELECT n.itemID, i.`key`, n.sourceItemID, n.note, n.title "
        "FROM itemNotes n JOIN items i ON i.itemID=n.itemID "
        "WHERE i.libraryID=%s",
        library_id,
    )


async def read_item_annotations(shard_id: int, library_id: int) -> list[dict[str, Any]]:
    return await _fetch(
        _shard_db(shard_id),
        "SELECT a.itemID, i.`key`, a.parentItemID, a.type, a.text, a.comment, "
        "       a.color, a.pageLabel, a.sortIndex, a.position, a.authorName "
        "FROM itemAnnotations a JOIN items i ON i.itemID=a.itemID "
        "WHERE i.libraryID=%s",
        library_id,
    )


async def read_collections(shard_id: int, library_id: int) -> list[dict[str, Any]]:
    return await _fetch(
        _shard_db(shard_id),
        "SELECT collectionID, `key`, collectionName, parentCollectionID "
        "FROM collections WHERE libraryID=%s",
        library_id,
    )


async def read_collection_items(shard_id: int, library_id: int) -> list[dict[str, Any]]:
    return await _fetch(
        _shard_db(shard_id),
        "SELECT ci.collectionID, ci.itemID "
        "FROM collectionItems ci "
        "JOIN collections c ON c.collectionID=ci.collectionID "
        "WHERE c.libraryID=%s",
        library_id,
    )


async def read_tags(shard_id: int, library_id: int) -> list[dict[str, Any]]:
    return await _fetch(
        _shard_db(shard_id),
        "SELECT tagID, `key`, name, type FROM tags WHERE libraryID=%s",
        library_id,
    )


async def read_item_tags(shard_id: int, library_id: int) -> list[dict[str, Any]]:
    return await _fetch(
        _shard_db(shard_id),
        "SELECT it.itemID, it.tagID FROM itemTags it "
        "JOIN tags t ON t.tagID=it.tagID WHERE t.libraryID=%s",
        library_id,
    )


async def read_creators(shard_id: int, library_id: int) -> list[dict[str, Any]]:
    return await _fetch(
        _shard_db(shard_id),
        "SELECT creatorID, firstName, lastName, fieldMode "
        "FROM creators WHERE libraryID=%s",
        library_id,
    )


async def read_item_creators(shard_id: int, library_id: int) -> list[dict[str, Any]]:
    return await _fetch(
        _shard_db(shard_id),
        "SELECT ic.itemID, ic.creatorID, ic.creatorTypeID, ic.orderIndex "
        "FROM itemCreators ic JOIN items i ON i.itemID=ic.itemID "
        "WHERE i.libraryID=%s",
        library_id,
    )
