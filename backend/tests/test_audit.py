"""The audit log: the admin listing, and the events admin actions write.

Per-area hooks (items, spaces, downloads, …) are tested alongside the
routes in `test_audit_*.py`; this file covers the log itself.
"""

import pytest
from httpx import AsyncClient

from shelf.config import settings

from .helpers import login


async def _admin(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    return await login(client, "admin@example.com")


async def test_non_admin_is_refused(client: AsyncClient) -> None:
    await login(client, "a@example.com")
    assert (await client.get("/api/admin/audit")).status_code == 403
    assert (await client.get("/api/admin/audit/actions")).status_code == 403


async def test_lists_known_actions(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _admin(client, monkeypatch)
    actions = (await client.get("/api/admin/audit/actions")).json()
    assert "attachment.download" in actions
    assert "space.member.add" in actions


async def test_user_admin_is_logged(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin_id = await _admin(client, monkeypatch)
    created = await client.post(
        "/api/admin/users", json={"email": "new@example.com"}
    )
    assert created.status_code == 201
    user_id = created.json()["id"]
    await client.patch(f"/api/admin/users/{user_id}", json={"role": "admin"})
    # No change, no entry.
    await client.patch(f"/api/admin/users/{user_id}", json={"role": "admin"})

    page = (await client.get("/api/admin/audit")).json()
    assert [e["action"] for e in page["events"]] == ["user.update", "user.create"]
    update, create = page["events"]
    assert update["details"] == {"role": ["user", "admin"]}
    assert update["target_id"] == user_id
    assert update["target_label"] == "new@example.com"
    assert create["actor_id"] == admin_id
    assert create["actor_email"] == "admin@example.com"
    assert create["via"] == "web"
    assert page["next"] is None


async def test_pages_and_filters(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _admin(client, monkeypatch)
    for i in range(5):
        await client.post("/api/admin/users", json={"email": f"u{i}@example.com"})

    first = (await client.get("/api/admin/audit", params={"limit": 3})).json()
    assert len(first["events"]) == 3
    assert first["next"]
    second = (
        await client.get(
            "/api/admin/audit", params={"limit": 3, "before": first["next"]}
        )
    ).json()
    assert len(second["events"]) == 2
    assert second["next"] is None
    labels = [e["target_label"] for e in first["events"] + second["events"]]
    assert labels == [f"u{i}@example.com" for i in reversed(range(5))]

    by_family = (await client.get("/api/admin/audit", params={"action": "user."})).json()
    assert len(by_family["events"]) == 5
    none = (await client.get("/api/admin/audit", params={"action": "item."})).json()
    assert none["events"] == []
    exact = (
        await client.get("/api/admin/audit", params={"action": "user.update"})
    ).json()
    assert exact["events"] == []


async def test_bad_cursor_is_a_400(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _admin(client, monkeypatch)
    r = await client.get("/api/admin/audit", params={"before": "nonsense"})
    assert r.status_code == 400
