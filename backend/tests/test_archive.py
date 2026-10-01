"""The shelf.archive format: what "Download PDFs" writes into index.json,
and importing it back.

Format compatibility is pinned by fixtures under tests/fixtures/archive:
every version ever released stays importable, so a fixture is added for
each new version and the old ones are never edited.
"""

import hashlib
import io
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from obstore.store import MemoryStore

from shelf.archive_format import ArchiveIndex, parse_index
from shelf.config import settings
from shelf.services import storage

from .helpers import login, serve_objects

ADMIN = "admin@example.com"
FIXTURES = Path(__file__).parent / "fixtures" / "archive"
SCHEMA = (
    Path(__file__).parents[2] / "docs" / "schemas" / "shelf-archive-1.schema.json"
)


@pytest.fixture(autouse=True)
def memory_store(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    storage._store = MemoryStore()

    async def fake_presign(key: str, **_: object) -> str:
        return f"https://memory/{key}?sig=stub"

    async def body_for(key: str) -> bytes:
        return f"%PDF-1.4 bytes of {key}".encode()

    monkeypatch.setattr(storage, "presign_upload", fake_presign)
    monkeypatch.setattr(storage, "presign_download", fake_presign)
    serve_objects(monkeypatch, body_for)
    yield
    storage.reset_store()


async def _admin(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "admin_emails", [ADMIN])
    await login(client, ADMIN)


async def _space(client: AsyncClient, slug: str) -> str:
    r = await client.post("/api/spaces", json={"name": slug.title(), "slug": slug})
    assert r.status_code == 201, r.text
    return slug


async def _collection(
    client: AsyncClient, slug: str, name: str, parent: str | None = None
) -> str:
    body: dict[str, Any] = {"name": name}
    if parent:
        body["parent_id"] = parent
    r = await client.post(f"/api/spaces/{slug}/collections", json=body)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _item(
    client: AsyncClient,
    slug: str,
    data: dict[str, Any],
    item_type: str = "report",
    collections: list[str] | None = None,
) -> str:
    r = await client.post(
        f"/api/spaces/{slug}/items", json={"item_type": item_type, "data": data}
    )
    assert r.status_code == 201, r.text
    item_id = str(r.json()["id"])
    if collections:
        r = await client.put(
            f"/api/items/{item_id}/collections",
            json={"collection_ids": collections},
        )
        assert r.status_code == 200, r.text
    return item_id


async def _pdf(client: AsyncClient, item_id: str, filename: str) -> str:
    """An attachment as the browser leaves it: registered, then completed."""
    r = await client.post(
        f"/api/items/{item_id}/attachments",
        json={"filename": filename, "content_type": "application/pdf"},
    )
    assert r.status_code == 201, r.text
    att_id = str(r.json()["attachment"]["id"])
    r = await client.post(f"/api/attachments/{att_id}/complete")
    assert r.status_code == 200, r.text
    return att_id


async def _download(
    client: AsyncClient, slug: str, **params: Any
) -> tuple[zipfile.ZipFile, dict[str, Any]]:
    r = await client.get(f"/api/spaces/{slug}/attachments-zip", params=params)
    assert r.status_code == 200, r.text
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    return zf, json.loads(zf.read("index.json"))


async def _import(
    client: AsyncClient,
    slug: str,
    index: Any,
    collection_id: str | None = None,
    status: int = 200,
) -> dict[str, Any]:
    r = await client.post(
        f"/api/spaces/{slug}/archive-import",
        json={"index": index, "collection_id": collection_id},
    )
    assert r.status_code == status, r.text
    body = r.json()
    assert isinstance(body, dict)
    return body


async def _upload_all(client: AsyncClient, plan: dict[str, Any]) -> None:
    """What the browser does with an import's answer."""
    for up in plan["uploads"]:
        r = await client.post(
            f"/api/items/{up['item_id']}/attachments",
            json={"filename": up["filename"], "content_type": up["content_type"]},
        )
        assert r.status_code == 201, r.text
        att = r.json()["attachment"]["id"]
        assert (await client.post(f"/api/attachments/{att}/complete")).status_code == 200


async def _tree(client: AsyncClient, slug: str) -> set[str]:
    """This space's own collections as "A/B/C" paths."""
    colls = (await client.get(f"/api/spaces/{slug}/collections")).json()
    own = {c["id"]: c for c in colls if not c.get("is_inherited")}

    def path(c: dict[str, Any]) -> str:
        parent = own.get(c["parent_id"]) if c["parent_id"] else None
        return f"{path(parent)}/{c['name']}" if parent else c["name"]

    return {path(c) for c in own.values()}


async def _source(client: AsyncClient) -> dict[str, str]:
    """A small library: Top > {Mid > Leaf, Empty}, three documents, tags."""
    slug = await _space(client, "src")
    top = await _collection(client, slug, "Top")
    mid = await _collection(client, slug, "Mid", top)
    leaf = await _collection(client, slug, "Leaf", mid)
    await _collection(client, slug, "Empty", top)
    a = await _item(
        client,
        slug,
        {
            "title": "Alpha",
            "creators": [{"creatorType": "author", "name": "ACME"}],
            "date": "2024",
        },
        collections=[top],
    )
    b = await _item(client, slug, {"title": "Beta"}, "standard", [leaf])
    both = await _item(client, slug, {"title": "Both"}, collections=[mid, leaf])
    await _pdf(client, a, "alpha.pdf")
    await _pdf(client, b, "beta.pdf")
    await _pdf(client, both, "both.pdf")
    tag = (
        await client.post(
            f"/api/spaces/{slug}/tags", json={"name": "Physics", "color": "#ff0000"}
        )
    ).json()["id"]
    assert (
        await client.put(f"/api/items/{a}/tags", json={"tag_ids": [tag]})
    ).status_code == 200
    return {"slug": slug, "top": top, "mid": mid, "leaf": leaf, "a": a, "b": b, "both": both}


# ── The index the export writes ─────────────────────────────────────────────


async def test_export_index_is_a_complete_v1_archive(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _admin(client, monkeypatch)
    src = await _source(client)
    zf, raw = await _download(client, src["slug"], collection=src["top"])

    index = parse_index(raw)
    assert index.format == "shelf.archive"
    assert index.version == "1.0"
    assert index.generator and index.generator.startswith("shelf ")
    assert index.source and index.source.space and index.source.space.slug == "src"
    assert str(index.root_collection_id) == src["top"]

    # The whole tree, parents first, the empty subcollection included.
    assert [(c.name, c.folder) for c in index.collections] == [
        ("Top", ""),
        ("Mid", "Mid"),
        ("Leaf", "Mid/Leaf"),
        ("Empty", "Empty"),
    ]
    assert index.collections[0].parent_id is None

    items = {str(it.id): it for it in index.items}
    alpha = items[src["a"]]
    assert alpha.item_type == "report"
    assert alpha.data["creators"] == [{"creatorType": "author", "name": "ACME"}]
    assert alpha.tags == ["Physics"]
    assert [t.model_dump() for t in index.tags] == [
        {"name": "Physics", "color": "#ff0000"}
    ]
    assert items[src["b"]].item_type == "standard"
    assert sorted(map(str, items[src["both"]].collection_ids)) == sorted(
        [src["mid"], src["leaf"]]
    )

    # Each file entry describes the bytes actually in the ZIP.
    for it in index.items:
        for f in it.files:
            body = zf.read(f.path)
            assert f.size == len(body)
            assert f.sha256 == hashlib.sha256(body).hexdigest()
            assert f.version == "original"
    assert [f.path for f in alpha.files] == ["alpha.pdf"]
    assert [f.path for f in items[src["b"]].files] == ["Mid/Leaf/beta.pdf"]


def test_schema_file_matches_the_models() -> None:
    """docs/schemas holds the published JSON Schema; it is generated from
    the models, never edited by hand. Regenerate with:

        python -c "import json; from shelf.archive_format import ArchiveIndex; \
print(json.dumps(ArchiveIndex.model_json_schema(), indent=2))"
    """
    assert json.loads(SCHEMA.read_text(encoding="utf-8")) == (
        ArchiveIndex.model_json_schema()
    )


# ── Importing ───────────────────────────────────────────────────────────────


async def test_import_into_another_space_rebuilds_the_archive(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _admin(client, monkeypatch)
    src = await _source(client)
    _, index = await _download(client, src["slug"], collection=src["top"])

    dst = await _space(client, "dst")
    into = await _collection(client, dst, "Imported")
    plan = await _import(client, dst, index, into)

    assert plan["version"] == "1.0"
    assert plan["collections_created"] == 4
    assert plan["items_created"] == 3
    assert plan["items_existing"] == 0
    assert sorted(u["path"] for u in plan["uploads"]) == [
        "Mid/Leaf/beta.pdf",
        "Mid/both.pdf",
        "alpha.pdf",
    ]
    assert await _tree(client, dst) == {
        "Imported",
        "Imported/Top",
        "Imported/Top/Mid",
        "Imported/Top/Mid/Leaf",
        "Imported/Top/Empty",
    }

    items = (await client.get(f"/api/spaces/{dst}/items")).json()["items"]
    by_title = {it["data"]["title"]: it for it in items}
    assert set(by_title) == {"Alpha", "Beta", "Both"}
    alpha = by_title["Alpha"]
    assert alpha["item_type"] == "report"
    assert alpha["data"]["date"] == "2024"
    assert alpha["id"] != src["a"]
    tags = (await client.get(f"/api/spaces/{dst}/tags")).json()
    assert [(t["name"], t["color"]) for t in tags] == [("Physics", "#ff0000")]
    assert alpha["tag_ids"] == [tags[0]["id"]]
    assert len(by_title["Both"]["collection_ids"]) == 2
    # Uploads are addressed to the new items.
    assert {u["item_id"] for u in plan["uploads"]} == {
        it["id"] for it in items
    }


async def test_reimport_is_idempotent_and_finishes_what_was_left(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running the same import again duplicates nothing, and asks only for
    the PDFs that never made it -- which is how an interrupted import is
    resumed."""
    await _admin(client, monkeypatch)
    src = await _source(client)
    _, index = await _download(client, src["slug"], collection=src["top"])
    dst = await _space(client, "dst")

    first = await _import(client, dst, index)
    # Only one of the three uploads finishes before the "browser closes".
    done = [u for u in first["uploads"] if u["filename"] == "alpha.pdf"]
    await _upload_all(client, {"uploads": done})

    second = await _import(client, dst, index)
    assert second["collections_created"] == 0
    assert second["collections_existing"] == 4
    assert second["items_created"] == 0
    assert second["items_existing"] == 3
    assert second["files_existing"] == 1
    assert sorted(u["filename"] for u in second["uploads"]) == [
        "beta.pdf",
        "both.pdf",
    ]
    await _upload_all(client, second)

    third = await _import(client, dst, index)
    assert third["uploads"] == []
    assert third["files_existing"] == 3
    items = (await client.get(f"/api/spaces/{dst}/items")).json()["items"]
    assert len(items) == 3
    assert await _tree(client, dst) == {"Top", "Top/Mid", "Top/Mid/Leaf", "Top/Empty"}


async def test_a_round_trip_recognises_the_originals(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Export A, import into B, export B, import back into A: A's own
    documents are recognised through the origin B recorded, not copied."""
    await _admin(client, monkeypatch)
    src = await _source(client)
    _, index = await _download(client, src["slug"], collection=src["top"])
    dst = await _space(client, "dst")
    await _upload_all(client, await _import(client, dst, index))

    dst_top = next(
        c["id"]
        for c in (await client.get(f"/api/spaces/{dst}/collections")).json()
        if c["name"] == "Top"
    )
    _, back = await _download(client, dst, collection=dst_top)
    assert {str(it["origin_id"]) for it in back["items"]} == {
        src["a"],
        src["b"],
        src["both"],
    }

    plan = await _import(client, src["slug"], back)
    assert plan["items_created"] == 0
    assert plan["items_existing"] == 3
    assert plan["collections_created"] == 0
    assert plan["uploads"] == []


async def test_existing_items_are_filed_not_edited(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An item already here keeps its metadata and tags; it only gains
    the archive's collections."""
    await _admin(client, monkeypatch)
    src = await _source(client)
    _, index = await _download(client, src["slug"], collection=src["top"])
    index["items"][0]["data"]["title"] = "Changed in the archive"
    index["items"][0]["tags"] = ["Other"]

    elsewhere = await _collection(client, src["slug"], "Elsewhere")
    plan = await _import(client, src["slug"], index, elsewhere)
    assert plan["items_existing"] == 3
    assert plan["uploads"] == []

    item = (await client.get(f"/api/items/{src['a']}")).json()
    assert item["data"]["title"] == "Alpha"
    tags = (await client.get(f"/api/spaces/{src['slug']}/tags")).json()
    assert [t["name"] for t in tags] == ["Physics"]
    # Filed into the copy of the tree under Elsewhere, and still in Top.
    assert len(item["collection_ids"]) == 2


async def test_selected_documents_import_under_the_target(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _admin(client, monkeypatch)
    src = await _source(client)
    _, index = await _download(client, src["slug"], item=[src["a"], src["b"]])
    assert index["collections"] == []

    dst = await _space(client, "dst")
    into = await _collection(client, dst, "Inbox")
    plan = await _import(client, dst, index, into)
    assert plan["items_created"] == 2
    items = (await client.get(f"/api/spaces/{dst}/items")).json()["items"]
    assert all(it["collection_ids"] == [into] for it in items)


async def test_a_whole_space_round_trips(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every collection of the space as a folder, unfiled documents at
    the root -- and importing it into an empty space reproduces it."""
    await _admin(client, monkeypatch)
    src = await _source(client)
    other = await _collection(client, src["slug"], "Second root")
    filed = await _item(client, src["slug"], {"title": "Filed"}, collections=[other])
    loose = await _item(client, src["slug"], {"title": "Loose"})
    await _pdf(client, filed, "filed.pdf")
    await _pdf(client, loose, "loose.pdf")

    summary = (
        await client.get(
            f"/api/spaces/{src['slug']}/attachments-zip/summary",
            params={"whole_space": True},
        )
    ).json()
    assert summary == {"items": 5, "files": 5}

    zf, index = await _download(client, src["slug"], whole_space=True)
    assert index["scope"] == "space"
    assert index["root_collection_id"] is None
    roots = [c["name"] for c in index["collections"] if c["parent_id"] is None]
    assert roots == ["Top", "Second root"]
    assert sorted(n for n in zf.namelist() if n.endswith(".pdf")) == [
        "Second root/filed.pdf",
        "Top/Mid/Leaf/beta.pdf",
        "Top/Mid/both.pdf",
        "Top/alpha.pdf",
        "loose.pdf",
    ]

    dst = await _space(client, "dst")
    plan = await _import(client, dst, index)
    assert plan["items_created"] == 5
    assert len(plan["uploads"]) == 5
    assert await _tree(client, dst) == await _tree(client, src["slug"])
    items = (await client.get(f"/api/spaces/{dst}/items")).json()["items"]
    unfiled = [it for it in items if not it["collection_ids"]]
    assert [it["data"]["title"] for it in unfiled] == ["Loose"]


async def test_whole_space_excludes_other_selectors(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _admin(client, monkeypatch)
    src = await _source(client)
    r = await client.get(
        f"/api/spaces/{src['slug']}/attachments-zip",
        params={"whole_space": True, "collection": src["top"]},
    )
    assert r.status_code == 400


# ── Versioning ──────────────────────────────────────────────────────────────


async def test_a_newer_minor_version_imports(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later 1.x may add fields anywhere; this reader ignores them."""
    await _admin(client, monkeypatch)
    dst = await _space(client, "dst")
    index = json.loads((FIXTURES / "v1.0" / "index.json").read_text("utf-8"))
    index["version"] = "1.7"
    index["some_new_section"] = {"anything": True}
    index["collections"][0]["icon"] = "folder"
    index["items"][0]["files"][0]["pages"] = 12
    plan = await _import(client, dst, index)
    assert plan["version"] == "1.7"
    assert plan["items_created"] == len(index["items"])


async def test_an_unknown_major_version_is_refused_clearly(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _admin(client, monkeypatch)
    dst = await _space(client, "dst")
    index = json.loads((FIXTURES / "v1.0" / "index.json").read_text("utf-8"))
    index["version"] = "2.0"
    body = await _import(client, dst, index, status=422)
    assert "2.0" in body["detail"] and "upgrade" in body["detail"]
    # Nothing was written.
    assert (await client.get(f"/api/spaces/{dst}/items")).json()["items"] == []


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda ix: ix.update(format="something.else"), "Not a Shelf archive"),
        (lambda ix: ix.update(version="one"), "Unreadable archive version"),
        (
            lambda ix: ix["items"][0].update(collection_ids=[
                "00000000-0000-0000-0000-000000000001"
            ]),
            "not in the archive",
        ),
        (
            lambda ix: ix["collections"][1].update(
                parent_id="00000000-0000-0000-0000-000000000001"
            ),
            "parent not in the archive",
        ),
    ],
)
async def test_a_broken_index_is_refused(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    mutate: Any,
    message: str,
) -> None:
    await _admin(client, monkeypatch)
    dst = await _space(client, "dst")
    index = json.loads((FIXTURES / "v1.0" / "index.json").read_text("utf-8"))
    mutate(index)
    body = await _import(client, dst, index, status=422)
    assert message in body["detail"]


@pytest.mark.parametrize("version", sorted(p.name for p in FIXTURES.iterdir()))
async def test_every_released_format_still_imports(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    """The compatibility promise, enforced: an archive written by any
    released Shelf imports, and rebuilds the tree its fixture expects."""
    await _admin(client, monkeypatch)
    dst = await _space(client, "dst")
    folder = FIXTURES / version
    index = json.loads((folder / "index.json").read_text("utf-8"))
    expected = json.loads((folder / "expected.json").read_text("utf-8"))

    plan = await _import(client, dst, index)
    assert plan["items_created"] == expected["items"]
    assert sorted(u["path"] for u in plan["uploads"]) == sorted(expected["uploads"])
    assert await _tree(client, dst) == set(expected["tree"])


# ── Access ──────────────────────────────────────────────────────────────────


async def test_import_needs_edit_access_and_a_local_target(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _admin(client, monkeypatch)
    dst = await _space(client, "dst")
    other = await _space(client, "other")
    foreign = await _collection(client, other, "Theirs")
    index = json.loads((FIXTURES / "v1.0" / "index.json").read_text("utf-8"))

    body = await _import(client, dst, index, foreign, status=404)
    assert body["detail"] == "No such collection in this space"

    await login(client, "mallory@example.com")
    await _import(client, dst, index, status=404)
