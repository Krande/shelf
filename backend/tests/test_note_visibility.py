"""Private and shared notes, and the same rule for PDF highlights.

The case that forced this: a standard lives in one shared space and is
read by everyone. "Everyone who can read the PDF" is then the whole
company, so a working note to yourself must not go to all of them — and
sharing one deliberately has to reach the space that owns the document,
not the personal shelf you happened to read it from.
"""

import pytest
from httpx import AsyncClient

from shelf.config import settings

from .helpers import login


async def _personal_slug(client: AsyncClient) -> str:
    return str((await client.get("/api/me/spaces")).json()[0]["slug"])


@pytest.fixture
async def shared_standard(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str, str, str]:
    """A subscribable Standards space with one item, and two engineers
    who have each subscribed their personal shelf to it.

    Returns (admin_id, item_id, eng_a_id, eng_b_id), client left
    authenticated as engineer A.
    """
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    admin_id = await login(client, "admin@example.com")
    r = await client.post(
        "/api/spaces", json={"name": "Standards", "slug": "standards"}
    )
    std_slug = str(r.json()["slug"])
    created = await client.post(
        f"/api/spaces/{std_slug}/items",
        json={"item_type": "standard", "data": {"title": "ACME 1234"}},
    )
    item_id = str(created.json()["id"])
    await client.patch(
        f"/api/spaces/{std_slug}/settings", json={"subscribable": True}
    )

    ids = {}
    for email in ("a@example.com", "b@example.com"):
        ids[email] = await login(client, email, link=True)
        personal = await _personal_slug(client)
        sub = await client.post(
            f"/api/spaces/{personal}/inherits", json={"parent_slug": std_slug}
        )
        assert sub.status_code == 201, sub.text

    await client.post("/auth/switch", json={"user_id": ids["a@example.com"]})
    return admin_id, item_id, ids["a@example.com"], ids["b@example.com"]


# ── Notes on an inherited item ───────────────────────────────────────────────


async def test_a_note_on_an_inherited_item_starts_private(
    client: AsyncClient, shared_standard: tuple[str, str, str, str]
) -> None:
    _admin, item_id, _a, _b = shared_standard
    r = await client.post(
        f"/api/items/{item_id}/notes", json={"content_html": "<p>mine</p>"}
    )
    assert r.status_code == 201, r.text
    assert r.json()["visibility"] == "private"
    assert r.json()["is_mine"] is True


async def test_a_private_note_is_invisible_to_the_other_subscribers(
    client: AsyncClient, shared_standard: tuple[str, str, str, str]
) -> None:
    _admin, item_id, _a, b_id = shared_standard
    created = await client.post(
        f"/api/items/{item_id}/notes", json={"content_html": "<p>mine</p>"}
    )
    note_id = created.json()["id"]

    await client.post("/auth/switch", json={"user_id": b_id})
    assert (await client.get(f"/api/items/{item_id}/notes")).json() == []
    # And not reachable by id either — 404, not 403: it isn't theirs to
    # know exists.
    assert (
        await client.put(
            f"/api/notes/{note_id}", json={"content_html": "<p>x</p>"}
        )
    ).status_code == 404


async def test_the_owner_of_the_standards_space_cannot_read_it_either(
    client: AsyncClient, shared_standard: tuple[str, str, str, str]
) -> None:
    """Owning the document does not mean owning the marginalia."""
    admin_id, item_id, _a, _b = shared_standard
    await client.post(
        f"/api/items/{item_id}/notes", json={"content_html": "<p>mine</p>"}
    )
    await client.post("/auth/switch", json={"user_id": admin_id})
    assert (await client.get(f"/api/items/{item_id}/notes")).json() == []


async def test_sharing_publishes_to_the_space_that_owns_the_item(
    client: AsyncClient, shared_standard: tuple[str, str, str, str]
) -> None:
    """The point of the feature: share, and the *other subscribers* see
    it — not just whoever can read your personal shelf."""
    admin_id, item_id, _a, b_id = shared_standard
    created = await client.post(
        f"/api/items/{item_id}/notes", json={"content_html": "<p>for all</p>"}
    )
    note_id = created.json()["id"]

    shared = await client.patch(
        f"/api/notes/{note_id}/visibility", json={"visibility": "space"}
    )
    assert shared.status_code == 200, shared.text
    assert shared.json()["visibility"] == "space"

    # Another subscriber sees it.
    await client.post("/auth/switch", json={"user_id": b_id})
    notes = (await client.get(f"/api/items/{item_id}/notes")).json()
    assert [n["content_html"] for n in notes] == ["<p>for all</p>"]
    assert notes[0]["is_mine"] is False

    # As does the space's owner.
    await client.post("/auth/switch", json={"user_id": admin_id})
    assert len((await client.get(f"/api/items/{item_id}/notes")).json()) == 1


