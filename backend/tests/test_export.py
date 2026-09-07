"""Integration tests for /api/items/{id}/export and /api/spaces/{slug}/export."""

import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile

import pytest
from httpx import AsyncClient

from shelf.services import storage


async def _login(client: AsyncClient, email: str = "alice@example.com") -> str:
    r = await client.post("/auth/dev-login", json={"email": email})
    assert r.status_code == 200, r.text
    me = await client.get("/api/me")
    user_id = me.json()["id"]
    return f"u-{user_id.replace('-', '')[:8]}"


async def _make_paper(
    client: AsyncClient, slug: str, title: str = "Quantum Foo"
) -> dict:
    r = await client.post(
        f"/api/spaces/{slug}/items",
        json={
            "item_type": "journalArticle",
            "data": {
                "title": title,
                "creators": [
                    {
                        "creatorType": "author",
                        "firstName": "Ada",
                        "lastName": "Lovelace",
                    },
                    {
                        "creatorType": "author",
                        "firstName": "Charles",
                        "lastName": "Babbage",
                    },
                ],
                "date": "2024-06-15",
                "publicationTitle": "Journal of Computing",
                "volume": "42",
                "issue": "3",
                "pages": "100-120",
                "DOI": "10.1234/jc.2024.42.100",
                "abstractNote": "An abstract about computing.",
            },
        },
    )
    assert r.status_code == 201
    return r.json()


async def test_bibtex_single_item(client: AsyncClient) -> None:
    slug = await _login(client)
    item = await _make_paper(client, slug)
    r = await client.get(
        f"/api/items/{item['id']}/export", params={"format": "bibtex"}
    )
    assert r.status_code == 200
    text = r.text
    assert text.startswith("@article{lovelace2024")
    assert "title = {Quantum Foo}" in text
    assert "author = {Lovelace, Ada and Babbage, Charles}" in text
    assert "year = {2024}" in text
    assert "journal = {Journal of Computing}" in text
    assert "doi = {10.1234/jc.2024.42.100}" in text
    assert r.headers["content-type"].startswith("application/x-bibtex")


async def test_csl_json_single_item(client: AsyncClient) -> None:
    slug = await _login(client)
    item = await _make_paper(client, slug)
    r = await client.get(
        f"/api/items/{item['id']}/export", params={"format": "csl-json"}
    )
    assert r.status_code == 200
    payload = json.loads(r.text)
    assert isinstance(payload, list) and len(payload) == 1
    e = payload[0]
    assert e["type"] == "article-journal"
    assert e["title"] == "Quantum Foo"
    assert e["author"] == [
        {"family": "Lovelace", "given": "Ada"},
        {"family": "Babbage", "given": "Charles"},
    ]
    assert e["issued"] == {"date-parts": [[2024, 6, 15]]}
    assert e["container-title"] == "Journal of Computing"
    assert e["DOI"] == "10.1234/jc.2024.42.100"


async def test_zotero_rdf_single_item(client: AsyncClient) -> None:
    slug = await _login(client)
    paper = await _make_paper(client, slug)
    # Add a tag and a collection so the RDF emits both.
    tag = (
        await client.post(f"/api/spaces/{slug}/tags", json={"name": "physics"})
    ).json()
    coll = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "Reading"}
        )
    ).json()
    await client.put(
        f"/api/items/{paper['id']}/tags", json={"tag_ids": [tag["id"]]}
    )
    await client.put(
        f"/api/items/{paper['id']}/collections",
        json={"collection_ids": [coll["id"]]},
    )

    r = await client.get(
        f"/api/items/{paper['id']}/export", params={"format": "rdf"}
    )
    assert r.status_code == 200
    body = r.text
    # Parse — it should be valid XML.
    root = ET.fromstring(body)
    ns = {
        "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
        "dc": "http://purl.org/dc/elements/1.1/",
        "dcterms": "http://purl.org/dc/terms/",
        "bib": "http://purl.org/net/biblio#",
        "foaf": "http://xmlns.com/foaf/0.1/",
        "z": "http://www.zotero.org/namespaces/export#",
    }
    article = root.find("bib:Article", ns)
    assert article is not None, "expected a bib:Article element"
    title = article.find("dc:title", ns)
    assert title is not None and title.text == "Quantum Foo"
    # Tag round-trips as dc:subject.
    subjects = [s.text for s in article.findall("dc:subject", ns)]
    assert "physics" in subjects
    # Collection link present.
    assert (
        f"#collection_{coll['id'].replace('-', '')}"
        in body
    )
    # Authors emitted in order.
    surnames = [
        s.text
        for s in article.iterfind(
            "bib:authors/rdf:Seq/rdf:li/foaf:Person/foaf:surname", ns
        )
    ]
    assert surnames == ["Lovelace", "Babbage"]


