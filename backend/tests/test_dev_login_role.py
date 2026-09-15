"""SHELF_DEV_LOGIN_ROLE — the role dev-login accounts get.

A fresh checkout has no OIDC provider, so SHELF_ADMIN_EMAILS is
unreachable and every dev account would be a plain user with no way to
see the admin surface. This knob fixes that for local development while
leaving the shipped default safe, since dev login mints a session for any
address presented.
"""

import pytest
from httpx import AsyncClient

from shelf.config import settings

from .helpers import get_me, login


async def test_defaults_to_user(client: AsyncClient) -> None:
    """The shipped default. dev_login_enabled is on by default, so an
    instance that forgot to turn it off must not also hand out admin."""
    assert settings.dev_login_role == "user"
    await login(client, "dev@example.com")
    assert (await get_me(client))["is_admin"] is False


async def test_admin_role_is_granted(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "dev_login_role", "admin")
    await login(client, "dev@example.com")
    me = await get_me(client)
    assert me["role"] == "admin"
    assert me["is_admin"] is True
    assert (await client.get("/api/admin/users")).status_code == 200


async def test_applies_to_an_existing_account_on_next_login(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await login(client, "dev@example.com")
    assert (await get_me(client))["is_admin"] is False

    monkeypatch.setattr(settings, "dev_login_role", "admin")
    await login(client, "dev@example.com")
    assert (await get_me(client))["is_admin"] is True


async def test_it_never_demotes(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Promote-only, like the admin-email bootstrap. A knob that demoted
    would silently strip, on next sign-in, a role someone had set in the
    admin UI on purpose."""
    monkeypatch.setattr(settings, "dev_login_role", "admin")
    await login(client, "dev@example.com")
    assert (await get_me(client))["is_admin"] is True

    monkeypatch.setattr(settings, "dev_login_role", "user")
    await login(client, "dev@example.com")
    assert (await get_me(client))["is_admin"] is True


async def test_does_not_fight_admin_emails(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both knobs only ever grant, so a 'user' setting here can't undo a
    promotion SHELF_ADMIN_EMAILS just made."""
    monkeypatch.setattr(settings, "dev_login_role", "user")
    monkeypatch.setattr(settings, "admin_emails", ["boss@example.com"])

    await login(client, "boss@example.com")
    assert (await get_me(client))["is_admin"] is True

    await login(client, "someone@example.com")
    assert (await get_me(client))["is_admin"] is False


async def test_a_typo_falls_back_to_user(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A misspelling must not hand out admin, nor hard-fail a local
    login."""
    monkeypatch.setattr(settings, "dev_login_role", "administrator")
    await login(client, "dev@example.com")
    me = await get_me(client)
    assert me["role"] == "user"
    assert me["is_admin"] is False


async def test_blank_falls_back_to_user(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "dev_login_role", "   ")
    await login(client, "dev@example.com")
    assert (await get_me(client))["role"] == "user"


async def test_role_does_not_leak_into_oidc_instances(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The knob is only reachable through /auth/dev-login, which 404s
    when dev login is off — so an instance running on OIDC alone cannot
    be affected by it however it is set."""
    monkeypatch.setattr(settings, "dev_login_role", "admin")
    monkeypatch.setattr(settings, "dev_login_enabled", False)
    r = await client.post("/auth/dev-login", json={"email": "dev@example.com"})
    assert r.status_code == 404
