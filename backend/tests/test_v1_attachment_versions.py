"""Fetching an attachment's processed copies through the token API.

Shelf keeps an upload untouched and stores each OCR or outline pass as a
new copy beside it. The SPA reader shows the newest; the token API used
to hand out only the original, so a scanned standard downloaded through
the CLI came without the text layer shelf had already made for it.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from obstore.store import MemoryStore

from shelf.config import settings
from shelf.db import session_factory
from shelf.models import Attachment, AttachmentDerivation
from shelf.services import storage

from .helpers import get_me, login


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def memory_store(monkeypatch: pytest.MonkeyPatch) -> object:
    storage._store = MemoryStore()

    async def fake_presign(key: str, **_: object) -> str:
        return f"https://memory/{key}"

    monkeypatch.setattr(storage, "presign_upload", fake_presign)
    monkeypatch.setattr(storage, "presign_download", fake_presign)
    yield
    storage.reset_store()


async def _token(client: AsyncClient, scopes: list[str]) -> str:
    r = await client.post("/api/me/tokens", json={"name": "cli", "scopes": scopes})
    assert r.status_code == 201, r.text
    return str(r.json()["plaintext"])


@pytest.fixture
async def attachment(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> Attachment:
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    await login(client, "admin@example.com")
    slug = f"u-{(await get_me(client))['id'].replace('-', '')[:8]}"
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "standard", "data": {"title": "Scanned"}},
        )
    ).json()
    att = (
        await client.post(
            f"/api/items/{item['id']}/attachments",
            json={"filename": "scan.pdf", "content_type": "application/pdf"},
        )
    ).json()["attachment"]
    async with session_factory() as db:
        row = await db.get(Attachment, uuid.UUID(att["id"]))
        assert row is not None
        return row


async def _derive(att: Attachment, kind: str, minutes_ago: int) -> str:
    async with session_factory() as db:
        row = AttachmentDerivation(
            attachment_id=att.id,
            kind=kind,
            storage_key=f"{att.storage_key}/derived/{kind}-{minutes_ago}.pdf",
            parent_storage_key=att.storage_key,
            engine=f"{kind}/test",
            created_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
        )
        db.add(row)
        await db.commit()
        return str(row.id)


async def test_download_defaults_to_the_original(
    client: AsyncClient, attachment: Attachment
) -> None:
    await _derive(attachment, "ocr", 5)
    token = await _token(client, ["download"])
    r = await client.get(
        f"/api/v1/download/{attachment.id}", headers=_auth(token), follow_redirects=False
    )
    assert r.status_code == 302, r.text
    assert r.headers["location"] == f"https://memory/{attachment.storage_key}"
    assert r.headers["x-shelf-version"] == "original"


async def test_latest_is_what_the_reader_shows(
    client: AsyncClient, attachment: Attachment
) -> None:
    await _derive(attachment, "ocr", 10)
    outline = await _derive(attachment, "outline", 5)
    token = await _token(client, ["download"])
    r = await client.get(
        f"/api/v1/download/{attachment.id}",
        params={"version": "latest"},
        headers=_auth(token),
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    assert r.headers["x-shelf-version"] == outline
    assert r.headers["location"].endswith("/derived/outline-5.pdf")


async def test_latest_without_processing_is_the_original(
    client: AsyncClient, attachment: Attachment
) -> None:
    token = await _token(client, ["download"])
    r = await client.get(
        f"/api/v1/download/{attachment.id}",
        params={"version": "latest"},
        headers=_auth(token),
        follow_redirects=False,
    )
    assert r.headers["x-shelf-version"] == "original"


async def test_a_named_version(client: AsyncClient, attachment: Attachment) -> None:
    ocr = await _derive(attachment, "ocr", 10)
    await _derive(attachment, "outline", 5)
    token = await _token(client, ["download"])
    r = await client.get(
        f"/api/v1/download/{attachment.id}",
        params={"version": ocr},
        headers=_auth(token),
        follow_redirects=False,
    )
    assert r.headers["x-shelf-version"] == ocr
    assert r.headers["location"].endswith("/derived/ocr-10.pdf")


async def test_a_version_of_another_attachment_is_not_found(
    client: AsyncClient, attachment: Attachment
) -> None:
    token = await _token(client, ["download"])
    r = await client.get(
        f"/api/v1/download/{attachment.id}",
        params={"version": str(uuid.uuid4())},
        headers=_auth(token),
        follow_redirects=False,
    )
    assert r.status_code == 404


async def test_a_nonsense_version_is_rejected(
    client: AsyncClient, attachment: Attachment
) -> None:
    token = await _token(client, ["download"])
    r = await client.get(
        f"/api/v1/download/{attachment.id}",
        params={"version": "newest"},
        headers=_auth(token),
        follow_redirects=False,
    )
    assert r.status_code == 400


async def test_versions_lists_newest_first(
    client: AsyncClient, attachment: Attachment
) -> None:
    ocr = await _derive(attachment, "ocr", 10)
    outline = await _derive(attachment, "outline", 5)
    token = await _token(client, ["search"])
    r = await client.get(
        f"/api/v1/attachments/{attachment.id}/versions", headers=_auth(token)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["latest"] == outline
    assert [(v["id"], v["kind"], v["engine"]) for v in body["versions"]] == [
        (outline, "outline", "outline/test"),
        (ocr, "ocr", "ocr/test"),
    ]
    # Storage keys stay server-side.
    assert "storage_key" not in body["versions"][0]
    assert "parent_storage_key" not in body["versions"][0]


async def test_versions_of_an_unprocessed_upload(
    client: AsyncClient, attachment: Attachment
) -> None:
    token = await _token(client, ["search"])
    r = await client.get(
        f"/api/v1/attachments/{attachment.id}/versions", headers=_auth(token)
    )
    assert r.json() == {"filename": "scan.pdf", "latest": "original", "versions": []}


async def test_versions_needs_the_search_scope(
    client: AsyncClient, attachment: Attachment
) -> None:
    token = await _token(client, ["download"])
    r = await client.get(
        f"/api/v1/attachments/{attachment.id}/versions", headers=_auth(token)
    )
    assert r.status_code == 403


async def test_versions_of_someone_elses_attachment_is_not_found(
    client: AsyncClient, attachment: Attachment
) -> None:
    await login(client, "mallory@example.com")
    token = await _token(client, ["search", "download"])
    r = await client.get(
        f"/api/v1/attachments/{attachment.id}/versions", headers=_auth(token)
    )
    assert r.status_code == 404
    r = await client.get(
        f"/api/v1/download/{attachment.id}",
        params={"version": "latest"},
        headers=_auth(token),
        follow_redirects=False,
    )
    assert r.status_code == 404