async def test_export_isolates_between_users(client: AsyncClient) -> None:
    a_slug = await _login(client, email="alice@example.com")
    a_item = await _make_paper(client, a_slug, title="Alice's paper")

    b_slug = await _login(client, email="bob@example.com")
    # Bob can't fetch Alice's item.
    r = await client.get(
        f"/api/items/{a_item['id']}/export", params={"format": "bibtex"}
    )
    assert r.status_code == 404
    # Bob's own space export is empty.
    r = await client.get(
        f"/api/spaces/{b_slug}/export", params={"format": "bibtex"}
    )
    assert r.status_code == 200
    assert r.text == ""


async def test_bulk_export_filters_by_tag(client: AsyncClient) -> None:
    slug = await _login(client)
    a = await _make_paper(client, slug, title="A paper")
    await _make_paper(client, slug, title="B paper")
    physics = (
        await client.post(f"/api/spaces/{slug}/tags", json={"name": "physics"})
    ).json()
    await client.put(
        f"/api/items/{a['id']}/tags", json={"tag_ids": [physics["id"]]}
    )
    # B has no tag.

    r = await client.get(
        f"/api/spaces/{slug}/export",
        params={"format": "bibtex", "tag": "physics"},
    )
    assert r.status_code == 200
    text = r.text
    assert "title = {A paper}" in text
    assert "title = {B paper}" not in text


async def test_bibtex_handles_missing_metadata(client: AsyncClient) -> None:
    slug = await _login(client)
    minimal = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "Bare"}},
        )
    ).json()
    r = await client.get(
        f"/api/items/{minimal['id']}/export", params={"format": "bibtex"}
    )
    assert r.status_code == 200
    # No author, no date — cite key falls back to anon+nd, which the
    # renderer rewrites to item<short-id>.
    assert re.search(r"@misc\{(item|anonnd)", r.text)
    assert "title = {Bare}" in r.text


