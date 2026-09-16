"""Looking a document up by something portable between instances.

Item ids differ per instance, so a profile authored against one and
pushed to another can't use them. Two lookups fill the gap: a standard by
its own identity, and anything else by the bytes of its file.
"""

import hashlib
from collections.abc import Iterator

import pytest
from httpx import AsyncClient
from obstore.store import MemoryStore

from shelf.config import settings
from shelf.services import storage

from .helpers import login


@pytest.fixture(autouse=True)
def memory_store(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Swap the S3 store for an in-memory one, as the other
    byte-handling suites do — these tests are about what gets hashed and
    recorded, not about reaching a bucket.

    Presigning is stubbed separately: a MemoryStore can't sign, and the
    URL is never fetched here.
    """
    storage._store = MemoryStore()

    async def fake_presign_upload(key: str, *_a: object, **_kw: object) -> str:
        return f"http://memory.invalid/{key}"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)
    yield
    storage.reset_store()

PDF = b"%PDF-1.7\nthe bytes\n"
PDF_SHA = hashlib.sha256(PDF).hexdigest()


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


# ── Standards: business identity ─────────────────────────────────────────


async def test_resolve_finds_the_named_edition(
    client: AsyncClient, token: str
) -> None:
    created = (
        await client.post(
            "/api/v1/items",
            headers=_auth(token),
            json={"item_type": "standard", "data": {"title": "Widgets"}},
        )
    ).json()
    await client.put(
        f"/api/v1/items/{created['id']}/revision",
        headers=_auth(token),
        json={
            "body": "NX Standards",
            "designation": "NX-ACME 1234",
            "label": "2015+A2:2020",
        },
    )

    r = await client.get(
        "/api/v1/standards/resolve",
        headers=_auth(token),
        params={
            "body": "NX Standards",
            "designation": "NX-ACME 1234",
            "label": "2015+A2:2020",
        },
    )
    assert r.status_code == 200, r.text
    assert [m["item_id"] for m in r.json()["items"]] == [created["id"]]


async def test_resolve_matches_case_insensitively(
    client: AsyncClient, token: str
) -> None:
    """Same rule as filing a revision, so a profile typed differently
    still finds what an earlier push created."""
    created = (
        await client.post(
            "/api/v1/items", headers=_auth(token), json={"item_type": "standard", "data": {}}
        )
    ).json()
    await client.put(
        f"/api/v1/items/{created['id']}/revision",
        headers=_auth(token),
        json={"body": "NX Standards", "designation": "NX-ACME 1234", "label": "2020"},
    )
    r = await client.get(
        "/api/v1/standards/resolve",
        headers=_auth(token),
        params={"body": "nx standards", "designation": "nx-acme 1234", "label": "2020"},
    )
    assert [m["item_id"] for m in r.json()["items"]] == [created["id"]]


async def test_an_unknown_edition_is_empty_not_404(
    client: AsyncClient, token: str
) -> None:
    """So a caller can tell "not here" from "no such route"."""
    r = await client.get(
        "/api/v1/standards/resolve",
        headers=_auth(token),
        params={"body": "NX Standards", "designation": "NX-ACME 9999", "label": "2020"},
    )
    assert r.status_code == 200
    assert r.json()["items"] == []


async def test_resolve_ignores_spaces_the_token_cannot_read(
    client: AsyncClient, token: str
) -> None:
    admin_id = (await client.get("/api/me")).json()["id"]
    hidden_slug = str(
        (
            await client.post("/api/spaces", json={"name": "Hidden", "slug": "hidden"})
        ).json()["slug"]
    )
    created = (
        await client.post(
            f"/api/spaces/{hidden_slug}/items",
            json={"item_type": "standard", "data": {}},
        )
    ).json()
    await client.put(
        f"/api/items/{created['id']}/revision",
        json={"body": "NX Standards", "designation": "NX-ACME 1234", "label": "2020"},
    )

    # A second user with their own token cannot see it.
    other_id = await login(client, "other@example.com", link=True)
    other_token = str(
        (
            await client.post(
                "/api/me/tokens", json={"name": "t", "scopes": ["search"]}
            )
        ).json()["plaintext"]
    )
    r = await client.get(
        "/api/v1/standards/resolve",
        headers=_auth(other_token),
        params={"body": "NX Standards", "designation": "NX-ACME 1234", "label": "2020"},
    )
    assert r.json()["items"] == []
    assert other_id != admin_id


# ── Attachments: content identity ────────────────────────────────────────


async def _upload(client: AsyncClient, token: str, name: str = "doc.pdf") -> dict:
    r = await client.post(
        "/api/v1/upload",
        headers=_auth(token),
        files={"file": (name, PDF, "application/pdf")},
        data={"title": "A report"},
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


async def test_the_proxied_upload_hashes_the_bytes(
    client: AsyncClient, token: str
) -> None:
    """This route is the one where the API actually holds the bytes, so
    the hash is computed rather than taken on trust."""
    body = await _upload(client, token)
    assert body["attachment"]["sha256"] == PDF_SHA


async def test_resolve_finds_an_item_by_its_file(
    client: AsyncClient, token: str
) -> None:
    body = await _upload(client, token)
    r = await client.get(
        "/api/v1/attachments/resolve",
        headers=_auth(token),
        params={"sha256": PDF_SHA},
    )
    assert r.status_code == 200, r.text
    found = r.json()["attachments"]
    assert [a["item_id"] for a in found] == [body["item"]["id"]]
    assert found[0]["filename"] == "doc.pdf"


async def test_resolve_accepts_an_uppercase_digest(
    client: AsyncClient, token: str
) -> None:
    await _upload(client, token)
    r = await client.get(
        "/api/v1/attachments/resolve",
        headers=_auth(token),
        params={"sha256": PDF_SHA.upper()},
    )
    assert len(r.json()["attachments"]) == 1


async def test_an_unknown_file_is_empty(client: AsyncClient, token: str) -> None:
    r = await client.get(
        "/api/v1/attachments/resolve",
        headers=_auth(token),
        params={"sha256": "0" * 64},
    )
    assert r.status_code == 200
    assert r.json()["attachments"] == []


async def test_a_malformed_digest_is_rejected(
    client: AsyncClient, token: str
) -> None:
    r = await client.get(
        "/api/v1/attachments/resolve",
        headers=_auth(token),
        params={"sha256": "not-a-hash"},
    )
    assert r.status_code == 422


async def test_the_same_file_in_two_spaces_reports_both(
    client: AsyncClient, token: str
) -> None:
    """Copying an item puts it there on purpose, so a caller has to be
    told about both rather than handed whichever came back first."""
    body = await _upload(client, token)
    target = str(
        (
            await client.post("/api/spaces", json={"name": "Other", "slug": "other"})
        ).json()["slug"]
    )
    copied = await client.post(
        f"/api/items/{body['item']['id']}/copy", json={"target_slug": target}
    )
    assert copied.status_code == 201, copied.text

    r = await client.get(
        "/api/v1/attachments/resolve",
        headers=_auth(token),
        params={"sha256": PDF_SHA},
    )
    assert len(r.json()["attachments"]) == 2


async def test_narrowing_to_a_space_disambiguates(
    client: AsyncClient, token: str
) -> None:
    body = await _upload(client, token)
    target = str(
        (
            await client.post("/api/spaces", json={"name": "Other", "slug": "other"})
        ).json()["slug"]
    )
    await client.post(
        f"/api/items/{body['item']['id']}/copy", json={"target_slug": target}
    )

    r = await client.get(
        "/api/v1/attachments/resolve",
        headers=_auth(token),
        params={"sha256": PDF_SHA, "space": target},
    )
    found = r.json()["attachments"]
    assert len(found) == 1
    assert found[0]["item_id"] != body["item"]["id"]


async def test_resolve_respects_the_tokens_space_allow_list(
    client: AsyncClient, token: str
) -> None:
    """A narrowed token must not learn about files outside its scope."""
    await _upload(client, token)
    other_slug = str(
        (
            await client.post("/api/spaces", json={"name": "Other", "slug": "other"})
        ).json()["slug"]
    )
    other_id = str(
        next(
            s["id"]
            for s in (await client.get("/api/me/spaces")).json()
            if s["slug"] == other_slug
        )
    )
    narrow = str(
        (
            await client.post(
                "/api/me/tokens",
                json={
                    "name": "narrow",
                    "scopes": ["search"],
                    "allowed_space_ids": [other_id],
                },
            )
        ).json()["plaintext"]
    )
    r = await client.get(
        "/api/v1/attachments/resolve",
        headers=_auth(narrow),
        params={"sha256": PDF_SHA},
    )
    assert r.json()["attachments"] == []


async def test_the_presigned_path_accepts_a_declared_hash(
    client: AsyncClient, token: str
) -> None:
    """Taken on trust — the bytes never reach the API on that route —
    but enough to answer "have I pushed this already"."""
    r = await client.post(
        "/api/v1/uploads/register",
        headers=_auth(token),
        json={
            "filename": "later.pdf",
            "content_type": "application/pdf",
            "size_bytes": len(PDF),
            "title": "Pending",
            "sha256": PDF_SHA,
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["attachment"]["sha256"] == PDF_SHA


async def test_a_declared_hash_must_look_like_one(
    client: AsyncClient, token: str
) -> None:
    r = await client.post(
        "/api/v1/uploads/register",
        headers=_auth(token),
        json={
            "filename": "x.pdf",
            "content_type": "application/pdf",
            "title": "x",
            "sha256": "nope",
        },
    )
    assert r.status_code == 422
