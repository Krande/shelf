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


async def test_admin_renames_a_user(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    admin_id = await login(client, "admin@example.com")
    b_id = await login(client, "b@example.com", link=True)
    await client.post("/auth/switch", json={"user_id": admin_id})

    resp = await client.patch(
        f"/api/admin/users/{b_id}", json={"display_name": "  Grace Hopper  "}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["display_name"] == "Grace Hopper"

    listed = {u["id"]: u for u in (await client.get("/api/admin/users")).json()}
    assert listed[b_id]["display_name"] == "Grace Hopper"


async def test_rename_leaves_the_role_alone(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A name-only PATCH is a partial update, not a replace — the admin
    table sends one field at a time."""
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    admin_id = await login(client, "admin@example.com")

    resp = await client.patch(
        f"/api/admin/users/{admin_id}", json={"display_name": "Renamed"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "admin"
    assert (await get_me(client))["is_admin"] is True


async def test_rename_and_role_in_one_patch(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    admin_id = await login(client, "admin@example.com")
    b_id = await login(client, "b@example.com", link=True)
    await client.post("/auth/switch", json={"user_id": admin_id})

    resp = await client.patch(
        f"/api/admin/users/{b_id}",
        json={"display_name": "Grace", "role": "admin"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["display_name"] == "Grace"
    assert body["role"] == "admin"


async def test_rename_rejects_a_blank_name(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """display_name is NOT NULL and every screen shows it, so there's no
    such thing as clearing one."""
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    admin_id = await login(client, "admin@example.com")

    resp = await client.patch(
        f"/api/admin/users/{admin_id}", json={"display_name": "   "}
    )
    assert resp.status_code == 400
    assert (await get_me(client))["display_name"] == "admin"


async def test_rename_rejects_an_overlong_name(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    admin_id = await login(client, "admin@example.com")
    resp = await client.patch(
        f"/api/admin/users/{admin_id}", json={"display_name": "x" * 201}
    )
    assert resp.status_code == 422


async def test_empty_patch_is_rejected(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both fields are optional, so a body with neither would otherwise be
    a silent no-op — and a misspelled field name would look like success."""
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    admin_id = await login(client, "admin@example.com")
    resp = await client.patch(f"/api/admin/users/{admin_id}", json={})
    assert resp.status_code == 400


async def test_a_rename_alongside_a_refused_demotion_is_not_saved(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 409 aborts the whole PATCH, not just the role half."""
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    admin_id = await login(client, "admin@example.com")

    resp = await client.patch(
        f"/api/admin/users/{admin_id}",
        json={"display_name": "Renamed", "role": "user"},
    )
    assert resp.status_code == 409
    me = await get_me(client)
    assert me["is_admin"] is True
    assert me["display_name"] == "admin"


async def test_rename_refused_to_non_admins(client: AsyncClient) -> None:
    user_id = await login(client, "a@example.com")
    resp = await client.patch(
        f"/api/admin/users/{user_id}", json={"display_name": "Nice Try"}
    )
    assert resp.status_code == 403


async def test_rename_survives_a_later_login(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing syncs names back from the provider, so a correction here
    isn't undone the next time that person signs in."""
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    admin_id = await login(client, "admin@example.com")
    b_id = await login(client, "b@example.com", link=True)
    await client.post("/auth/switch", json={"user_id": admin_id})
    await client.patch(f"/api/admin/users/{b_id}", json={"display_name": "Grace"})

    await login(client, "b@example.com", link=True, display_name="b")
    assert (await get_me(client))["display_name"] == "Grace"


async def test_patch_unknown_user(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    await login(client, "admin@example.com")
    resp = await client.patch(
        f"/api/admin/users/{uuid.uuid4()}", json={"role": "admin"}
    )
    assert resp.status_code == 404


async def _as_admin(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    return await login(client, "admin@example.com")


async def test_create_user_pre_provisions(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _as_admin(client, monkeypatch)

    resp = await client.post(
        "/api/admin/users",
        json={"email": "new@example.com", "display_name": "New Person"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "new@example.com"
    assert body["display_name"] == "New Person"
    assert body["role"] == "user"

    # Visible to the admin list and to the member picker straight away —
    # the whole point is being able to share a space with them before
    # they've ever signed in.
    assert "new@example.com" in {u["email"] for u in (await client.get("/api/admin/users")).json()}
    assert "new@example.com" in {u["email"] for u in (await client.get("/api/users")).json()}


async def test_created_user_gets_a_personal_space(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without one they'd sign in to an account that can't hold anything."""
    await _as_admin(client, monkeypatch)
    await client.post("/api/admin/users", json={"email": "new@example.com"})

    await login(client, "new@example.com", link=True)
    spaces = (await client.get("/api/me/spaces")).json()
    assert len(spaces) == 1
    assert spaces[0]["is_personal"] is True
    assert spaces[0]["is_owner"] is True


async def test_first_login_links_onto_the_pre_provisioned_row(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The email match in `upsert_user_from_claims` is what makes
    pre-provisioning worth anything: signing in must adopt the seeded
    row, not mint a second account beside it."""
    await _as_admin(client, monkeypatch)
    created = (
        await client.post(
            "/api/admin/users",
            json={"email": "new@example.com", "display_name": "New Person"},
        )
    ).json()

    logged_in_id = await login(client, "new@example.com", link=True)
    assert logged_in_id == created["id"]
    me = await get_me(client)
    assert me["display_name"] == "New Person"


async def test_display_name_defaults_to_the_local_part(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _as_admin(client, monkeypatch)
    resp = await client.post("/api/admin/users", json={"email": "jo.blogs@example.com"})
    assert resp.json()["display_name"] == "jo.blogs"


async def test_create_user_can_seed_an_admin(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _as_admin(client, monkeypatch)
    resp = await client.post(
        "/api/admin/users", json={"email": "two@example.com", "role": "admin"}
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "admin"


async def test_create_user_rejects_a_duplicate(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _as_admin(client, monkeypatch)
    await client.post("/api/admin/users", json={"email": "dup@example.com"})

    resp = await client.post("/api/admin/users", json={"email": "dup@example.com"})
    assert resp.status_code == 409


async def test_duplicate_check_ignores_case(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """users.email is CITEXT; a second row differing only in case would
    be rejected by the unique index as a 500 rather than a 409."""
    await _as_admin(client, monkeypatch)
    await client.post("/api/admin/users", json={"email": "dup@example.com"})

    resp = await client.post("/api/admin/users", json={"email": "DUP@Example.com"})
    assert resp.status_code == 409


async def test_create_user_rejects_an_existing_login(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin_id = await _as_admin(client, monkeypatch)
    await login(client, "b@example.com", link=True)
    await client.post("/auth/switch", json={"user_id": admin_id})

    resp = await client.post("/api/admin/users", json={"email": "b@example.com"})
    assert resp.status_code == 409


@pytest.mark.parametrize(
    "email", ["not-an-email", "@example.com", "nobody@", "a b@example.com", "x@localhost"]
)
async def test_create_user_rejects_malformed_addresses(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, email: str
) -> None:
    await _as_admin(client, monkeypatch)
    resp = await client.post("/api/admin/users", json={"email": email})
    assert resp.status_code in (400, 422), resp.text


async def test_create_user_trims_whitespace(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _as_admin(client, monkeypatch)
    resp = await client.post(
        "/api/admin/users",
        json={"email": "  spaced@example.com  ", "display_name": "  Spaced  "},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["email"] == "spaced@example.com"
    assert resp.json()["display_name"] == "Spaced"


async def test_create_user_refused_to_non_admins(client: AsyncClient) -> None:
    await login(client, "a@example.com")
    resp = await client.post("/api/admin/users", json={"email": "new@example.com"})
    assert resp.status_code == 403


async def test_create_user_refused_to_anonymous(client: AsyncClient) -> None:
    resp = await client.post("/api/admin/users", json={"email": "new@example.com"})
    assert resp.status_code == 401


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
