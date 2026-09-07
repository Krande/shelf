"""Integration tests for per-item rich-text notes."""

from httpx import AsyncClient

from shelf.api.notes import html_to_text


async def _login(client: AsyncClient, email: str = "alice@example.com") -> str:
    r = await client.post("/auth/dev-login", json={"email": email})
    assert r.status_code == 200, r.text
    me = await client.get("/api/me")
    user_id = me.json()["id"]
    return f"u-{user_id.replace('-', '')[:8]}"


async def _make_item(client: AsyncClient, slug: str) -> dict:
    r = await client.post(
        f"/api/spaces/{slug}/items",
        json={"item_type": "document", "data": {"title": "Paper"}},
    )
    assert r.status_code == 201
    return r.json()


def test_html_to_text_strips_tags_and_decodes_entities() -> None:
    assert (
        html_to_text("<p>Hello <strong>world</strong>!</p>")
        == "Hello world!"
    )
    # Block-level closers become newlines so list items separate.
    assert (
        html_to_text("<ul><li>One</li><li>Two</li></ul>") == "One\nTwo"
    )
    # Entities decode.
    assert html_to_text("<p>Caf&eacute;</p>") == "Café"
    # Empty input safe.
    assert html_to_text("") == ""


async def test_create_and_list_notes(client: AsyncClient) -> None:
    slug = await _login(client)
    item = await _make_item(client, slug)

    r = await client.post(
        f"/api/items/{item['id']}/notes",
        json={"content_html": "<p>First note</p>"},
    )
    assert r.status_code == 201, r.text
    note = r.json()
    assert note["item_id"] == item["id"]
    assert note["content_html"] == "<p>First note</p>"
    assert note["content_text"] == "First note"

    rows = (await client.get(f"/api/items/{item['id']}/notes")).json()
    assert [n["id"] for n in rows] == [note["id"]]


async def test_update_note_replaces_content_and_text(client: AsyncClient) -> None:
    slug = await _login(client)
    item = await _make_item(client, slug)
    note = (
        await client.post(
            f"/api/items/{item['id']}/notes",
            json={"content_html": "<p>Old</p>"},
        )
    ).json()
    r = await client.put(
        f"/api/notes/{note['id']}",
        json={"content_html": "<p><strong>New</strong> content</p>"},
    )
    assert r.status_code == 200
    out = r.json()
    assert out["content_html"] == "<p><strong>New</strong> content</p>"
    assert out["content_text"] == "New content"
    # updated_at moved forward.
    assert out["updated_at"] >= note["updated_at"]


async def test_delete_note(client: AsyncClient) -> None:
    slug = await _login(client)
    item = await _make_item(client, slug)
    note = (
        await client.post(
            f"/api/items/{item['id']}/notes",
            json={"content_html": "<p>Bye</p>"},
        )
    ).json()
    r = await client.delete(f"/api/notes/{note['id']}")
    assert r.status_code == 204
    rows = (await client.get(f"/api/items/{item['id']}/notes")).json()
    assert rows == []


async def test_notes_isolated_between_users(client: AsyncClient) -> None:
    a_slug = await _login(client, email="alice@example.com")
    a_item = await _make_item(client, a_slug)
    a_note = (
        await client.post(
            f"/api/items/{a_item['id']}/notes",
            json={"content_html": "<p>private</p>"},
        )
    ).json()

    await _login(client, email="bob@example.com")
    # Bob can't list notes on Alice's item.
    r = await client.get(f"/api/items/{a_item['id']}/notes")
    assert r.status_code == 404
    # Bob can't update or delete Alice's note.
    r = await client.put(
        f"/api/notes/{a_note['id']}",
        json={"content_html": "<p>edit</p>"},
    )
    assert r.status_code == 404
    r = await client.delete(f"/api/notes/{a_note['id']}")
    assert r.status_code == 404


async def test_deleting_item_cascades_notes(client: AsyncClient) -> None:
    slug = await _login(client)
    item = await _make_item(client, slug)
    note = (
        await client.post(
            f"/api/items/{item['id']}/notes",
            json={"content_html": "<p>x</p>"},
        )
    ).json()
    # Trash, then permanent delete — cascades through the FK.
    r = await client.delete(f"/api/items/{item['id']}")
    assert r.status_code == 204
    r = await client.delete(
        f"/api/items/{item['id']}", params={"permanent": "true"}
    )
    assert r.status_code == 204
    r = await client.put(
        f"/api/notes/{note['id']}", json={"content_html": "<p>y</p>"}
    )
    assert r.status_code == 404
