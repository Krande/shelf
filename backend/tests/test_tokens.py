"""Token lifecycle + /api/v1/* enforcement.

Presign URLs aren't asserted because the local test stack uses the
real S3 store — the upload/download paths are exercised against the
deployed Garage in the live stack."""

import pytest
from httpx import AsyncClient
from obstore.store import MemoryStore

from shelf.services import storage


@pytest.fixture(autouse=True)
def memory_store() -> None:
    storage._store = MemoryStore()
    yield
    storage.reset_store()


async def _login(client: AsyncClient, email: str = "alice@example.com") -> str:
    r = await client.post("/auth/dev-login", json={"email": email})
    assert r.status_code == 200, r.text
    me = await client.get("/api/me")
    return f"u-{me.json()['id'].replace('-', '')[:8]}"


async def _make_token(
    client: AsyncClient, **overrides: object
) -> tuple[str, dict]:
    payload = {"name": "test", "scopes": ["upload", "search", "download"]}
    payload.update(overrides)
    r = await client.post("/api/me/tokens", json=payload)
    assert r.status_code == 201, r.text
    body = r.json()
    return body["plaintext"], body


# ── management ────────────────────────────────────────────────────────────


async def test_create_lists_and_revokes_token(client: AsyncClient) -> None:
    await _login(client)
    plaintext, body = await _make_token(client)
    assert plaintext.startswith("shelf_")
    assert body["prefix"] == plaintext[:12]
    token_id = body["id"]

    listing = (await client.get("/api/me/tokens")).json()
    assert [t["id"] for t in listing] == [token_id]
    # Plaintext is *not* present on subsequent reads.
    assert "plaintext" not in listing[0]

    r = await client.delete(f"/api/me/tokens/{token_id}")
    assert r.status_code == 204
    listing = (await client.get("/api/me/tokens")).json()
    assert listing == []


async def test_create_token_rejects_unknown_scope(client: AsyncClient) -> None:
    await _login(client)
    r = await client.post(
        "/api/me/tokens",
        json={"name": "x", "scopes": ["upload", "delete"]},
    )
    # Pydantic rejects unknown literal values at the body-validation
    # layer (422); the explicit 400 path in the route only fires when
    # the literal matches the type but the value is an empty list.
    assert r.status_code == 422


async def test_create_token_rejects_collection_owned_by_other(
    client: AsyncClient,
) -> None:
    await _login(client, "alice@example.com")
    alice_slug = f"u-{(await client.get('/api/me')).json()['id'].replace('-', '')[:8]}"
    coll = (
        await client.post(
            f"/api/spaces/{alice_slug}/collections", json={"name": "C"}
        )
    ).json()

    await client.post("/auth/logout")
    await _login(client, "mallory@example.com")
    r = await client.post(
        "/api/me/tokens",
        json={
            "name": "x",
            "scopes": ["upload"],
            "allowed_collection_ids": [coll["id"]],
        },
    )
    assert r.status_code == 400


# ── bearer auth ───────────────────────────────────────────────────────────


async def test_v1_search_requires_bearer(client: AsyncClient) -> None:
    r = await client.get("/api/v1/search")
    assert r.status_code == 401


async def test_v1_rejects_garbage_bearer(client: AsyncClient) -> None:
    r = await client.get(
        "/api/v1/search",
        headers={"Authorization": "Bearer not-a-token"},
    )
    assert r.status_code == 401


async def test_v1_rejects_missing_scope(client: AsyncClient) -> None:
    await _login(client)
    plaintext, _ = await _make_token(client, scopes=["search"])
    # Drop the cookie to make sure auth is via the bearer alone.
    client.cookies.clear()
    r = await client.post(
        "/api/v1/upload",
        headers={"Authorization": f"Bearer {plaintext}"},
        data={"item_id": "00000000-0000-0000-0000-000000000000"},
        files={"file": ("a.txt", b"x", "text/plain")},
    )
    assert r.status_code == 403, r.text


