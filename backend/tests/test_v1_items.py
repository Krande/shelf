"""Setting item metadata through the token API.

The gap these close: a token could put *files* into shelf but not
describe them. The upload path minted a `document` item carrying a title
and nothing else, so an importer had to finish the job by hand in the
SPA.

The worked example is a nationally-adopted standard, which is the shape
that exercises the most of this: a good half-dozen metadata fields plus
a link to the standard's revision history before it's properly filed.
Every value is invented.
"""

import pytest
from httpx import AsyncClient

from shelf.config import settings

from .helpers import login

STANDARD = {
    "title": "Guidance on the design of widgets — Part 2: Plated widgets",
    "standardBody": "NX Standards",
    "designation": "NX-ACME 1234",
    "edition": "2015+A2:2020+NA:2020",
    "nationalAnnex": "NA:2020 (Ruritania)",
    "amendments": "AC:2017, A1:2018, A2:2020",
    "issuedOn": "2020-10-01",
    "supersedes": "NX-ACME 1234:2015+NA:2016",
    "language": "en",
    "numberOfPages": "74",
}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def token(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    await login(client, "admin@example.com")
    r = await client.post(
        "/api/me/tokens",
        json={"name": "importer", "scopes": ["upload", "search", "download"]},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["plaintext"])


# ── Creating with metadata ───────────────────────────────────────────────────


async def test_create_carries_every_field(
    client: AsyncClient, token: str
) -> None:
    r = await client.post(
        "/api/v1/items",
        headers=_auth(token),
        json={"item_type": "standard", "data": STANDARD},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["item_type"] == "standard"
    assert body["data"] == STANDARD


async def test_the_item_is_readable_back(
    client: AsyncClient, token: str
) -> None:
    created = (
        await client.post(
            "/api/v1/items",
            headers=_auth(token),
            json={"item_type": "standard", "data": STANDARD},
        )
    ).json()
    r = await client.get(
        f"/api/v1/items/{created['id']}", headers=_auth(token)
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["designation"] == "NX-ACME 1234"


async def test_create_defaults_to_a_document(
    client: AsyncClient, token: str
) -> None:
    r = await client.post("/api/v1/items", headers=_auth(token), json={})
    assert r.status_code == 201, r.text
    assert r.json()["item_type"] == "document"
    assert r.json()["data"] == {}


async def test_create_can_name_a_space(
    client: AsyncClient, token: str
) -> None:
    slug = str(
        (
            await client.post(
                "/api/spaces", json={"name": "Standards", "slug": "standards"}
            )
        ).json()["slug"]
    )
    r = await client.post(
        "/api/v1/items",
        headers=_auth(token),
        json={"item_type": "standard", "data": STANDARD, "space_slug": slug},
    )
    assert r.status_code == 201, r.text

    listed = (await client.get(f"/api/spaces/{slug}/items")).json()
    assert [i["data"]["designation"] for i in listed["items"]] == [
        "NX-ACME 1234"
    ]


async def test_create_rejects_an_unreachable_space(
    client: AsyncClient, token: str
) -> None:
    r = await client.post(
        "/api/v1/items",
        headers=_auth(token),
        json={"data": {}, "space_slug": "no-such-space"},
    )
    assert r.status_code == 404


# ── Updating ─────────────────────────────────────────────────────────────────


async def test_patch_replaces_data_by_default(
    client: AsyncClient, token: str
) -> None:
    """Matches the SPA's own PATCH, so the two don't mean different
    things by the same verb."""
    created = (
        await client.post(
            "/api/v1/items",
            headers=_auth(token),
            json={"item_type": "standard", "data": STANDARD},
        )
    ).json()
    r = await client.patch(
        f"/api/v1/items/{created['id']}",
        headers=_auth(token),
        json={"data": {"title": "Only this"}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"title": "Only this"}


async def test_patch_can_merge_instead(
    client: AsyncClient, token: str
) -> None:
    """The mode an enriching script wants: set one field, leave the rest
    of the metadata standing."""
    created = (
        await client.post(
            "/api/v1/items",
            headers=_auth(token),
            json={"item_type": "standard", "data": STANDARD},
        )
    ).json()
    r = await client.patch(
        f"/api/v1/items/{created['id']}",
        headers=_auth(token),
        json={"data": {"edition": "2015+A2:2020+NA:2020 (corrected)"}, "merge": True},
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["edition"] == "2015+A2:2020+NA:2020 (corrected)"
    assert data["designation"] == "NX-ACME 1234"
    assert data["nationalAnnex"] == "NA:2020 (Ruritania)"


async def test_merging_null_removes_a_field(
    client: AsyncClient, token: str
) -> None:
    created = (
        await client.post(
            "/api/v1/items",
            headers=_auth(token),
            json={"item_type": "standard", "data": STANDARD},
        )
    ).json()
    r = await client.patch(
        f"/api/v1/items/{created['id']}",
        headers=_auth(token),
        json={"data": {"supersedes": None}, "merge": True},
    )
    assert "supersedes" not in r.json()["data"]
    assert "designation" in r.json()["data"]


async def test_patch_can_change_the_item_type(
    client: AsyncClient, token: str
) -> None:
    created = (
        await client.post(
            "/api/v1/items", headers=_auth(token), json={"data": {"title": "x"}}
        )
    ).json()
    r = await client.patch(
        f"/api/v1/items/{created['id']}",
        headers=_auth(token),
        json={"item_type": "standard"},
    )
    assert r.json()["item_type"] == "standard"
    # Data untouched when the field is omitted.
    assert r.json()["data"] == {"title": "x"}


async def test_patch_needs_the_upload_scope(
    client: AsyncClient, token: str
) -> None:
    created = (
        await client.post(
            "/api/v1/items", headers=_auth(token), json={"data": {}}
        )
    ).json()
    read_only = str(
        (
            await client.post(
                "/api/me/tokens", json={"name": "ro", "scopes": ["search"]}
            )
        ).json()["plaintext"]
    )
    r = await client.patch(
        f"/api/v1/items/{created['id']}",
        headers=_auth(read_only),
        json={"data": {"title": "nope"}},
    )
    assert r.status_code == 403


# ── Filing under a standard ──────────────────────────────────────────────────


async def test_a_token_can_file_a_revision(
    client: AsyncClient, token: str
) -> None:
    created = (
        await client.post(
            "/api/v1/items",
            headers=_auth(token),
            json={"item_type": "standard", "data": STANDARD},
        )
    ).json()
    r = await client.put(
        f"/api/v1/items/{created['id']}/revision",
        headers=_auth(token),
        json={
            "body": "NX Standards",
            "designation": "NX-ACME 1234",
            "label": "2015+A2:2020+NA:2020",
            "issued_on": "2020-10-01",
            "title": STANDARD["title"],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["designation"] == "NX-ACME 1234"

    # And the SPA sees it as a revision, from the same family.
    revisions = (
        await client.get(f"/api/items/{created['id']}/revisions")
    ).json()
    assert revisions["family"]["designation"] == "NX-ACME 1234"
    assert [rev["label"] for rev in revisions["revisions"]] == [
        "2015+A2:2020+NA:2020"
    ]


async def test_two_editions_filed_by_token_share_one_history(
    client: AsyncClient, token: str
) -> None:
    """The reason the family lookup is shared code rather than copied:
    an importer and the SPA disagreeing about what counts as the same
    standard would split a revision history in half."""
    ids = []
    for label, issued in (
        ("2015+NA:2016", "2009-01-01"),
        ("2015+A2:2020+NA:2020", "2020-10-01"),
    ):
        created = (
            await client.post(
                "/api/v1/items",
                headers=_auth(token),
                json={"item_type": "standard", "data": {"title": label}},
            )
        ).json()
        ids.append(created["id"])
        r = await client.put(
            f"/api/v1/items/{created['id']}/revision",
            headers=_auth(token),
            json={
                # Deliberately different capitalisation on the second
                # pass — CITEXT means it's still one family.
                "body": "nx standards" if issued.startswith("2019") else "NX Standards",
                "designation": "nx-acme 1234" if issued.startswith("2019") else "NX-ACME 1234",
                "label": label,
                "issued_on": issued,
            },
        )
        assert r.status_code == 200, r.text

    revisions = (await client.get(f"/api/items/{ids[0]}/revisions")).json()
    assert len(revisions["revisions"]) == 2
    assert [r["label"] for r in revisions["revisions"] if r["is_latest"]] == [
        "2015+A2:2020+NA:2020"
    ]


async def test_filing_a_revision_needs_the_upload_scope(
    client: AsyncClient, token: str
) -> None:
    created = (
        await client.post(
            "/api/v1/items", headers=_auth(token), json={"data": {}}
        )
    ).json()
    read_only = str(
        (
            await client.post(
                "/api/me/tokens", json={"name": "ro", "scopes": ["search"]}
            )
        ).json()["plaintext"]
    )
    r = await client.put(
        f"/api/v1/items/{created['id']}/revision",
        headers=_auth(read_only),
        json={"body": "ACME", "designation": "ISO 1", "label": "2020"},
    )
    assert r.status_code == 403


async def test_a_bad_issue_date_is_rejected(
    client: AsyncClient, token: str
) -> None:
    created = (
        await client.post(
            "/api/v1/items", headers=_auth(token), json={"data": {}}
        )
    ).json()
    r = await client.put(
        f"/api/v1/items/{created['id']}/revision",
        headers=_auth(token),
        json={
            "body": "ACME",
            "designation": "ISO 1",
            "label": "2020",
            "issued_on": "October 2019",
        },
    )
    assert r.status_code == 400
