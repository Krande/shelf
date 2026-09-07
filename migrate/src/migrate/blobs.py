"""Cross-bucket blob copy: legacy ``zotero`` → shelf ``shelf``.

Two obstore stores constructed from independent credentials. We
``get`` from legacy and ``put`` into shelf — there's no server-side
S3 ``CopyObject`` across buckets with different credentials, so the
bytes round-trip through this process. For the personal-library
scale (649 attachments, mostly PDFs in the low-MB range) that's
fine; if this is ever pointed at a much bigger library we'd switch
to ``open_reader_async``/``open_writer_async`` for streaming.
"""

from __future__ import annotations

from obstore import get_async, head_async, put_async
from obstore.store import S3Store
from shelf.config import settings as shelf_settings

from .config import settings


def legacy_store() -> S3Store:
    return S3Store(
        bucket=settings.legacy_s3_bucket,
        endpoint=settings.legacy_s3_endpoint,
        region=settings.legacy_s3_region,
        access_key_id=settings.legacy_s3_access_key_id,
        secret_access_key=settings.legacy_s3_secret_access_key,
        virtual_hosted_style_request=False,
    )


def shelf_store() -> S3Store:
    return S3Store(
        bucket=shelf_settings.s3_bucket,
        endpoint=shelf_settings.s3_endpoint,
        region=shelf_settings.s3_region,
        access_key_id=shelf_settings.s3_access_key_id,
        secret_access_key=shelf_settings.s3_secret_access_key,
        virtual_hosted_style_request=False,
    )


async def object_exists(store: S3Store, key: str) -> bool:
    try:
        await head_async(store, key)
    except Exception:  # obstore raises on 404 — treat as not-found
        return False
    return True


async def copy_blob(src: S3Store, src_key: str, dst: S3Store, dst_key: str) -> int:
    """Copy a single object. Returns the number of bytes written."""
    result = await get_async(src, src_key)
    body = await result.bytes_async()
    data = bytes(body)
    await put_async(dst, dst_key, data)
    return len(data)