# ── list attachments ──────────────────────────────────────────────────────


async def test_v1_list_attachments_returns_uploaded_files(
    client: AsyncClient,
) -> None:
    """``GET /api/v1/items/{id}/attachments`` returns every attachment
    on the resolved item, in the order they were created — pairs with
    the ``download`` endpoint so token-driven automation can fetch all
    of an item's files without going through the SPA."""
    await _login(client)
    plaintext, _ = await _make_token(client)
    client.cookies.clear()
    # Seed two attachments on the same auto-created item.
    r1 = await client.post(
        "/api/v1/upload",
        headers={"Authorization": f"Bearer {plaintext}"},
        data={"title": "Pair"},
        files={"file": ("a.pdf", b"%PDF-1\nA", "application/pdf")},
    )
    assert r1.status_code == 201
    item_id = r1.json()["item"]["id"]
    a1_id = r1.json()["attachment"]["id"]
    r2 = await client.post(
        "/api/v1/upload",
        headers={"Authorization": f"Bearer {plaintext}"},
        data={"item_id": item_id},
        files={"file": ("b.pdf", b"%PDF-1\nB", "application/pdf")},
    )
    assert r2.status_code == 201
    a2_id = r2.json()["attachment"]["id"]

    r = await client.get(
        f"/api/v1/items/{item_id}/attachments",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, list)
    ids = [a["id"] for a in body]
    assert ids == [a1_id, a2_id], "ordering should be created_at ASC"
    assert {a["filename"] for a in body} == {"a.pdf", "b.pdf"}
    # AttachmentSummary shape — caller can drive /download directly.
    assert all(set(a.keys()) >= {"id", "item_id", "filename", "content_type"} for a in body)


async def test_v1_list_attachments_requires_bearer(client: AsyncClient) -> None:
    r = await client.get(
        "/api/v1/items/00000000-0000-0000-0000-000000000000/attachments"
    )
    assert r.status_code == 401


async def test_v1_list_attachments_other_users_item_404s(
    client: AsyncClient,
) -> None:
    """Token belongs to user A; item belongs to user B → 404 (not 403)
    so we don't leak existence of items in other spaces."""
    # User A's token
    await _login(client)
    plaintext_a, _ = await _make_token(client)
    client.cookies.clear()
    # User B creates an item via cookie auth.
    slug = await _login(client, email="b@example.com")
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "Mine"}},
        )
    ).json()
    client.cookies.clear()

    r = await client.get(
        f"/api/v1/items/{item['id']}/attachments",
        headers={"Authorization": f"Bearer {plaintext_a}"},
    )
    assert r.status_code == 404


# ── upload ────────────────────────────────────────────────────────────────


