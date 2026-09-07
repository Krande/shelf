"""Storage service smoke tests using an in-memory store.

We swap the global store for an obstore MemoryStore so we can exercise
put/head/delete without needing S3 credentials or a running Garage. Presign
isn't tested here — MemoryStore has no URL space, so signing is exercised
in integration tests against a real backend.
"""

from obstore import delete_async, get_async, head_async, put_async
from obstore.store import MemoryStore

from shelf.services import storage


async def test_memory_store_roundtrip() -> None:
    storage._store = MemoryStore()
    try:
        store = storage.get_store()
        await put_async(store, "hello.txt", b"world")

        meta = await head_async(store, "hello.txt")
        assert meta["size"] == 5

        body = await get_async(store, "hello.txt")
        assert (await body.bytes_async()) == b"world"

        await delete_async(store, "hello.txt")
    finally:
        storage.reset_store()
