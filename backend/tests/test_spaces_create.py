"""Creating shared spaces.

Until this existed the only space anyone had was the personal one minted
at first login, so there was no way to make one to share.
"""

import pytest
from httpx import AsyncClient

from shelf.api.spaces import slugify
from shelf.config import settings

from .helpers import login


@pytest.fixture
def admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "admin_emails", ["boss@example.com"])


# ── slugify ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Engineering", "engineering"),
        ("Client Contracts", "client-contracts"),
        ("  Spaced  Out  ", "spaced-out"),
        ("Already-Hyphenated", "already-hyphenated"),
        ("Symbols!@#$%Here", "symbols-here"),
        # Accents fold rather than vanish - "Rsums" would be unreadable.
        ("Résumés", "resumes"),
        ("2026 Reports", "2026-reports"),
    ],
)
def test_slugify(name: str, expected: str) -> None:
    assert slugify(name) == expected


def test_slugify_gives_up_on_unusable_names() -> None:
    """Nothing ASCII-safe left; the endpoint asks for an explicit slug
    rather than inventing one."""
    assert slugify("!!!") == ""
    assert slugify("日本語") == ""


# ── Permissions ──────────────────────────────────────────────────────────────


async def test_non_admin_cannot_create(client: AsyncClient) -> None:
    await login(client, "someone@example.com")
    r = await client.post("/api/spaces", json={"name": "Engineering"})
    assert r.status_code == 403


async def test_anonymous_cannot_create(client: AsyncClient) -> None:
    r = await client.post("/api/spaces", json={"name": "Engineering"})
    assert r.status_code == 401


# ── Creating ─────────────────────────────────────────────────────────────────


async def test_admin_creates_a_space_and_owns_it(
    client: AsyncClient, admin: None
) -> None:
    await login(client, "boss@example.com")
    r = await client.post("/api/spaces", json={"name": "Engineering"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["slug"] == "engineering"
    assert body["name"] == "Engineering"
    assert body["role"] == "owner"
    assert body["is_owner"] is True
    assert body["is_personal"] is False

    # It shows up in their listing, alongside the personal one.
    slugs = {s["slug"]: s for s in (await client.get("/api/me/spaces")).json()}
    assert slugs["engineering"]["role"] == "owner"
    assert slugs["engineering"]["is_personal"] is False


async def test_the_new_space_is_usable_immediately(
    client: AsyncClient, admin: None
) -> None:
    await login(client, "boss@example.com")
    await client.post("/api/spaces", json={"name": "Engineering"})

    created = await client.post(
        "/api/spaces/engineering/items",
        json={"item_type": "document", "data": {"title": "Spec"}},
    )
    assert created.status_code == 201, created.text


async def test_creator_can_share_it_straight_away(
    client: AsyncClient, admin: None
) -> None:
    """The point of the feature: make a space, then choose who is in it
    and at what level."""
    boss = await login(client, "boss@example.com")
    await login(client, "mate@example.com", link=True)
    await client.post("/auth/switch", json={"user_id": boss})
    await client.post("/api/spaces", json={"name": "Engineering"})

    r = await client.post(
        "/api/spaces/engineering/members",
        json={"email": "mate@example.com", "role": "editor"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "editor"

    members = (await client.get("/api/spaces/engineering/members")).json()
    assert {m["email"] for m in members} == {
        "boss@example.com",
        "mate@example.com",
    }


async def test_an_explicit_slug_is_honoured(
    client: AsyncClient, admin: None
) -> None:
    await login(client, "boss@example.com")
    r = await client.post(
        "/api/spaces", json={"name": "Engineering", "slug": "eng"}
    )
    assert r.status_code == 201
    assert r.json()["slug"] == "eng"


# ── Rejections ───────────────────────────────────────────────────────────────


async def test_duplicate_slug_is_refused(
    client: AsyncClient, admin: None
) -> None:
    await login(client, "boss@example.com")
    assert (
        await client.post("/api/spaces", json={"name": "Engineering"})
    ).status_code == 201
    r = await client.post("/api/spaces", json={"name": "Engineering"})
    assert r.status_code == 409
    assert "engineering" in r.json()["detail"]


async def test_personal_prefix_is_reserved(
    client: AsyncClient, admin: None
) -> None:
    """`u-` marks a personal space; a hand-named one must not be able to
    impersonate it."""
    await login(client, "boss@example.com")
    r = await client.post(
        "/api/spaces", json={"name": "Sneaky", "slug": "u-deadbeef"}
    )
    assert r.status_code == 400
    assert "reserved" in r.json()["detail"]


@pytest.mark.parametrize(
    "slug", ["Has Spaces", "trailing-", "-leading", "has_underscore", "dots.here"]
)
async def test_malformed_slug_is_refused(
    client: AsyncClient, admin: None, slug: str
) -> None:
    await login(client, "boss@example.com")
    r = await client.post("/api/spaces", json={"name": "Nope", "slug": slug})
    assert r.status_code == 400, f"{slug!r} should be refused"


async def test_an_uppercase_slug_is_normalised_not_refused(
    client: AsyncClient, admin: None
) -> None:
    """Case is the one thing worth fixing up silently — there's exactly
    one thing the caller can have meant."""
    await login(client, "boss@example.com")
    r = await client.post("/api/spaces", json={"name": "Nope", "slug": "ENG"})
    assert r.status_code == 201, r.text
    assert r.json()["slug"] == "eng"


async def test_unsluggable_name_asks_for_one(
    client: AsyncClient, admin: None
) -> None:
    await login(client, "boss@example.com")
    r = await client.post("/api/spaces", json={"name": "日本語"})
    assert r.status_code == 400
    assert "explicitly" in r.json()["detail"]


async def test_blank_name_is_refused(client: AsyncClient, admin: None) -> None:
    await login(client, "boss@example.com")
    assert (
        await client.post("/api/spaces", json={"name": "   "})
    ).status_code == 400
    # Pydantic catches the empty string before the handler does.
    assert (await client.post("/api/spaces", json={"name": ""})).status_code == 422


async def test_creating_grants_no_access_to_other_spaces(
    client: AsyncClient, admin: None
) -> None:
    """Being an admin who can make spaces still confers nothing over
    spaces other people own."""
    other = await login(client, "other@example.com")
    other_slug = (await client.get("/api/me/spaces")).json()[0]["slug"]

    await login(client, "boss@example.com", link=True)
    await client.post("/api/spaces", json={"name": "Engineering"})
    assert (await client.get(f"/api/spaces/{other_slug}/items")).status_code == 404
    assert other  # the space under test belongs to them