async def test_v1_upload_creates_attachment(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = await _login(client)
    plaintext, _ = await _make_token(client)
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "T"}},
        )
    ).json()

    client.cookies.clear()
    r = await client.post(
        "/api/v1/upload",
        headers={"Authorization": f"Bearer {plaintext}"},
        data={"item_id": item["id"]},
        files={"file": ("paper.pdf", b"%PDF-1.4\nfake", "application/pdf")},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    att = body["attachment"]
    assert att["filename"] == "paper.pdf"
    assert att["content_type"] == "application/pdf"
    assert att["size_bytes"] == len(b"%PDF-1.4\nfake")
    assert att["item_id"] == item["id"]
    # Attaching to an existing item: no new item synthesised.
    assert body["item"] is None


async def test_v1_upload_creates_new_document_when_title_given(
    client: AsyncClient,
) -> None:
    await _login(client)
    plaintext, _ = await _make_token(client)
    client.cookies.clear()
    r = await client.post(
        "/api/v1/upload",
        headers={"Authorization": f"Bearer {plaintext}"},
        data={"title": "On the Origin of Trees"},
        files={"file": ("trees.pdf", b"%PDF-1.4\nfake", "application/pdf")},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["item"] is not None
    assert body["item"]["item_type"] == "document"
    assert body["item"]["data"]["title"] == "On the Origin of Trees"
    assert body["attachment"]["filename"] == "trees.pdf"
    assert body["attachment"]["item_id"] == body["item"]["id"]


async def test_v1_upload_requires_either_item_or_title(
    client: AsyncClient,
) -> None:
    await _login(client)
    plaintext, _ = await _make_token(client)
    client.cookies.clear()

    # Both missing.
    r = await client.post(
        "/api/v1/upload",
        headers={"Authorization": f"Bearer {plaintext}"},
        files={"file": ("x.bin", b"x", "text/plain")},
    )
    assert r.status_code == 400

    # Both present.
    item_id = "00000000-0000-0000-0000-000000000000"
    r = await client.post(
        "/api/v1/upload",
        headers={"Authorization": f"Bearer {plaintext}"},
        data={"title": "T", "item_id": item_id},
        files={"file": ("x.bin", b"x", "text/plain")},
    )
    assert r.status_code == 400


async def test_v1_upload_respects_collection_scope(client: AsyncClient) -> None:
    slug = await _login(client)
    a = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "A"}
        )
    ).json()
    b = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "B"}
        )
    ).json()
    item_a = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "in-A"}},
        )
    ).json()
    item_b = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "in-B"}},
        )
    ).json()
    await client.put(
        f"/api/items/{item_a['id']}/collections", json={"collection_ids": [a["id"]]}
    )
    await client.put(
        f"/api/items/{item_b['id']}/collections", json={"collection_ids": [b["id"]]}
    )

    plaintext, _ = await _make_token(
        client, scopes=["upload"], allowed_collection_ids=[a["id"]]
    )
    client.cookies.clear()

    # Upload to A is allowed.
    r = await client.post(
        "/api/v1/upload",
        headers={"Authorization": f"Bearer {plaintext}"},
        data={"item_id": item_a["id"]},
        files={"file": ("a.bin", b"a", "application/octet-stream")},
    )
    assert r.status_code == 201, r.text

    # Upload to B is forbidden.
    r = await client.post(
        "/api/v1/upload",
        headers={"Authorization": f"Bearer {plaintext}"},
        data={"item_id": item_b["id"]},
        files={"file": ("b.bin", b"b", "application/octet-stream")},
    )
    assert r.status_code == 403, r.text


async def test_v1_upload_with_title_requires_collection_when_token_scoped(
    client: AsyncClient,
) -> None:
    slug = await _login(client)
    a = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "A"}
        )
    ).json()
    plaintext, _ = await _make_token(
        client, scopes=["upload"], allowed_collection_ids=[a["id"]]
    )
    client.cookies.clear()

    # Without collection_id, the new item would be invisible to the
    # very token that just minted it — refuse cleanly.
    r = await client.post(
        "/api/v1/upload",
        headers={"Authorization": f"Bearer {plaintext}"},
        data={"title": "T"},
        files={"file": ("t.pdf", b"x", "application/pdf")},
    )
    assert r.status_code == 400, r.text

    # With matching collection_id it succeeds.
    r = await client.post(
        "/api/v1/upload",
        headers={"Authorization": f"Bearer {plaintext}"},
        data={"title": "T", "collection_id": a["id"]},
        files={"file": ("t.pdf", b"x", "application/pdf")},
    )
    assert r.status_code == 201, r.text


# ── search ────────────────────────────────────────────────────────────────