async def test_rdf_bundle_zip_layout(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When a bulk RDF export is requested with bundle=true (the
    default for /spaces/{slug}/export), the response is a ZIP that
    contains <slug>.rdf at the root and files/<id>/<filename> for
    every attachment. Mirrors Zotero's translator output."""
    fake_pdf = b"%PDF-1.4 fake bytes"

    async def fake_read_object(key: str) -> bytes:
        return fake_pdf

    async def fake_presign_upload(key: str, **_: object) -> str:
        return f"https://stub/upload/{key}"

    monkeypatch.setattr(storage, "read_object", fake_read_object)
    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    slug = await _login(client)
    paper = await _make_paper(client, slug, title="Bundled paper")
    # Register an attachment row (no real upload — the read_object
    # monkeypatch returns fake bytes when the export tries to fetch).
    r = await client.post(
        f"/api/items/{paper['id']}/attachments",
        json={
            "filename": "paper.pdf",
            "content_type": "application/pdf",
            "size_bytes": len(fake_pdf),
        },
    )
    assert r.status_code == 201
    att = r.json()["attachment"]

    r = await client.get(
        f"/api/spaces/{slug}/export", params={"format": "rdf"}
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/zip")

    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(zf.namelist())
    assert f"{slug}.rdf" in names
    expected_path = f"files/{att['id'].replace('-', '')}/paper.pdf"
    assert expected_path in names
    assert zf.read(expected_path) == fake_pdf

    # The .rdf must reference the bundled path via z:path.
    rdf_text = zf.read(f"{slug}.rdf").decode()
    assert f'<z:path rdf:resource="{expected_path}"/>' in rdf_text
    # Parent → attachment link uses #item_<attachment_id>.
    att_hex = att["id"].replace("-", "")
    assert f'<link:link rdf:resource="#item_{att_hex}"/>' in rdf_text


async def _attach(
    client: AsyncClient,
    item_id: str,
    filename: str,
    content_type: str = "application/pdf",
    size: int = 10,
) -> dict:
    r = await client.post(
        f"/api/items/{item_id}/attachments",
        json={
            "filename": filename,
            "content_type": content_type,
            "size_bytes": size,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["attachment"]


async def test_bulk_pdf_zip_bundles_selected_items(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bulk-select "Download PDFs" action zips every PDF attachment
    of the selected items into one flat archive, disambiguating
    duplicate filenames and skipping non-PDF attachments."""

    async def fake_read_object(key: str) -> bytes:
        return f"bytes-of-{key}".encode()

    monkeypatch.setattr(storage, "read_object", fake_read_object)

    slug = await _login(client)
    a = await _make_paper(client, slug, title="Paper A")
    b = await _make_paper(client, slug, title="Paper B")
    c = await _make_paper(client, slug, title="Paper C (not selected)")

    await _attach(client, a["id"], "report.pdf")
    # Same filename on a different item — must not collide in the ZIP.
    await _attach(client, b["id"], "report.pdf")
    # A non-PDF attachment on B is skipped.
    await _attach(client, b["id"], "notes.txt", content_type="text/plain")
    # C's PDF must not appear — it isn't in the selection.
    await _attach(client, c["id"], "other.pdf")

    r = await client.get(
        f"/api/spaces/{slug}/attachments-zip",
        params={"item": [a["id"], b["id"]]},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/zip")
    assert r.headers["content-disposition"] == (
        f'attachment; filename="{slug}-pdfs.zip"'
    )

    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert names == ["report.pdf", "report (2).pdf"]
    # No non-PDF and nothing from the unselected item.
    assert not any(n.endswith(".txt") for n in names)
    assert "other.pdf" not in names


async def test_bulk_pdf_zip_reports_unfetchable_blobs(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PDF whose blob can't be fetched (e.g. its original object was
    superseded and the key is gone) must not silently vanish: the good
    files still come through, the missing one is listed in
    _MISSING_FILES.txt, and X-Shelf-Skipped reports the count."""
    slug = await _login(client)
    good = await _make_paper(client, slug, title="Good")
    bad = await _make_paper(client, slug, title="Bad")
    await _attach(client, good["id"], "good.pdf")
    bad_att = await _attach(client, bad["id"], "gone.pdf")

    async def flaky_read_object(key: str) -> bytes:
        if bad_att["id"].replace("-", "") in key.replace("-", ""):
            raise FileNotFoundError(key)
        return b"good-bytes"

    monkeypatch.setattr(storage, "read_object", flaky_read_object)

    r = await client.get(
        f"/api/spaces/{slug}/attachments-zip",
        params={"item": [good["id"], bad["id"]]},
    )
    assert r.status_code == 200, r.text
    assert r.headers["x-shelf-skipped"] == "1"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert "good.pdf" in names
    assert "gone.pdf" not in names
    assert "_MISSING_FILES.txt" in names
    assert "gone.pdf" in zf.read("_MISSING_FILES.txt").decode()


async def test_bulk_pdf_zip_404_when_no_pdfs(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_read_object(key: str) -> bytes:
        return b"x"

    monkeypatch.setattr(storage, "read_object", fake_read_object)
    slug = await _login(client)
    item = await _make_paper(client, slug, title="No PDF here")
    await _attach(client, item["id"], "notes.txt", content_type="text/plain")

    r = await client.get(
        f"/api/spaces/{slug}/attachments-zip", params={"item": [item["id"]]}
    )
    assert r.status_code == 404


async def test_bulk_pdf_zip_requires_an_item(client: AsyncClient) -> None:
    slug = await _login(client)
    r = await client.get(f"/api/spaces/{slug}/attachments-zip")
    assert r.status_code == 400


async def test_bulk_pdf_zip_isolated_between_users(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_read_object(key: str) -> bytes:
        return b"x"

    monkeypatch.setattr(storage, "read_object", fake_read_object)

    a_slug = await _login(client, email="alice@example.com")
    a_item = await _make_paper(client, a_slug, title="Alice paper")
    await _attach(client, a_item["id"], "alice.pdf")

    # Bob can't pull Alice's PDFs through his own space slug.
    b_slug = await _login(client, email="bob@example.com")
    r = await client.get(
        f"/api/spaces/{b_slug}/attachments-zip",
        params={"item": [a_item["id"]]},
    )
    # Alice's item isn't in Bob's space → nothing matches → 404.
    assert r.status_code == 404
    # And Bob can't read Alice's space either.
    r = await client.get(
        f"/api/spaces/{a_slug}/attachments-zip",
        params={"item": [a_item["id"]]},
    )
    assert r.status_code == 404


async def test_csl_json_treats_organisation_as_literal(client: AsyncClient) -> None:
    slug = await _login(client)
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={
                "item_type": "report",
                "data": {
                    "title": "Org report",
                    "creators": [{"creatorType": "author", "name": "ACME Inc."}],
                },
            },
        )
    ).json()
    r = await client.get(
        f"/api/items/{item['id']}/export", params={"format": "csl-json"}
    )
    e = json.loads(r.text)[0]
    assert e["author"] == [{"literal": "ACME Inc."}]
