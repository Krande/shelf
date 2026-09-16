"""Engineering standards: revision history, "latest", and pinning."""

import pytest
from httpx import AsyncClient

from shelf.config import settings

from .helpers import login


async def _personal_slug(client: AsyncClient) -> str:
    return str((await client.get("/api/me/spaces")).json()[0]["slug"])


async def _add_standard(
    client: AsyncClient,
    slug: str,
    *,
    designation: str,
    label: str,
    issued_on: str | None,
    body: str = "ACME",
    superseded: bool = False,
) -> str:
    """An item plus the revision row that files it under a standard."""
    created = await client.post(
        f"/api/spaces/{slug}/items",
        json={
            "item_type": "standard",
            "data": {"title": f"{designation}:{label}"},
        },
    )
    assert created.status_code == 201, created.text
    item_id = str(created.json()["id"])

    linked = await client.put(
        f"/api/items/{item_id}/revision",
        json={
            "body": body,
            "designation": designation,
            "label": label,
            "issued_on": issued_on,
            "superseded": superseded,
        },
    )
    assert linked.status_code == 200, linked.text
    return item_id


@pytest.fixture
async def library(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str, dict[str, str]]:
    """An admin, a subscribable "standards" space, three ACME 1234
    editions. Returns (admin_id, slug, {label: item_id})."""
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    admin_id = await login(client, "admin@example.com")
    r = await client.post(
        "/api/spaces", json={"name": "Standards", "slug": "standards"}
    )
    assert r.status_code == 201, r.text
    slug = str(r.json()["slug"])
    await client.patch(
        f"/api/spaces/{slug}/settings", json={"subscribable": True}
    )

    ids = {
        label: await _add_standard(
            client,
            slug,
            designation="ACME 1234",
            label=label,
            issued_on=issued,
        )
        for label, issued in (
            ("2007", "2007-12-01"),
            ("2020", "2020-11-01"),
            ("2014", "2014-06-01"),
        )
    }
    return admin_id, slug, ids


# ── Revision history ─────────────────────────────────────────────────────────


async def test_revisions_are_newest_first(
    client: AsyncClient, library: tuple[str, str, dict[str, str]]
) -> None:
    _admin, _slug, ids = library
    body = (await client.get(f"/api/items/{ids['2007']}/revisions")).json()
    assert [r["label"] for r in body["revisions"]] == ["2020", "2014", "2007"]
    assert body["family"]["designation"] == "ACME 1234"


async def test_the_newest_is_flagged_latest(
    client: AsyncClient, library: tuple[str, str, dict[str, str]]
) -> None:
    _admin, _slug, ids = library
    body = (await client.get(f"/api/items/{ids['2007']}/revisions")).json()
    latest = [r["label"] for r in body["revisions"] if r["is_latest"]]
    assert latest == ["2020"]
    assert body["is_latest_known"] is True


async def test_a_withdrawn_edition_is_never_latest(
    client: AsyncClient, library: tuple[str, str, dict[str, str]]
) -> None:
    """A withdrawn late amendment must not displace the current edition."""
    _admin, slug, ids = library
    await _add_standard(
        client,
        slug,
        designation="ACME 1234",
        label="2023 (withdrawn)",
        issued_on="2023-01-01",
        superseded=True,
    )
    body = (await client.get(f"/api/items/{ids['2007']}/revisions")).json()
    assert [r["label"] for r in body["revisions"] if r["is_latest"]] == ["2020"]


async def test_an_undated_edition_is_never_latest(
    client: AsyncClient, library: tuple[str, str, dict[str, str]]
) -> None:
    """Labels don't sort, so an undated import must not claim the top."""
    _admin, slug, ids = library
    undated = await _add_standard(
        client, slug, designation="ACME 1234", label="Rev. ?", issued_on=None
    )
    body = (await client.get(f"/api/items/{ids['2007']}/revisions")).json()
    assert [r["label"] for r in body["revisions"] if r["is_latest"]] == ["2020"]
    # And it sorts last rather than first.
    assert body["revisions"][-1]["item_id"] == undated


async def test_case_insensitive_designations_join_one_history(
    client: AsyncClient, library: tuple[str, str, dict[str, str]]
) -> None:
    """A second upload typed differently must not fork the history."""
    _admin, slug, ids = library
    await _add_standard(
        client,
        slug,
        designation="ACME 1234",
        body="ACME",
        label="2024",
        issued_on="2024-02-01",
    )
    body = (await client.get(f"/api/items/{ids['2007']}/revisions")).json()
    assert len(body["revisions"]) == 4
    assert [r["label"] for r in body["revisions"] if r["is_latest"]] == ["2024"]