async def test_v1_uploads_register_returns_presigned_url(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_presign_upload(key: str, *_args: object, **_kw: object) -> str:
        return f"https://memory/upload/{key}?sig=stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    await _login(client)
    plaintext, _ = await _make_token(client)
    client.cookies.clear()

    r = await client.post(
        "/api/v1/uploads/register",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={
            "title": "Migrated Paper",
            "filename": "paper.pdf",
            "content_type": "application/pdf",
            "size_bytes": 12345,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    # New attachments key off the space, so a bucket policy or lifecycle
    # rule can address one space's objects without a database lookup.
    assert body["upload_url"].startswith("https://memory/upload/spaces/")
    assert f"/items/{body['item']['id']}/attachments/" in body["upload_url"]
    assert body["item"] is not None
    assert body["item"]["item_type"] == "document"
    assert body["item"]["data"]["title"] == "Migrated Paper"
    assert body["attachment"]["uploaded_at"] is None
    assert body["attachment"]["item_id"] == body["item"]["id"]
    assert "expires_at" in body


async def test_v1_uploads_complete_stamps_uploaded_at(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_presign_upload(key: str, *_args: object, **_kw: object) -> str:
        return f"https://memory/upload/{key}"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    await _login(client)
    plaintext, _ = await _make_token(client)
    client.cookies.clear()
    reg = (
        await client.post(
            "/api/v1/uploads/register",
            headers={"Authorization": f"Bearer {plaintext}"},
            json={
                "title": "X",
                "filename": "x.pdf",
                "content_type": "application/pdf",
            },
        )
    ).json()
    att_id = reg["attachment"]["id"]

    r = await client.post(
        f"/api/v1/uploads/{att_id}/complete",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["uploaded_at"] is not None

    # Idempotent — completing again leaves the timestamp alone.
    first = r.json()["uploaded_at"]
    r2 = await client.post(
        f"/api/v1/uploads/{att_id}/complete",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert r2.status_code == 200
    assert r2.json()["uploaded_at"] == first


async def test_v1_uploads_complete_verify_uses_bucket_size(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify=true HEADs the bucket and overrides the client-claimed
    size with what's actually there."""

    async def fake_presign_upload(key: str, *_args: object, **_kw: object) -> str:
        return f"https://memory/upload/{key}"

    async def fake_head_object(_key: str) -> dict[str, object]:
        return {"size": 999_999}

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)
    monkeypatch.setattr(storage, "head_object", fake_head_object)

    await _login(client)
    plaintext, _ = await _make_token(client)
    client.cookies.clear()
    reg = (
        await client.post(
            "/api/v1/uploads/register",
            headers={"Authorization": f"Bearer {plaintext}"},
            json={
                "title": "X",
                "filename": "x.pdf",
                "content_type": "application/pdf",
                "size_bytes": 100,
            },
        )
    ).json()
    att_id = reg["attachment"]["id"]

    r = await client.post(
        f"/api/v1/uploads/{att_id}/complete?verify=true",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert r.status_code == 200
    assert r.json()["size_bytes"] == 999_999


async def test_v1_search_filters_by_collection_scope(
    client: AsyncClient,
) -> None:
    slug = await _login(client)
    a = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "A"}
        )
    ).json()
    b = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "B"}
        )
    ).json()
    in_a = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "in-A"}},
        )
    ).json()
    in_b = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "in-B"}},
        )
    ).json()
    await client.put(
        f"/api/items/{in_a['id']}/collections", json={"collection_ids": [a["id"]]}
    )
    await client.put(
        f"/api/items/{in_b['id']}/collections", json={"collection_ids": [b["id"]]}
    )

    plaintext, _ = await _make_token(
        client, scopes=["search"], allowed_collection_ids=[a["id"]]
    )
    client.cookies.clear()
    rows = (
        await client.get(
            "/api/v1/search",
            headers={"Authorization": f"Bearer {plaintext}"},
        )
    ).json()
    assert [r["id"] for r in rows] == [in_a["id"]]


