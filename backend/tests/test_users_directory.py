"""The user directory behind the member picker — /api/users.

Visible to every authenticated user rather than admins only, because
everyone owns their personal space and so everyone may need to share one.
That means any account can enumerate the others; these tests pin both the
access rule and the fact that nothing beyond a name and address is
exposed.
"""

from httpx import AsyncClient

from .helpers import login


async def _register(client: AsyncClient, *emails: str) -> None:
    """Give each address an account, leaving the client signed in as the
    last one."""
    for i, email in enumerate(emails):
        await login(client, email, link=i > 0)


async def test_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/users")).status_code == 401


async def test_lists_everyone(client: AsyncClient) -> None:
    await _register(client, "ada@example.com", "grace@example.com")
    rows = (await client.get("/api/users")).json()
    assert {r["email"] for r in rows} == {"ada@example.com", "grace@example.com"}


async def test_a_plain_user_can_read_it(client: AsyncClient) -> None:
    """Not admin-gated: a non-admin owns their personal space and may
    want to share it."""
    await _register(client, "ada@example.com", "nobody@example.com")
    r = await client.get("/api/users")
    assert r.status_code == 200
    assert len(r.json()) == 2


async def test_exposes_only_name_and_address(client: AsyncClient) -> None:
    """Never roles, identities, or anything about someone's library."""
    await _register(client, "ada@example.com")
    row = (await client.get("/api/users")).json()[0]
    assert set(row) == {"id", "email", "display_name"}


async def test_sorted_by_display_name(client: AsyncClient) -> None:
    """The picker renders in the order it receives, so the order is the
    API's responsibility."""
    await _register(
        client, "zoe@example.com", "ada@example.com", "mel@example.com"
    )
    names = [r["display_name"] for r in (await client.get("/api/users")).json()]
    assert names == ["ada", "mel", "zoe"]


async def test_sort_ignores_case(client: AsyncClient) -> None:
    """Under a C-collation database a plain ORDER BY puts "Zoe" before
    "ada", which reads as unsorted in a dropdown of people's names."""
    await login(client, "zoe@example.com", display_name="Zoe")
    await login(client, "ada@example.com", display_name="ada", link=True)
    await login(client, "Mel@example.com", display_name="Mel", link=True)

    names = [r["display_name"] for r in (await client.get("/api/users")).json()]
    assert names == ["ada", "Mel", "Zoe"]


async def test_filters_on_name_or_email(client: AsyncClient) -> None:
    await _register(client, "ada@example.com", "grace@example.com")

    by_name = (await client.get("/api/users", params={"q": "grac"})).json()
    assert [r["email"] for r in by_name] == ["grace@example.com"]

    by_email = (await client.get("/api/users", params={"q": "ada@"})).json()
    assert [r["email"] for r in by_email] == ["ada@example.com"]


async def test_filter_is_case_insensitive(client: AsyncClient) -> None:
    await _register(client, "ada@example.com")
    assert len((await client.get("/api/users", params={"q": "ADA"})).json()) == 1


async def test_wildcards_are_literal(client: AsyncClient) -> None:
    """A typed % should match a literal percent, not everyone."""
    await _register(client, "ada@example.com", "grace@example.com")
    assert (await client.get("/api/users", params={"q": "%"})).json() == []


async def test_blank_query_is_ignored(client: AsyncClient) -> None:
    await _register(client, "ada@example.com", "grace@example.com")
    assert len((await client.get("/api/users", params={"q": "   "})).json()) == 2


async def test_limit_is_honoured_and_bounded(client: AsyncClient) -> None:
    await _register(client, "ada@example.com", "grace@example.com")
    assert len((await client.get("/api/users", params={"limit": 1})).json()) == 1
    assert (
        await client.get("/api/users", params={"limit": 0})
    ).status_code == 422
    assert (
        await client.get("/api/users", params={"limit": 501})
    ).status_code == 422


async def test_a_picked_user_can_actually_be_added(client: AsyncClient) -> None:
    """The directory and the add-member endpoint agree: the email listed
    is the one that resolves."""
    ada = await login(client, "ada@example.com")
    slug = (await client.get("/api/me/spaces")).json()[0]["slug"]
    await login(client, "grace@example.com", link=True)
    await client.post("/auth/switch", json={"user_id": ada})

    grace = next(
        r
        for r in (await client.get("/api/users")).json()
        if r["email"] == "grace@example.com"
    )
    r = await client.post(
        f"/api/spaces/{slug}/members",
        json={"email": grace["email"], "role": "editor"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["user_id"] == grace["id"]