async def test_latest_is_relative_to_what_the_caller_can_see(
    client: AsyncClient, library: tuple[str, str, dict[str, str]]
) -> None:
    """Someone who only inherits part of the picture is told the newest
    they can open — and told that a newer one exists."""
    admin_id, _slug, ids = library

    # A second space with only the 2007 edition, not subscribed to
    # standards. Its owner sees one revision.
    r = await client.post("/api/spaces", json={"name": "Old", "slug": "old"})
    old_slug = str(r.json()["slug"])
    only = await _add_standard(
        client, old_slug, designation="ACME 1234", label="2007", issued_on="2007-12-01"
    )

    other_id = await login(client, "other@example.com", link=True)
    await client.post("/auth/switch", json={"user_id": admin_id})
    await client.post(
        f"/api/spaces/{old_slug}/members",
        json={"email": "other@example.com", "role": "viewer"},
    )
    await client.post("/auth/switch", json={"user_id": other_id})

    body = (await client.get(f"/api/items/{only}/revisions")).json()
    assert [r["label"] for r in body["revisions"]] == ["2007"]
    assert body["revisions"][0]["is_latest"] is True
    # But the instance has newer ones they can't reach.
    assert body["is_latest_known"] is False
    assert ids["2020"] not in {r["item_id"] for r in body["revisions"]}


async def test_an_item_that_is_not_a_standard_has_no_revisions(
    client: AsyncClient, library: tuple[str, str, dict[str, str]]
) -> None:
    _admin, slug, _ids = library
    created = await client.post(
        f"/api/spaces/{slug}/items",
        json={"item_type": "report", "data": {"title": "Not a standard"}},
    )
    r = await client.get(f"/api/items/{created.json()['id']}/revisions")
    assert r.status_code == 404


async def test_linking_a_revision_needs_editor(
    client: AsyncClient, library: tuple[str, str, dict[str, str]]
) -> None:
    """An inherited copy is read-only, metadata included."""
    _admin, std_slug, ids = library
    await login(client, "eng@example.com", link=True)
    personal = await _personal_slug(client)
    await client.post(
        f"/api/spaces/{personal}/inherits", json={"parent_slug": std_slug}
    )
    r = await client.put(
        f"/api/items/{ids['2020']}/revision",
        json={"body": "ACME", "designation": "ACME 1234", "label": "hacked"},
    )
    assert r.status_code == 403


async def test_a_bad_date_is_rejected(
    client: AsyncClient, library: tuple[str, str, dict[str, str]]
) -> None:
    _admin, _slug, ids = library
    r = await client.put(
        f"/api/items/{ids['2007']}/revision",
        json={
            "body": "ACME",
            "designation": "ACME 1234",
            "label": "2007",
            "issued_on": "December 2007",
        },
    )
    assert r.status_code == 400


# ── Pinning ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def project(
    client: AsyncClient, library: tuple[str, str, dict[str, str]]
) -> tuple[str, str, dict[str, str]]:
    """A project space inheriting standards. Returns (slug, family_id,
    revision ids), client left as the admin who owns both."""
    _admin, std_slug, ids = library
    r = await client.post(
        "/api/spaces", json={"name": "Project X", "slug": "project-x"}
    )
    slug = str(r.json()["slug"])
    await client.post(
        f"/api/spaces/{slug}/inherits", json={"parent_slug": std_slug}
    )
    body = (await client.get(f"/api/items/{ids['2007']}/revisions")).json()
    return slug, str(body["family"]["id"]), ids


async def test_pinning_hides_the_other_revisions(
    client: AsyncClient, project: tuple[str, str, dict[str, str]]
) -> None:
    slug, family_id, ids = project
    listed = (await client.get(f"/api/spaces/{slug}/items")).json()
    assert len(listed["items"]) == 3

    r = await client.put(
        f"/api/spaces/{slug}/pins/{family_id}", json={"item_id": ids["2007"]}
    )
    assert r.status_code == 200, r.text

    listed = (await client.get(f"/api/spaces/{slug}/items")).json()
    assert [i["id"] for i in listed["items"]] == [ids["2007"]]
    assert listed["total"] == 1


async def test_revisions_all_reveals_them_again(
    client: AsyncClient, project: tuple[str, str, dict[str, str]]
) -> None:
    slug, family_id, ids = project
    await client.put(
        f"/api/spaces/{slug}/pins/{family_id}", json={"item_id": ids["2007"]}
    )
    listed = (await client.get(f"/api/spaces/{slug}/items?revisions=all")).json()
    assert len(listed["items"]) == 3


async def test_an_unpinned_standard_keeps_every_revision(
    client: AsyncClient, project: tuple[str, str, dict[str, str]]
) -> None:
    """Pinning one family must not filter another."""
    slug, family_id, ids = project
    await client.put(
        f"/api/spaces/{slug}/pins/{family_id}", json={"item_id": ids["2007"]}
    )
    # A second standard, never pinned.
    await _add_standard(
        client,
        "standards",
        designation="ZENCO 55-2",
        body="ZENCO",
        label="Rev. 3",
        issued_on="2013-02-01",
    )
    await _add_standard(
        client,
        "standards",
        designation="ZENCO 55-2",
        body="ZENCO",
        label="Rev. 2",
        issued_on="2004-10-01",
    )
    listed = (await client.get(f"/api/spaces/{slug}/items")).json()
    titles = {i["data"]["title"] for i in listed["items"]}
    assert "ZENCO 55-2:Rev. 3" in titles
    assert "ZENCO 55-2:Rev. 2" in titles