async def test_v1_search_excludes_descendants_by_default(
    client: AsyncClient,
) -> None:
    """Without ``include_descendants`` the seed list is exact-match —
    an item in a subcollection of a seed is NOT visible."""
    slug = await _login(client)
    parent = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "Parent"}
        )
    ).json()
    child = (
        await client.post(
            f"/api/spaces/{slug}/collections",
            json={"name": "Child", "parent_id": parent["id"]},
        )
    ).json()
    in_parent = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "in-parent"}},
        )
    ).json()
    in_child = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "in-child"}},
        )
    ).json()
    await client.put(
        f"/api/items/{in_parent['id']}/collections",
        json={"collection_ids": [parent["id"]]},
    )
    await client.put(
        f"/api/items/{in_child['id']}/collections",
        json={"collection_ids": [child["id"]]},
    )

    plaintext, _ = await _make_token(
        client, scopes=["search"], allowed_collection_ids=[parent["id"]]
    )
    client.cookies.clear()
    rows = (
        await client.get(
            "/api/v1/search",
            headers={"Authorization": f"Bearer {plaintext}"},
        )
    ).json()
    assert [r["id"] for r in rows] == [in_parent["id"]]


async def test_v1_search_includes_descendants_when_flag_set(
    client: AsyncClient,
) -> None:
    """``include_descendants=true`` widens the gate to the whole
    subtree below each seed — including grandchildren — while still
    excluding unrelated collections."""
    slug = await _login(client)
    parent = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "Parent"}
        )
    ).json()
    child = (
        await client.post(
            f"/api/spaces/{slug}/collections",
            json={"name": "Child", "parent_id": parent["id"]},
        )
    ).json()
    grandchild = (
        await client.post(
            f"/api/spaces/{slug}/collections",
            json={"name": "Grandchild", "parent_id": child["id"]},
        )
    ).json()
    sibling = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "Sibling"}
        )
    ).json()

    titles_in = {
        "in-parent": [parent["id"]],
        "in-child": [child["id"]],
        "in-grandchild": [grandchild["id"]],
    }
    expected_ids: set[str] = set()
    for title, coll_ids in titles_in.items():
        it = (
            await client.post(
                f"/api/spaces/{slug}/items",
                json={"item_type": "document", "data": {"title": title}},
            )
        ).json()
        await client.put(
            f"/api/items/{it['id']}/collections",
            json={"collection_ids": coll_ids},
        )
        expected_ids.add(it["id"])

    in_sibling = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "in-sibling"}},
        )
    ).json()
    await client.put(
        f"/api/items/{in_sibling['id']}/collections",
        json={"collection_ids": [sibling["id"]]},
    )

    plaintext, _ = await _make_token(
        client,
        scopes=["search"],
        allowed_collection_ids=[parent["id"]],
        include_descendants=True,
    )
    client.cookies.clear()
    rows = (
        await client.get(
            "/api/v1/search",
            headers={"Authorization": f"Bearer {plaintext}"},
        )
    ).json()
    assert {r["id"] for r in rows} == expected_ids
    assert in_sibling["id"] not in {r["id"] for r in rows}


async def test_v1_search_picks_up_subcollections_added_after_mint(
    client: AsyncClient,
) -> None:
    """The point of the dynamic flag: a new subcollection added after
    the token was minted is automatically covered."""
    slug = await _login(client)
    parent = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "Parent"}
        )
    ).json()

    plaintext, _ = await _make_token(
        client,
        scopes=["search"],
        allowed_collection_ids=[parent["id"]],
        include_descendants=True,
    )

    # New subcollection minted *after* the token, with an item filed in it.
    new_child = (
        await client.post(
            f"/api/spaces/{slug}/collections",
            json={"name": "Added later", "parent_id": parent["id"]},
        )
    ).json()
    it = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "fresh"}},
        )
    ).json()
    await client.put(
        f"/api/items/{it['id']}/collections",
        json={"collection_ids": [new_child["id"]]},
    )

    client.cookies.clear()
    rows = (
        await client.get(
            "/api/v1/search",
            headers={"Authorization": f"Bearer {plaintext}"},
        )
    ).json()
    assert [r["id"] for r in rows] == [it["id"]]


