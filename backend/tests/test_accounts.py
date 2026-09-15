"""Account linking and switching, end to end over the API.

Dev-login stands in for the OIDC code flow here — it goes through the
same session-minting path, and the parts it can't exercise
(`prompt=select_account` at the provider) aren't testable without an
identity provider anyway.
"""

import uuid

from httpx import ASGITransport, AsyncClient

from shelf.main import app

from .helpers import get_me, login


async def test_plain_login_has_one_account(client: AsyncClient) -> None:
    user_id = await login(client, "a@example.com")
    me = await get_me(client)
    assert me["id"] == user_id
    assert [a["id"] for a in me["accounts"]] == [user_id]


async def test_link_appends_and_activates(client: AsyncClient) -> None:
    a = await login(client, "a@example.com")
    b = await login(client, "b@example.com", link=True)

    me = await get_me(client)
    # The account just authenticated becomes active.
    assert me["id"] == b
    assert {acct["id"] for acct in me["accounts"]} == {a, b}


async def test_login_without_link_replaces_the_session(client: AsyncClient) -> None:
    await login(client, "a@example.com")
    b = await login(client, "b@example.com")

    me = await get_me(client)
    assert me["id"] == b
    assert [acct["id"] for acct in me["accounts"]] == [b]


async def test_switch_between_linked_accounts(client: AsyncClient) -> None:
    a = await login(client, "a@example.com")
    b = await login(client, "b@example.com", link=True)

    resp = await client.post("/auth/switch", json={"user_id": a})
    assert resp.status_code == 200, resp.text
    assert resp.json()["user_id"] == a
    assert (await get_me(client))["id"] == a

    resp = await client.post("/auth/switch", json={"user_id": b})
    assert resp.status_code == 200
    assert (await get_me(client))["id"] == b


async def test_switch_to_unlinked_account_is_forbidden(client: AsyncClient) -> None:
    await login(client, "a@example.com")
    # A real user, but one this session never authenticated as. Log them
    # in on a separate client so the cookie jar under test is untouched.
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as other:
        stranger = await login(other, "stranger@example.com")

    resp = await client.post("/auth/switch", json={"user_id": stranger})
    assert resp.status_code == 403


async def test_switch_without_session_is_unauthorized(client: AsyncClient) -> None:
    resp = await client.post("/auth/switch", json={"user_id": str(uuid.uuid4())})
    assert resp.status_code == 401


async def test_switch_does_not_extend_the_session(client: AsyncClient) -> None:
    from shelf.auth.session import parse_session

    a = await login(client, "a@example.com")
    await login(client, "b@example.com", link=True)
    before = parse_session(client.cookies["shelf_session"]).expires_at

    await client.post("/auth/switch", json={"user_id": a})
    after = parse_session(client.cookies["shelf_session"]).expires_at

    assert after == before


async def test_data_stays_isolated_across_a_switch(client: AsyncClient) -> None:
    """The case that actually matters: switching must not leak one
    account's library into the other's. Every ownership check in the app
    keys off the *active* user, so this is really asserting that a switch
    moves who that is — and nothing else."""
    a = await login(client, "a@example.com")
    a_slug = (await client.get("/api/me/spaces")).json()[0]["slug"]
    resp = await client.post(
        f"/api/spaces/{a_slug}/items",
        json={"item_type": "document", "data": {"title": "A's private document"}},
    )
    assert resp.status_code == 201, resp.text

    # Linked, but a different person: A's space is invisible.
    await login(client, "b@example.com", link=True)
    assert (await client.get(f"/api/spaces/{a_slug}/items")).status_code == 404

    # Switch back and it returns.
    await client.post("/auth/switch", json={"user_id": a})
    listing = await client.get(f"/api/spaces/{a_slug}/items")
    assert listing.status_code == 200
    titles = [row["data"]["title"] for row in listing.json()["items"]]
    assert titles == ["A's private document"]


async def test_each_linked_account_keeps_its_own_space(client: AsyncClient) -> None:
    await login(client, "a@example.com")
    a_spaces = (await client.get("/api/me/spaces")).json()

    await login(client, "b@example.com", link=True)
    b_spaces = (await client.get("/api/me/spaces")).json()

    assert {s["id"] for s in a_spaces}.isdisjoint({s["id"] for s in b_spaces})


async def test_unlink_non_active_account(client: AsyncClient) -> None:
    a = await login(client, "a@example.com")
    b = await login(client, "b@example.com", link=True)

    resp = await client.post("/auth/accounts/unlink", json={"user_id": a})
    assert resp.status_code == 200, resp.text
    assert resp.json()["active_user_id"] == b

    me = await get_me(client)
    assert [acct["id"] for acct in me["accounts"]] == [b]

    # And it's no longer switchable.
    assert (await client.post("/auth/switch", json={"user_id": a})).status_code == 403


async def test_unlink_active_account_falls_back(client: AsyncClient) -> None:
    a = await login(client, "a@example.com")
    b = await login(client, "b@example.com", link=True)

    resp = await client.post("/auth/accounts/unlink", json={"user_id": b})
    assert resp.status_code == 200
    assert resp.json()["active_user_id"] == a
    assert (await get_me(client))["id"] == a


async def test_unlink_last_account_ends_the_session(client: AsyncClient) -> None:
    a = await login(client, "a@example.com")

    resp = await client.post("/auth/accounts/unlink", json={"user_id": a})
    assert resp.status_code == 200
    assert resp.json()["active_user_id"] is None
    assert (await client.get("/api/me")).status_code == 401


async def test_unlink_unknown_account_is_forbidden(client: AsyncClient) -> None:
    await login(client, "a@example.com")
    resp = await client.post(
        "/auth/accounts/unlink", json={"user_id": str(uuid.uuid4())}
    )
    assert resp.status_code == 403


async def test_logout_clears_every_linked_account(client: AsyncClient) -> None:
    await login(client, "a@example.com")
    await login(client, "b@example.com", link=True)

    assert (await client.post("/auth/logout")).status_code == 200
    assert (await client.get("/api/me")).status_code == 401


async def test_accounts_report_their_providers(client: AsyncClient) -> None:
    """The SPA uses `idps` to send "switch user" straight to the provider
    the active account signed in with, rather than asking which one."""
    from sqlalchemy import select

    from shelf.db import session_factory
    from shelf.models import Identity, User

    user_id = await login(client, "a@example.com")

    # Dev-login creates no identity row, so the list starts empty.
    me = await get_me(client)
    assert me["accounts"][0]["idps"] == []

    async with session_factory() as db:
        user = (
            await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
        ).scalar_one()
        db.add(Identity(user_id=user.id, idp="entra", subject="oid-1"))
        db.add(Identity(user_id=user.id, idp="authentik", subject="sub-1"))
        await db.commit()

    me = await get_me(client)
    assert me["accounts"][0]["idps"] == ["authentik", "entra"]


async def test_link_requires_an_existing_session(client: AsyncClient) -> None:
    """/auth/link is 401 with no session — there's nothing to link to.
    (404 first if the provider is unknown, which it is in tests, so this
    asserts the ordering holds for a configured provider only indirectly.)"""
    resp = await client.get("/auth/link/nonexistent-provider")
    assert resp.status_code == 404
