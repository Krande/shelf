"""Shared test helpers.

Every existing test file grew its own local `_login`. New tests use these
instead; the older copies are left alone rather than churned, since
they're doing no harm.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import AsyncClient

from shelf.services import storage


async def login(
    client: AsyncClient,
    email: str,
    *,
    link: bool = False,
    display_name: str | None = None,
) -> str:
    """Dev-login as `email`, returning the user id.

    `link=True` appends to the session already on the client instead of
    replacing it — the no-IdP equivalent of /auth/link/{provider}. The
    cookie lands on the client's jar, so subsequent requests are made as
    the newly active account.

    `display_name` is only honoured the first time an address is seen,
    since that's when the row is created; it defaults to the local part.
    """
    body: dict[str, object] = {"email": email, "link": link}
    if display_name is not None:
        body["display_name"] = display_name
    resp = await client.post("/auth/dev-login", json=body)
    assert resp.status_code == 200, resp.text
    user_id = resp.json()["user_id"]
    assert isinstance(user_id, str)
    return user_id


def serve_objects(
    monkeypatch: pytest.MonkeyPatch,
    read: Callable[[str], Awaitable[bytes]],
    *,
    chunk_size: int = 4,
) -> None:
    """Fake object storage for code that streams: `storage.stream_object`
    serves whatever `read(key)` returns, split into `chunk_size` pieces
    so a multi-chunk entry is exercised even with tiny bodies. `read`
    raising is an object that can't be opened.

    `read_object` is faked too, for the paths that still read whole.
    """

    @asynccontextmanager
    async def fake_stream(
        key: str, client: object
    ) -> AsyncIterator[tuple[int, AsyncIterator[bytes]]]:
        body = await read(key)

        async def chunks() -> AsyncIterator[bytes]:
            for i in range(0, len(body), chunk_size):
                yield body[i : i + chunk_size]

        yield len(body), chunks()

    monkeypatch.setattr(storage, "stream_object", fake_stream)
    monkeypatch.setattr(storage, "read_object", read)


async def get_me(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get("/api/me")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, dict)
    return data