async def test_v1_search_collection_filter_respects_descendants(
    client: AsyncClient,
) -> None:
    """``?collection=<child>`` should succeed when the child is below
    a seed (with the flag) and yield empty results without it."""
    slug = await _login(client)
    parent = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "Parent"}
        )
    ).json()
    child = (
        await client.post(
            f"/api/spaces/{slug}/collections",
            json={"name": "Child", "parent_id": parent["id"]},
        )
    ).json()
    in_child = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "in-child"}},
        )
    ).json()
    await client.put(
        f"/api/items/{in_child['id']}/collections",
        json={"collection_ids": [child["id"]]},
    )

    # Mint both tokens up-front while the cookie session is still live.
    plaintext_flag, _ = await _make_token(
        client,
        scopes=["search"],
        name="with-flag",
        allowed_collection_ids=[parent["id"]],
        include_descendants=True,
    )
    plaintext_exact, _ = await _make_token(
        client,
        scopes=["search"],
        name="exact",
        allowed_collection_ids=[parent["id"]],
    )

    client.cookies.clear()
    # Flag on: ?collection=<child> resolves through the descendant set.
    rows = (
        await client.get(
            f"/api/v1/search?collection={child['id']}",
            headers={"Authorization": f"Bearer {plaintext_flag}"},
        )
    ).json()
    assert [r["id"] for r in rows] == [in_child["id"]]

    # Flag off: child is outside the exact-match allow-list, empty.
    rows = (
        await client.get(
            f"/api/v1/search?collection={child['id']}",
            headers={"Authorization": f"Bearer {plaintext_exact}"},
        )
    ).json()
    assert rows == []


# ── collections (list/create) ─────────────────────────────────────────────


async def test_v1_list_collections_returns_paths(client: AsyncClient) -> None:
    slug = await _login(client)
    fem = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "FEM"}
        )
    ).json()
    aster = (
        await client.post(
            f"/api/spaces/{slug}/collections",
            json={"name": "Code Aster", "parent_id": fem["id"]},
        )
    ).json()
    plaintext, _ = await _make_token(client)

    client.cookies.clear()
    r = await client.get(
        "/api/v1/collections",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert r.status_code == 200, r.text
    by_id = {c["id"]: c for c in r.json()}
    assert by_id[fem["id"]]["path"] == "FEM"
    assert by_id[aster["id"]]["path"] == "FEM / Code Aster"
    assert by_id[aster["id"]]["parent_id"] == fem["id"]


async def test_v1_list_collections_respects_token_scope(
    client: AsyncClient,
) -> None:
    slug = await _login(client)
    fem = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "FEM"}
        )
    ).json()
    aster = (
        await client.post(
            f"/api/spaces/{slug}/collections",
            json={"name": "Code Aster", "parent_id": fem["id"]},
        )
    ).json()
    other = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "Sealed"}
        )
    ).json()
    # Token scoped to Code Aster only — FEM (parent) and Sealed (sibling)
    # must not leak through; path falls back to local name when the
    # parent is invisible.
    plaintext, _ = await _make_token(
        client,
        scopes=["search"],
        allowed_collection_ids=[aster["id"]],
    )

    client.cookies.clear()
    r = await client.get(
        "/api/v1/collections",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert [c["id"] for c in rows] == [aster["id"]]
    assert rows[0]["path"] == "Code Aster"
    assert other["id"] not in {c["id"] for c in rows}


async def test_v1_create_collection_under_parent(client: AsyncClient) -> None:
    slug = await _login(client)
    fem = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "FEM"}
        )
    ).json()
    plaintext, _ = await _make_token(client)

    client.cookies.clear()
    r = await client.post(
        "/api/v1/collections",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={"name": "Code Aster", "parent_id": fem["id"]},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["parent_id"] == fem["id"]
    assert body["path"] == "FEM / Code Aster"
    assert body["space_id"] == fem["space_id"]


async def test_v1_create_collection_scoped_token_must_pass_parent(
    client: AsyncClient,
) -> None:
    slug = await _login(client)
    aster = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "Code Aster"}
        )
    ).json()
    other = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "Sealed"}
        )
    ).json()
    plaintext, _ = await _make_token(
        client,
        scopes=["upload"],
        allowed_collection_ids=[aster["id"]],
    )

    client.cookies.clear()
    # No parent_id → would create a root collection outside scope.
    r = await client.post(
        "/api/v1/collections",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={"name": "Stray"},
    )
    assert r.status_code == 400, r.text

    # parent_id outside the allow-list → 403.
    r = await client.post(
        "/api/v1/collections",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={"name": "Sub", "parent_id": other["id"]},
    )
    assert r.status_code == 403, r.text

    # parent_id inside the allow-list → ok.
    r = await client.post(
        "/api/v1/collections",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={"name": "Sub", "parent_id": aster["id"]},
    )
    assert r.status_code == 201, r.text
    assert r.json()["parent_id"] == aster["id"]


