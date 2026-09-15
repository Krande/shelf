"""Roles and the admin routes."""

import uuid

import pytest
from httpx import AsyncClient

from shelf.config import settings

from .helpers import get_me, login


async def test_new_users_are_not_admins(client: AsyncClient) -> None:
    await login(client, "a@example.com")
    me = await get_me(client)
    assert me["role"] == "user"
    assert me["is_admin"] is False


async def test_non_admin_is_refused(client: AsyncClient) -> None:
    await login(client, "a@example.com")
    assert (await client.get("/api/admin/users")).status_code == 403


async def test_anonymous_is_unauthorized(client: AsyncClient) -> None:
    assert (await client.get("/api/admin/users")).status_code == 401


async def test_env_bootstrap_promotes_on_login(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    await login(client, "admin@example.com")
    me = await get_me(client)
    assert me["role"] == "admin"
    assert me["is_admin"] is True


async def test_bootstrap_match_is_case_insensitive(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "admin_emails", ["Admin@Example.com"])
    await login(client, "admin@example.com")
    assert (await get_me(client))["is_admin"] is True


async def test_bootstrap_promotes_an_existing_user(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adding an email to the list promotes on their *next* login, not
    only at first sight."""
    await login(client, "a@example.com")
    assert (await get_me(client))["is_admin"] is False

    monkeypatch.setattr(settings, "admin_emails", ["a@example.com"])
    await login(client, "a@example.com")
    assert (await get_me(client))["is_admin"] is True


async def test_bootstrap_never_demotes(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dropping someone from the env list must not strip the role — the
    var is a way in, not a source of truth that fights the UI."""
    monkeypatch.setattr(settings, "admin_emails", ["a@example.com"])
    await login(client, "a@example.com")
    assert (await get_me(client))["is_admin"] is True

    monkeypatch.setattr(settings, "admin_emails", [])
    await login(client, "a@example.com")
    assert (await get_me(client))["is_admin"] is True


async def test_admin_lists_users(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    admin_id = await login(client, "admin@example.com")
    await login(client, "b@example.com", link=True)
    await client.post("/auth/switch", json={"user_id": admin_id})

    resp = await client.get("/api/admin/users")
    assert resp.status_code == 200, resp.text
    emails = {row["email"] for row in resp.json()}
    assert emails == {"admin@example.com", "b@example.com"}


async def test_admin_promotes_and_demotes(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    admin_id = await login(client, "admin@example.com")
    b_id = await login(client, "b@example.com", link=True)
    await client.post("/auth/switch", json={"user_id": admin_id})

    resp = await client.patch(f"/api/admin/users/{b_id}", json={"role": "admin"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "admin"

    # Now that there are two, either can be demoted.
    resp = await client.patch(f"/api/admin/users/{b_id}", json={"role": "user"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "user"


async def test_cannot_demote_the_last_admin(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    admin_id = await login(client, "admin@example.com")

    resp = await client.patch(f"/api/admin/users/{admin_id}", json={"role": "user"})
    assert resp.status_code == 409
    assert (await get_me(client))["is_admin"] is True


async def test_can_demote_when_another_admin_remains(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    admin_id = await login(client, "admin@example.com")
    b_id = await login(client, "b@example.com", link=True)
    await client.post("/auth/switch", json={"user_id": admin_id})
    await client.patch(f"/api/admin/users/{b_id}", json={"role": "admin"})

    # Demoting yourself is allowed once someone else can still get in.
    resp = await client.patch(f"/api/admin/users/{admin_id}", json={"role": "user"})
    assert resp.status_code == 200
    assert (await get_me(client))["is_admin"] is False
    # And the route is now closed to them.
    assert (await client.get("/api/admin/users")).status_code == 403


async def test_invalid_role_rejected(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    admin_id = await login(client, "admin@example.com")
    resp = await client.patch(f"/api/admin/users/{admin_id}", json={"role": "superuser"})
    assert resp.status_code == 422


async def test_patch_unknown_user(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    await login(client, "admin@example.com")
    resp = await client.patch(
        f"/api/admin/users/{uuid.uuid4()}", json={"role": "admin"}
    )
    assert resp.status_code == 404


async def test_role_change_takes_effect_immediately(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Role is re-read from the database per request rather than baked
    into the session, so a demotion doesn't wait for the cookie to
    expire. Asserted through a second session for the demoted user."""
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    admin_id = await login(client, "admin@example.com")
    b_id = await login(client, "b@example.com", link=True)
    await client.post("/auth/switch", json={"user_id": admin_id})
    await client.patch(f"/api/admin/users/{b_id}", json={"role": "admin"})

    # B's session predates the promotion and still sees it.
    await client.post("/auth/switch", json={"user_id": b_id})
    assert (await client.get("/api/admin/users")).status_code == 200

    # Demote B from A's session, then check B's untouched session lost it.
    await client.post("/auth/switch", json={"user_id": admin_id})
    await client.patch(f"/api/admin/users/{b_id}", json={"role": "user"})
    await client.post("/auth/switch", json={"user_id": b_id})
    assert (await client.get("/api/admin/users")).status_code == 403
