"""Shared test helpers.

Every existing test file grew its own local `_login`. New tests use these
instead; the older copies are left alone rather than churned, since
they're doing no harm.
"""

from typing import Any

from httpx import AsyncClient


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


async def get_me(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get("/api/me")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, dict)
    return data
