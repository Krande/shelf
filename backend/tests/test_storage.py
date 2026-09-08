"""Storage service smoke tests using an in-memory store.

We swap the global store for an obstore MemoryStore so we can exercise
put/head/delete without needing S3 credentials or a running Garage. Presign
isn't tested here — MemoryStore has no URL space, so signing is exercised
in integration tests against a real backend.
"""

from typing import Any

import pytest
from obstore import delete_async, get_async, head_async, put_async
from obstore.store import MemoryStore

from shelf.config import settings
from shelf.services import storage


def _captured_store_kwargs(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Build the store with S3Store stubbed out, returning its kwargs."""
    captured: dict[str, Any] = {}

    def fake_s3_store(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(storage, "S3Store", fake_s3_store)
    storage._build_store()
    return captured


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("http://localhost:3900", True),
        ("https://s3.example.com", False),
    ],
)
def test_http_endpoint_opts_into_plaintext(
    monkeypatch: pytest.MonkeyPatch, endpoint: str, expected: bool
) -> None:
    """obstore's client rejects a plaintext endpoint unless allow_http is set,
    failing with "BadScheme" before the request goes out. Presigned URLs mask
    it — those are fetched by the browser or httpx — so the damage lands on
    head/delete/copy: object_exists() reported False for objects that existed
    and ensure_original() skipped the OCR snapshot without raising."""
    monkeypatch.setattr(settings, "s3_endpoint", endpoint)
    kwargs = _captured_store_kwargs(monkeypatch)
    assert kwargs["client_options"] == {"allow_http": expected}


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