async def test_v1_set_item_collections_replaces_membership(
    client: AsyncClient,
) -> None:
    slug = await _login(client)
    a = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "A"}
        )
    ).json()
    b = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "B"}
        )
    ).json()
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "x"}},
        )
    ).json()
    await client.put(
        f"/api/items/{item['id']}/collections",
        json={"collection_ids": [a["id"]]},
    )
    plaintext, _ = await _make_token(client)
    client.cookies.clear()

    r = await client.put(
        f"/api/v1/items/{item['id']}/collections",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={"collection_ids": [b["id"]]},
    )
    assert r.status_code == 200, r.text
    assert r.json() == [b["id"]]


async def test_v1_set_item_collections_respects_token_scope(
    client: AsyncClient,
) -> None:
    slug = await _login(client)
    a = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "A"}
        )
    ).json()
    b = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "B"}
        )
    ).json()
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "x"}},
        )
    ).json()
    await client.put(
        f"/api/items/{item['id']}/collections",
        json={"collection_ids": [a["id"]]},
    )
    plaintext, _ = await _make_token(
        client, scopes=["upload"], allowed_collection_ids=[a["id"]]
    )
    client.cookies.clear()

    # Filing into B is outside the allow-list → 403.
    r = await client.put(
        f"/api/v1/items/{item['id']}/collections",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={"collection_ids": [b["id"]]},
    )
    assert r.status_code == 403, r.text

    # Refiling within A is fine.
    r = await client.put(
        f"/api/v1/items/{item['id']}/collections",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={"collection_ids": [a["id"]]},
    )
    assert r.status_code == 200, r.text


async def test_v1_delete_collection_cascades(client: AsyncClient) -> None:
    slug = await _login(client)
    parent = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "P"}
        )
    ).json()
    child = (
        await client.post(
            f"/api/spaces/{slug}/collections",
            json={"name": "C", "parent_id": parent["id"]},
        )
    ).json()
    plaintext, _ = await _make_token(client)
    client.cookies.clear()

    r = await client.delete(
        f"/api/v1/collections/{child['id']}",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert r.status_code == 204, r.text

    # Listing no longer includes the child.
    rows = (
        await client.get(
            "/api/v1/collections",
            headers={"Authorization": f"Bearer {plaintext}"},
        )
    ).json()
    assert child["id"] not in {c["id"] for c in rows}
    assert parent["id"] in {c["id"] for c in rows}


async def test_v1_delete_collection_outside_scope_forbidden(
    client: AsyncClient,
) -> None:
    slug = await _login(client)
    a = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "A"}
        )
    ).json()
    other = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "Other"}
        )
    ).json()
    plaintext, _ = await _make_token(
        client, scopes=["upload"], allowed_collection_ids=[a["id"]]
    )
    client.cookies.clear()

    r = await client.delete(
        f"/api/v1/collections/{other['id']}",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert r.status_code == 403, r.text
