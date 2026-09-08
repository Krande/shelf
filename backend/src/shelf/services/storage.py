"""Object-storage service.

Thin wrapper over obstore so the rest of the app talks to one interface
regardless of whether the backend is S3-compatible (Garage, AWS, MinIO),
Azure Blob, GCS, or a local filesystem. The backend is chosen by config.
"""

import secrets
from datetime import timedelta

import httpx
from obstore import copy_async, delete_async, head_async, sign_async
from obstore.store import (
    AzureStore,
    GCSStore,
    LocalStore,
    MemoryStore,
    ObjectStore,
    S3Store,
)

from ..config import settings

DEFAULT_PRESIGN_TTL = timedelta(minutes=15)


def _build_store() -> ObjectStore:
    """Construct the configured object store.

    For now only S3-compatible stores are wired (Garage in dev/prod). The
    function is structured so AzureStore / GCSStore / LocalStore / MemoryStore
    can be selected by config later without changing call sites.
    """
    return S3Store(
        bucket=settings.s3_bucket,
        endpoint=settings.s3_endpoint,
        region=settings.s3_region,
        access_key_id=settings.s3_access_key_id,
        secret_access_key=settings.s3_secret_access_key,
        virtual_hosted_style_request=False,
        # obstore's own HTTP client refuses a plaintext endpoint unless it's
        # opted into, failing with reqwest's "BadScheme" before a request is
        # sent. Presigned URLs hide this — those are handed to the browser (or
        # to httpx in read_object) and work regardless — so what breaks is
        # exactly the server-side calls: head, delete and copy. That made
        # object_exists() report False for objects that exist, and
        # ensure_original() silently skip the OCR snapshot.
        #
        # Scoped to http:// endpoints so a misconfigured https:// deployment
        # still fails loudly rather than quietly downgrading.
        client_options={"allow_http": settings.s3_endpoint.startswith("http://")},
    )


_store: ObjectStore | None = None


def get_store() -> ObjectStore:
    global _store
    if _store is None:
        _store = _build_store()
    return _store


def reset_store() -> None:
    """Force re-construction of the store (used by tests)."""
    global _store
    _store = None


SigningStore = S3Store | AzureStore | GCSStore


def _signing_store() -> SigningStore:
    store = get_store()
    if not isinstance(store, S3Store | AzureStore | GCSStore):
        raise RuntimeError(
            f"presign requires a cloud-backed store; got {type(store).__name__}"
        )
    return store


async def presign_upload(
    key: str, expires_in: timedelta = DEFAULT_PRESIGN_TTL
) -> str:
    return await sign_async(_signing_store(), "PUT", key, expires_in)


async def presign_download(
    key: str, expires_in: timedelta = DEFAULT_PRESIGN_TTL
) -> str:
    return await sign_async(_signing_store(), "GET", key, expires_in)


async def head_object(key: str) -> dict[str, object]:
    meta = await head_async(get_store(), key)
    return dict(meta)


async def read_object(key: str) -> bytes:
    """Download an object's full body. Used by bulk-export ZIP
    assembly; the presign + httpx round-trip is the lowest-common-
    denominator path that works for every backend obstore supports.
    """
    url = await presign_download(key)
    async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.content


async def delete_object(key: str) -> None:
    await delete_async(get_store(), key)


# ── Original-blob preservation ───────────────────────────────────────────────
# Each mutable attachment blob lives at ``storage_key``. The first time we're
# about to mutate it (OCR rewrite, future re-renders) we copy the live bytes
# to ``original_key(storage_key)`` and never touch that copy again. The SPA
# can then issue a restore that copies the .original blob back over the live
# one if a worker pass produced something worse than the input.

ORIGINAL_SUFFIX = ".original"


def original_key(storage_key: str) -> str:
    return storage_key + ORIGINAL_SUFFIX


def derived_key(storage_key: str, kind: str) -> str:
    """Generate a unique storage key for a derivation of ``storage_key``.

    Layout: ``<storage_key>/derived/<kind>-<8 hex>.pdf`` — the original
    key acts as a "directory" so all derivations live next to (and are
    cleaned up with) their source attachment. The 8-hex random suffix
    is enough that re-running the same kind on the same source never
    collides; the table's UNIQUE constraint on storage_key is the
    backstop.
    """
    suffix = secrets.token_hex(4)
    return f"{storage_key}/derived/{kind}-{suffix}.pdf"


async def object_exists(key: str) -> bool:
    """HEAD the key; return True iff present.

    obstore raises on 404 for any backend; we don't import the
    backend-specific exception class because it varies (S3
    NotFoundError, GCS, Azure …) so a broad catch is the
    pragmatic shape.
    """
    try:
        await head_async(get_store(), key)
        return True
    except Exception:
        return False


async def copy_object(src_key: str, dst_key: str) -> None:
    """Server-side object copy when the backend supports it (S3
    CopyObject, GCS rewrite, Azure copy-blob). For local /
    in-memory stores obstore falls back to a fetch+put."""
    await copy_async(get_store(), src_key, dst_key)


async def ensure_original(storage_key: str) -> bool:
    """Create the ``.original`` sibling once, idempotently.

    Returns True if the copy was made on this call, False otherwise
    — including the (common) case where the sibling already exists,
    the source object isn't there yet (upload not finalised), or
    the backend is unreachable. We keep this best-effort because
    the snapshot is a fallback feature; a copy failure shouldn't
    block whatever upload / OCR flow it's running inside of.
    """
    dst = original_key(storage_key)
    if await object_exists(dst):
        return False
    if not await object_exists(storage_key):
        # Source missing — nothing to snapshot. Common in tests that
        # finalise the row before putting bytes; also possible if a
        # client crashed mid-upload. Either way: silent no-op.
        return False
    try:
        await copy_object(storage_key, dst)
    except Exception:
        return False
    return True


async def restore_from_original(storage_key: str) -> bool:
    """Copy the preserved original back over the live blob.

    Returns False if no original exists (caller should surface that
    to the user — there's nothing to restore). The live blob is
    overwritten via a fresh server-side copy, keeping the same
    storage_key so existing presigned URLs stay valid.
    """
    src = original_key(storage_key)
    if not await object_exists(src):
        return False
    await copy_object(src, storage_key)
    return True


__all__ = [
    "ORIGINAL_SUFFIX",
    "AzureStore",
    "GCSStore",
    "LocalStore",
    "MemoryStore",
    "ObjectStore",
    "S3Store",
    "copy_object",
    "delete_object",
    "derived_key",
    "ensure_original",
    "get_store",
    "head_object",
    "object_exists",
    "original_key",
    "presign_download",
    "presign_upload",
    "read_object",
    "reset_store",
    "restore_from_original",
]