async def test_unsharing_takes_it_back(
    client: AsyncClient, shared_standard: tuple[str, str, str, str]
) -> None:
    _admin, item_id, _a, b_id = shared_standard
    created = await client.post(
        f"/api/items/{item_id}/notes", json={"content_html": "<p>oops</p>"}
    )
    note_id = created.json()["id"]
    await client.patch(
        f"/api/notes/{note_id}/visibility", json={"visibility": "space"}
    )
    await client.patch(
        f"/api/notes/{note_id}/visibility", json={"visibility": "private"}
    )

    await client.post("/auth/switch", json={"user_id": b_id})
    assert (await client.get(f"/api/items/{item_id}/notes")).json() == []


async def test_only_the_author_can_share_a_note(
    client: AsyncClient, shared_standard: tuple[str, str, str, str]
) -> None:
    """Sharing is publishing someone's writing; nobody else gets to."""
    admin_id, item_id, _a, _b = shared_standard
    created = await client.post(
        f"/api/items/{item_id}/notes", json={"content_html": "<p>mine</p>"}
    )
    note_id = created.json()["id"]
    await client.patch(
        f"/api/notes/{note_id}/visibility", json={"visibility": "space"}
    )

    # The admin owns the space and can read the note — but not retract it.
    await client.post("/auth/switch", json={"user_id": admin_id})
    r = await client.patch(
        f"/api/notes/{note_id}/visibility", json={"visibility": "private"}
    )
    assert r.status_code == 403


async def test_another_subscriber_cannot_edit_a_shared_note(
    client: AsyncClient, shared_standard: tuple[str, str, str, str]
) -> None:
    _admin, item_id, _a, b_id = shared_standard
    created = await client.post(
        f"/api/items/{item_id}/notes", json={"content_html": "<p>mine</p>"}
    )
    note_id = created.json()["id"]
    await client.patch(
        f"/api/notes/{note_id}/visibility", json={"visibility": "space"}
    )

    await client.post("/auth/switch", json={"user_id": b_id})
    assert (
        await client.put(
            f"/api/notes/{note_id}", json={"content_html": "<p>edited</p>"}
        )
    ).status_code == 403
    assert (await client.delete(f"/api/notes/{note_id}")).status_code == 403


async def test_the_author_can_edit_and_delete_their_own(
    client: AsyncClient, shared_standard: tuple[str, str, str, str]
) -> None:
    _admin, item_id, _a, _b = shared_standard
    created = await client.post(
        f"/api/items/{item_id}/notes", json={"content_html": "<p>mine</p>"}
    )
    note_id = created.json()["id"]
    edited = await client.put(
        f"/api/notes/{note_id}", json={"content_html": "<p>better</p>"}
    )
    assert edited.status_code == 200
    assert edited.json()["content_text"] == "better"
    assert (await client.delete(f"/api/notes/{note_id}")).status_code == 204


async def test_a_note_can_be_shared_at_creation(
    client: AsyncClient, shared_standard: tuple[str, str, str, str]
) -> None:
    _admin, item_id, _a, b_id = shared_standard
    r = await client.post(
        f"/api/items/{item_id}/notes",
        json={"content_html": "<p>hello</p>", "visibility": "space"},
    )
    assert r.json()["visibility"] == "space"
    await client.post("/auth/switch", json={"user_id": b_id})
    assert len((await client.get(f"/api/items/{item_id}/notes")).json()) == 1


# ── Notes in an ordinary space keep their old behaviour ──────────────────────


