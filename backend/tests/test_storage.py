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


def _captured_store_kwargs(
    monkeypatch: pytest.MonkeyPatch, endpoint: str | None = None
) -> dict[str, Any]:
    """Build the store with S3Store stubbed out, returning its kwargs."""
    captured: dict[str, Any] = {}

    def fake_s3_store(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(storage, "S3Store", fake_s3_store)
    storage._build_store(endpoint if endpoint is not None else settings.s3_endpoint)
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


@pytest.mark.parametrize(
    ("public", "expect_separate"),
    [
        ("", False),
        ("http://localhost:3900", False),
        ("https://s3.example.com", True),
    ],
)
def test_browser_store_uses_public_endpoint(
    monkeypatch: pytest.MonkeyPatch, public: str, expect_separate: bool
) -> None:
    """Presigned URLs are signed for s3_endpoint_public when it names a
    different host, so the URL handed to a browser points somewhere the
    browser can reach. Unset or identical means one shared store."""
    monkeypatch.setattr(settings, "s3_endpoint", "http://localhost:3900")
    monkeypatch.setattr(settings, "s3_endpoint_public", public)
    storage.reset_store()
    try:
        server = storage.get_store()
        browser = storage._browser_store()
        assert (browser is not server) == expect_separate
    finally:
        storage.reset_store()


def test_browser_store_allow_http_follows_its_own_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    """The allow_http opt-in is derived from the endpoint the store is built
    for, so an http:// server endpoint does not leak the opt-in into an
    https:// browser store."""
    kwargs = _captured_store_kwargs(monkeypatch, "https://s3.example.com")
    assert kwargs["client_options"] == {"allow_http": False}
    assert kwargs["endpoint"] == "https://s3.example.com"


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
