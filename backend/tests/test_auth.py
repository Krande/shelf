"""Integration tests for /auth/dev-login, /auth/logout, and /api/me."""

from httpx import AsyncClient


async def test_me_unauthenticated(client: AsyncClient) -> None:
    r = await client.get("/api/me")
    assert r.status_code == 401


async def test_me_with_garbage_cookie(client: AsyncClient) -> None:
    client.cookies.set("shelf_session", "not-a-real-token")
    r = await client.get("/api/me")
    assert r.status_code == 401


async def test_dev_login_creates_user_and_session(client: AsyncClient) -> None:
    r = await client.post("/auth/dev-login", json={"email": "alice@example.com"})
    assert r.status_code == 200, r.text
    assert "shelf_session" in r.cookies

    r2 = await client.get("/api/me")
    assert r2.status_code == 200
    body = r2.json()
    assert body["email"] == "alice@example.com"
    assert body["display_name"] == "alice"


async def test_dev_login_with_explicit_display_name(client: AsyncClient) -> None:
    r = await client.post(
        "/auth/dev-login",
        json={"email": "bob@example.com", "display_name": "Bobby"},
    )
    assert r.status_code == 200
    me = (await client.get("/api/me")).json()
    assert me["display_name"] == "Bobby"


async def test_dev_login_idempotent_for_same_email(client: AsyncClient) -> None:
    r1 = await client.post("/auth/dev-login", json={"email": "carol@example.com"})
    user_id_1 = r1.json()["user_id"]

    r2 = await client.post(
        "/auth/dev-login",
        json={"email": "carol@example.com", "display_name": "Carol the Second"},
    )
    user_id_2 = r2.json()["user_id"]
    assert user_id_1 == user_id_2


async def test_logout_clears_cookie(client: AsyncClient) -> None:
    await client.post("/auth/dev-login", json={"email": "dan@example.com"})
    assert (await client.get("/api/me")).status_code == 200

    await client.post("/auth/logout")
    assert (await client.get("/api/me")).status_code == 401