async def test_notes_in_a_space_you_belong_to_stay_shared(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only inherited items default to private. Flipping team libraries
    to private would hide notes colleagues rely on."""
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    admin_id = await login(client, "admin@example.com")
    r = await client.post("/api/spaces", json={"name": "Team", "slug": "team"})
    slug = str(r.json()["slug"])
    created = await client.post(
        f"/api/spaces/{slug}/items",
        json={"item_type": "report", "data": {"title": "Q3"}},
    )
    item_id = created.json()["id"]

    mate_id = await login(client, "mate@example.com", link=True)
    await client.post("/auth/switch", json={"user_id": admin_id})
    await client.post(
        f"/api/spaces/{slug}/members",
        json={"email": "mate@example.com", "role": "editor"},
    )

    note = await client.post(
        f"/api/items/{item_id}/notes", json={"content_html": "<p>team note</p>"}
    )
    assert note.json()["visibility"] == "space"

    await client.post("/auth/switch", json={"user_id": mate_id})
    assert len((await client.get(f"/api/items/{item_id}/notes")).json()) == 1


# ── Annotations follow the same rule ─────────────────────────────────────────


async def _attachment_on(client: AsyncClient, item_id: str) -> str:
    """Register an attachment row. No bytes — annotations only need the
    row, and the upload path is exercised elsewhere."""
    r = await client.post(
        f"/api/items/{item_id}/attachments",
        json={
            "filename": "standard.pdf",
            "content_type": "application/pdf",
            "size_bytes": 1024,
        },
    )
    assert r.status_code in (200, 201), r.text
    return str(r.json()["attachment"]["id"])


async def test_a_highlight_on_an_inherited_pdf_starts_private(
    client: AsyncClient, shared_standard: tuple[str, str, str, str]
) -> None:
    admin_id, item_id, a_id, b_id = shared_standard
    await client.post("/auth/switch", json={"user_id": admin_id})
    att_id = await _attachment_on(client, item_id)

    await client.post("/auth/switch", json={"user_id": a_id})
    r = await client.post(
        f"/api/attachments/{att_id}/annotations",
        json={
            "kind": "highlight",
            "page_number": 4,
            "rects": [[10, 10, 100, 12]],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["visibility"] == "private"

    await client.post("/auth/switch", json={"user_id": b_id})
    assert (await client.get(f"/api/attachments/{att_id}/annotations")).json() == []


async def test_a_highlight_can_be_shared(
    client: AsyncClient, shared_standard: tuple[str, str, str, str]
) -> None:
    admin_id, item_id, a_id, b_id = shared_standard
    await client.post("/auth/switch", json={"user_id": admin_id})
    att_id = await _attachment_on(client, item_id)

    await client.post("/auth/switch", json={"user_id": a_id})
    ann_id = (
        await client.post(
            f"/api/attachments/{att_id}/annotations",
            json={
                "kind": "highlight",
                "page_number": 4,
                "rects": [[10, 10, 100, 12]],
                "text": "load combinations",
            },
        )
    ).json()["id"]
    shared = await client.patch(
        f"/api/annotations/{ann_id}/visibility", json={"visibility": "space"}
    )
    assert shared.status_code == 200, shared.text

    await client.post("/auth/switch", json={"user_id": b_id})
    got = (await client.get(f"/api/attachments/{att_id}/annotations")).json()
    assert [a["text"] for a in got] == ["load combinations"]
    assert got[0]["is_mine"] is False


async def test_another_subscriber_cannot_change_a_shared_highlight(
    client: AsyncClient, shared_standard: tuple[str, str, str, str]
) -> None:
    admin_id, item_id, a_id, b_id = shared_standard
    await client.post("/auth/switch", json={"user_id": admin_id})
    att_id = await _attachment_on(client, item_id)

    await client.post("/auth/switch", json={"user_id": a_id})
    ann_id = (
        await client.post(
            f"/api/attachments/{att_id}/annotations",
            json={
                "kind": "highlight",
                "page_number": 1,
                "rects": [[1, 1, 2, 2]],
            },
        )
    ).json()["id"]
    await client.patch(
        f"/api/annotations/{ann_id}/visibility", json={"visibility": "space"}
    )

    await client.post("/auth/switch", json={"user_id": b_id})
    assert (
        await client.patch(
            f"/api/annotations/{ann_id}", json={"color": "#ff0000"}
        )
    ).status_code == 403
    assert (
        await client.delete(f"/api/annotations/{ann_id}")
    ).status_code == 403
