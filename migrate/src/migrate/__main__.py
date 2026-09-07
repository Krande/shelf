"""Entry point: ``python -m migrate [--apply]``.

Default is dry-run — reads everything, applies transforms, prints
the row counts that *would* be written, and exits without touching
Postgres or either S3 bucket.

``--apply`` runs the full sequence: ON CONFLICT-based upserts for
every Shelf row, then a streaming blob copy from the legacy ``zotero``
bucket into the ``shelf`` bucket. The whole run is idempotent: re-run
to converge.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import datetime
from typing import Any

from shelf.config import settings as shelf_settings
from shelf.services import queue
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from . import blobs, reader, transform, writer
from .config import settings


def _arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="shelf-migrate",
        description="Import the legacy Zotero library into Shelf. "
        "Default is dry-run; pass --apply to commit.",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Actually write to Shelf Postgres + copy blobs into the shelf bucket. "
        "Without this flag the importer prints what it would do and exits.",
    )
    p.add_argument(
        "--skip-blobs",
        action="store_true",
        help="Run DB upserts but don't copy any blobs (for fast iteration).",
    )
    p.add_argument(
        "--skip-extract-jobs",
        action="store_true",
        help="Don't publish extract jobs at the end (default is to publish).",
    )
    return p


async def _gather_legacy() -> dict[str, Any]:
    """Fetch everything we need from the legacy DB up front."""
    user = await reader.resolve_user(settings.legacy_user_id)
    library = await reader.resolve_library(user["libraryID"])
    shard = library["shardID"]
    library_id = library["libraryID"]

    item_types = await reader.read_item_types()
    fields = await reader.read_fields()
    creator_types = await reader.read_creator_types()
    storage_files = await reader.read_storage_files()

    items = await reader.read_items(shard, library_id)
    item_data = await reader.read_item_data(shard, library_id)
    creators = await reader.read_creators(shard, library_id)
    item_creators = await reader.read_item_creators(shard, library_id)
    attachments = await reader.read_item_attachments(shard, library_id)
    notes = await reader.read_item_notes(shard, library_id)
    annotations = await reader.read_item_annotations(shard, library_id)
    collections = await reader.read_collections(shard, library_id)
    collection_items = await reader.read_collection_items(shard, library_id)
    tags = await reader.read_tags(shard, library_id)
    item_tags = await reader.read_item_tags(shard, library_id)

    return {
        "user": user,
        "library": library,
        "item_types": item_types,
        "fields": fields,
        "creator_types": creator_types,
        "storage_files": storage_files,
        "items": items,
        "item_data": item_data,
        "creators": creators,
        "item_creators": item_creators,
        "attachments": attachments,
        "notes": notes,
        "annotations": annotations,
        "collections": collections,
        "collection_items": collection_items,
        "tags": tags,
        "item_tags": item_tags,
    }


def _print_dry_run_report(
    items_t: list[dict[str, Any]],
    attachments_t: list[dict[str, Any]],
    notes_t: list[dict[str, Any]],
    annotations_t: list[dict[str, Any]],
    collections_t: list[dict[str, Any]],
    tags_t: list[dict[str, Any]],
) -> None:
    print("── DRY RUN — would import:")
    print(f"  items:        {len(items_t)}")
    print(f"  attachments:  {len(attachments_t)}")
    print(f"  notes:        {len(notes_t)}")
    print(f"  annotations:  {len(annotations_t)}")
    print(f"  collections:  {len(collections_t)}")
    print(f"  tags:         {len(tags_t)}")
    if items_t:
        print(f"\nFirst item example: {items_t[0]['data'].get('title', '<no title>')!r}")
    if attachments_t:
        a = attachments_t[0]
        print(
            f"First attachment example: hash={a['legacy_hash']} "
            f"filename={a['filename']!r} parent_legacy_id={a['parent_legacy_id']}"
        )
    print("\nRe-run with --apply to commit.")


async def _run() -> int:
    args = _arg_parser().parse_args()

    print("Reading legacy MariaDB…")
    legacy = await _gather_legacy()

    print(
        f"Found user={legacy['user']['username']!r} "
        f"libraryID={legacy['library']['libraryID']} "
        f"shardID={legacy['library']['shardID']}"
    )

    items_t = transform.build_items(
        legacy["items"],
        legacy["item_data"],
        legacy["item_creators"],
        legacy["creators"],
        legacy["item_types"],
        legacy["fields"],
        legacy["creator_types"],
    )
    items_by_legacy_id = {i["legacy_item_id"]: i for i in items_t}

    attachments_t = transform.build_attachments(
        legacy["attachments"],
        items_by_legacy_id,
        legacy["storage_files"],
    )
    notes_t = transform.build_notes(legacy["notes"])
    annotations_t = transform.build_annotations(legacy["annotations"])
    collections_t = transform.build_collections(legacy["collections"])
    tags_t = transform.build_tags(legacy["tags"])

    if not args.apply:
        _print_dry_run_report(
            items_t, attachments_t, notes_t, annotations_t, collections_t, tags_t
        )
        return 0

    # ── Apply phase ─────────────────────────────────────────────
    engine = create_async_engine(shelf_settings.database_url, echo=False)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    item_legacy_to_uuid: dict[int, uuid.UUID] = writer.reverse_item_uuid_map(items_t)

    async with Session() as session:
        async with session.begin():
            user_id, space_id = await writer.resolve_target_space(
                session,
                target_identity=settings.target_identity,
                target_email=settings.target_email,
            )
            print(f"Target user_id={user_id} space_id={space_id}")

            # Items first (FK target for everything below).
            n = await writer.upsert_items(
                session, items_t, space_id=space_id, created_by=user_id
            )
            print(f"items upserted: {n}")

            # Synthetic parents for orphaned attachments / notes.
            orphan_keys: list[tuple[str, str]] = []
            for a in attachments_t:
                if not a["parent_legacy_id"]:
                    orphan_keys.append((a["legacy_key"], a["filename"]))
            for nt in notes_t:
                if not nt["parent_legacy_id"]:
                    title = (nt["content_text"] or "Imported note")[:80]
                    orphan_keys.append((f"note-{nt['id']}", title))
            synth_map = await writer.materialise_synthetic_parents(
                session,
                space_id=space_id,
                created_by=user_id,
                orphan_legacy_keys_with_titles=orphan_keys,
            )
            for a in attachments_t:
                if not a["parent_legacy_id"]:
                    a["parent_uuid_override"] = synth_map[a["legacy_key"]]
            for nt in notes_t:
                if not nt["parent_legacy_id"]:
                    nt["parent_uuid_override"] = synth_map[f"note-{nt['id']}"]

            # Collections (parents-first) + item↔collection edges.
            collection_legacy_to_uuid = {
                c["legacy_id"]: c["id"] for c in collections_t
            }
            await writer.upsert_collections(
                session,
                collections_t,
                space_id=space_id,
                legacy_id_to_uuid=collection_legacy_to_uuid,
            )
            await writer.upsert_item_collections(
                session,
                transform.build_item_collections(legacy["collection_items"]),
                item_legacy_to_uuid=item_legacy_to_uuid,
                collection_legacy_to_uuid=collection_legacy_to_uuid,
            )

            # Tags + item↔tag edges. Tag IDs come from the post-upsert lookup.
            tag_legacy_to_uuid = await writer.upsert_tags(
                session, tags_t, space_id=space_id
            )
            await writer.upsert_item_tags(
                session,
                transform.build_item_tags(legacy["item_tags"]),
                item_legacy_to_uuid=item_legacy_to_uuid,
                tag_legacy_to_uuid=tag_legacy_to_uuid,
            )

            # Attachments (rows) — blob copy comes after the DB commit so
            # a partial blob run can be retried without losing DB state.
            uploaded_at = datetime.now()
            await writer.upsert_attachments(
                session,
                attachments_t,
                item_legacy_to_uuid=item_legacy_to_uuid,
                created_by=user_id,
                uploaded_at=uploaded_at,
            )

            # Notes.
            await writer.upsert_notes(
                session,
                notes_t,
                item_legacy_to_uuid=item_legacy_to_uuid,
            )

            # Annotations — keyed by parent attachment legacy id.
            attachment_legacy_to_uuid = {
                a["legacy_item_id"]: a["id"] for a in attachments_t
            }
            await writer.upsert_annotations(
                session,
                annotations_t,
                attachment_legacy_to_uuid=attachment_legacy_to_uuid,
                created_by=user_id,
            )

    # ── Blob copy ──────────────────────────────────────────────
    if args.skip_blobs:
        print("Skipping blob copy (--skip-blobs).")
    else:
        print(f"Copying {len(attachments_t)} blobs zotero/* → shelf/items/.../*")
        src = blobs.legacy_store()
        dst = blobs.shelf_store()
        copied = 0
        skipped = 0
        failed = 0
        total_bytes = 0
        for a in attachments_t:
            item_uuid = a.get("parent_uuid_override") or item_legacy_to_uuid.get(
                a["parent_legacy_id"]
            )
            if not item_uuid:
                skipped += 1
                continue
            dst_key = writer.attachment_storage_key(item_uuid, a["id"])
            if await blobs.object_exists(dst, dst_key):
                skipped += 1
                continue
            try:
                n_bytes = await blobs.copy_blob(src, a["legacy_hash"], dst, dst_key)
                copied += 1
                total_bytes += n_bytes
            except Exception as e:
                failed += 1
                print(f"  blob copy failed for {a['legacy_key']}: {e}", file=sys.stderr)
        mb = total_bytes / (1024 * 1024)
        print(f"blobs: {copied} copied, {skipped} skipped, {failed} failed, {mb:.1f} MB total")

    # Publish an extract job per imported attachment so the worker
    # populates body text + per-page dims for the reader. The same
    # NATS publisher the API uses on upload-complete; if SHELF_NATS_URL
    # isn't set the calls are no-ops and the rows stay pending until a
    # backfill picks them up.
    if args.skip_extract_jobs:
        print("Skipping extract-job publish (--skip-extract-jobs).")
    else:
        published = 0
        for a in attachments_t:
            ok = await queue.publish_extract(a["id"])
            if ok:
                published += 1
        print(f"extract jobs published: {published} / {len(attachments_t)}")

    print("Done.")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