async def test_the_pin_is_reported_against_the_space(
    client: AsyncClient, project: tuple[str, str, dict[str, str]]
) -> None:
    slug, family_id, ids = project
    await client.put(
        f"/api/spaces/{slug}/pins/{family_id}", json={"item_id": ids["2007"]}
    )
    body = (
        await client.get(f"/api/items/{ids['2007']}/revisions?space={slug}")
    ).json()
    pinned = [r["label"] for r in body["revisions"] if r["is_pinned"]]
    assert pinned == ["2007"]
    # Still honest about which one is current.
    assert [r["label"] for r in body["revisions"] if r["is_latest"]] == ["2020"]


async def test_pins_are_per_space(
    client: AsyncClient, project: tuple[str, str, dict[str, str]]
) -> None:
    """The Standards space itself stays the complete record."""
    slug, family_id, ids = project
    await client.put(
        f"/api/spaces/{slug}/pins/{family_id}", json={"item_id": ids["2007"]}
    )
    listed = (await client.get("/api/spaces/standards/items")).json()
    assert len(listed["items"]) == 3


async def test_cannot_pin_an_item_from_another_family(
    client: AsyncClient, project: tuple[str, str, dict[str, str]]
) -> None:
    slug, family_id, _ids = project
    other = await _add_standard(
        client,
        "standards",
        designation="ZENCO 55-2",
        body="ZENCO",
        label="Rev. 3",
        issued_on="2013-02-01",
    )
    r = await client.put(
        f"/api/spaces/{slug}/pins/{family_id}", json={"item_id": other}
    )
    assert r.status_code == 400
    assert "not a revision" in r.json()["detail"]


async def test_cannot_pin_an_item_the_space_cannot_see(
    client: AsyncClient, project: tuple[str, str, dict[str, str]]
) -> None:
    """A library hiding four revisions in favour of one nobody can open
    is worse than no pin at all."""
    slug, family_id, _ids = project
    r = await client.post("/api/spaces", json={"name": "Elsewhere", "slug": "elsewhere"})
    elsewhere = str(r.json()["slug"])
    hidden = await _add_standard(
        client, elsewhere, designation="ACME 1234", label="1997", issued_on="1997-01-01"
    )
    resp = await client.put(
        f"/api/spaces/{slug}/pins/{family_id}", json={"item_id": hidden}
    )
    assert resp.status_code == 400
    assert "isn't in this space" in resp.json()["detail"]


async def test_pinning_needs_owner_not_editor(
    client: AsyncClient, project: tuple[str, str, dict[str, str]]
) -> None:
    """Which edition a project builds to is a project-level decision."""
    slug, family_id, ids = project
    admin_id = (await client.get("/api/me")).json()["id"]
    editor_id = await login(client, "editor@example.com", link=True)
    await client.post("/auth/switch", json={"user_id": admin_id})
    await client.post(
        f"/api/spaces/{slug}/members",
        json={"email": "editor@example.com", "role": "editor"},
    )
    await client.post("/auth/switch", json={"user_id": editor_id})

    r = await client.put(
        f"/api/spaces/{slug}/pins/{family_id}", json={"item_id": ids["2007"]}
    )
    assert r.status_code == 403


async def test_clearing_a_pin_restores_the_listing(
    client: AsyncClient, project: tuple[str, str, dict[str, str]]
) -> None:
    slug, family_id, ids = project
    await client.put(
        f"/api/spaces/{slug}/pins/{family_id}", json={"item_id": ids["2007"]}
    )
    assert (
        await client.delete(f"/api/spaces/{slug}/pins/{family_id}")
    ).status_code == 204
    listed = (await client.get(f"/api/spaces/{slug}/items")).json()
    assert len(listed["items"]) == 3


async def test_pins_survive_unsubscribing(
    client: AsyncClient, project: tuple[str, str, dict[str, str]]
) -> None:
    """A project's record of which revision it chose outlives a dropped
    subscription — but it isn't listed while the items are unreachable."""
    slug, family_id, ids = project
    await client.put(
        f"/api/spaces/{slug}/pins/{family_id}", json={"item_id": ids["2007"]}
    )
    await client.delete(f"/api/spaces/{slug}/inherits/standards")
    assert (await client.get(f"/api/spaces/{slug}/pins")).json() == []

    await client.post(
        f"/api/spaces/{slug}/inherits", json={"parent_slug": "standards"}
    )
    pins = (await client.get(f"/api/spaces/{slug}/pins")).json()
    assert [p["label"] for p in pins] == ["2007"]
