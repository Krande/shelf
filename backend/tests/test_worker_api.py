"""Token-auth API for external worker pods (Option 2 broker).

Covers:
  * Public token endpoint refuses to mint ``worker`` scope.
  * GET /api/v1/worker/attachments/{id} requires the worker scope
    and returns the slim DTO.
  * GET /api/v1/worker/processing/{id} returns 404 for an unset row.
  * POST /api/v1/worker/processing/{id} performs partial UPSERT and
    rejects unknown / non-writable keys.
  * The HttpBackend in shelf.worker._db_client round-trips against
    the same endpoints.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from shelf.auth.tokens import mint
from shelf.db import session_factory
from shelf.models import ApiToken, AttachmentProcessing, User


async def _login(client: AsyncClient, email: str = "alice@example.com") -> str:
    await client.post("/auth/dev-login", json={"email": email})
    me = (await client.get("/api/me")).json()
    return f"u-{me['id'].replace('-', '')[:8]}"


async def _make_pdf_attachment(client: AsyncClient, slug: str) -> dict:
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "T"}},
        )
    ).json()
    att = (
        await client.post(
            f"/api/items/{item['id']}/attachments",
            json={"filename": "p.pdf", "content_type": "application/pdf"},
        )
    ).json()["attachment"]
    await client.post(f"/api/attachments/{att['id']}/complete")
    return att


async def _mint_worker_token(email: str = "worker@shelf.local") -> str:
    """Insert a worker-scope token directly. Mirrors what the
    ``shelf.scripts.mint_worker_token`` CLI does."""
    async with session_factory() as db:
        from sqlalchemy import select

        user = (
            await db.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if user is None:
            user = User(email=email, display_name="Worker")
            db.add(user)
            await db.flush()
        m = mint()
        token = ApiToken(
            user_id=user.id,
            name="test-worker",
            token_hash=m.token_hash,
            prefix=m.prefix,
            scopes=["worker"],
        )
        db.add(token)
        await db.commit()
        return m.plaintext


# ── Auth gate ────────────────────────────────────────────────────────────────


async def test_public_mint_endpoint_rejects_worker_scope(
    client: AsyncClient,
) -> None:
    await _login(client)
    r = await client.post(
        "/api/me/tokens",
        json={"name": "rogue", "scopes": ["worker"]},
    )
    # 400 = our explicit operator-only / valid-scope gate fires;
    # 422 = Pydantic's Scope Literal validator rejects first.
    # Either is fine — the user can't mint a worker scope through
    # the public endpoint.
    assert r.status_code in (400, 422)
    assert "worker" in r.text


async def test_worker_endpoint_rejects_user_scope_token(
    client: AsyncClient,
) -> None:
    """A token without the worker scope can't reach the worker
    endpoints, even from a logged-in user with valid cookies."""
    await _login(client)
    # Mint a normal upload-scope token.
    r = await client.post(
        "/api/me/tokens",
        json={"name": "upload-only", "scopes": ["upload"]},
    )
    assert r.status_code == 201
    plaintext = r.json()["plaintext"]

    # The worker endpoint refuses despite a valid bearer token.
    r = await client.get(
        f"/api/v1/worker/attachments/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert r.status_code == 403
    assert "worker" in r.json()["detail"]


async def test_worker_endpoint_rejects_missing_bearer(
    client: AsyncClient,
) -> None:
    r = await client.get(f"/api/v1/worker/attachments/{uuid.uuid4()}")
    assert r.status_code == 401


# ── Read-side ───────────────────────────────────────────────────────────────


async def test_worker_get_attachment_returns_dto(
    client: AsyncClient,
) -> None:
    token = await _mint_worker_token()
    slug = await _login(client)
    att = await _make_pdf_attachment(client, slug)

    r = await client.get(
        f"/api/v1/worker/attachments/{att['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == att["id"]
    assert body["filename"] == "p.pdf"
    assert body["content_type"] == "application/pdf"
    assert body["uploaded_at"] is not None  # complete() stamped it
    assert "storage_key" in body


async def test_worker_get_processing_404_when_unset(
    client: AsyncClient,
) -> None:
    token = await _mint_worker_token()
    slug = await _login(client)
    att = await _make_pdf_attachment(client, slug)

    # Ensure no processing row exists yet.
    async with session_factory() as db:
        existing = await db.get(AttachmentProcessing, uuid.UUID(att["id"]))
        if existing is not None:
            await db.delete(existing)
            await db.commit()

    r = await client.get(
        f"/api/v1/worker/processing/{att['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404


# ── Write-side (UPSERT) ─────────────────────────────────────────────────────


async def test_worker_upsert_creates_row_then_updates(
    client: AsyncClient,
) -> None:
    token = await _mint_worker_token()
    slug = await _login(client)
    att = await _make_pdf_attachment(client, slug)

    # Initial UPSERT — row didn't exist, gets created.
    r = await client.post(
        f"/api/v1/worker/processing/{att['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"ocr_status": "running", "ocr_engine": "test/0.1 shelf/x"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ocr_status"] == "running"
    assert r.json()["ocr_engine"] == "test/0.1 shelf/x"

    # Subsequent UPSERT — patches selected fields, leaves others.
    r = await client.post(
        f"/api/v1/worker/processing/{att['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"progress_done": 17, "progress_total": 198},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["progress_done"] == 17
    assert body["progress_total"] == 198
    # Earlier write survived.
    assert body["ocr_status"] == "running"


async def test_worker_upsert_clear_nulls_listed_fields(
    client: AsyncClient,
) -> None:
    token = await _mint_worker_token()
    slug = await _login(client)
    att = await _make_pdf_attachment(client, slug)

    await client.post(
        f"/api/v1/worker/processing/{att['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"progress_done": 5, "progress_total": 10},
    )
    # Now clear progress on terminal-state transition.
    r = await client.post(
        f"/api/v1/worker/processing/{att['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "ocr_status": "done",
            "clear": ["progress_done", "progress_total"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ocr_status"] == "done"
    assert body["progress_done"] is None
    assert body["progress_total"] is None


async def test_worker_upsert_rejects_unknown_field(
    client: AsyncClient,
) -> None:
    """Pydantic's extra='forbid' surfaces unknown keys as a 422."""
    token = await _mint_worker_token()
    slug = await _login(client)
    att = await _make_pdf_attachment(client, slug)

    r = await client.post(
        f"/api/v1/worker/processing/{att['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"text_chars": 99999},  # not in WorkerProcessingUpsert
    )
    assert r.status_code == 422


async def test_worker_upsert_rejects_clear_of_non_writable_field(
    client: AsyncClient,
) -> None:
    token = await _mint_worker_token()
    slug = await _login(client)
    att = await _make_pdf_attachment(client, slug)

    r = await client.post(
        f"/api/v1/worker/processing/{att['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"clear": ["page_count"]},  # reserved for extract worker
    )
    assert r.status_code == 400
    assert "page_count" in r.json()["detail"]


# ── HttpBackend (worker-side client) ─────────────────────────────────────────


async def test_http_backend_round_trips_against_real_endpoints(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spin up the HttpBackend with a httpx client routed at the
    in-process FastAPI app. Confirms the worker's outbound shape
    matches what the API expects."""
    from shelf.worker import _db_client as dbm

    token = await _mint_worker_token()
    slug = await _login(client)
    att = await _make_pdf_attachment(client, slug)
    aid = uuid.UUID(att["id"])

    backend = dbm.HttpBackend(
        base_url="http://test", token=token, timeout=10.0
    )

    # Patch httpx.AsyncClient inside the backend to reuse the same
    # ASGITransport the test client is using.
    import httpx

    from shelf.main import app

    real_async_client = httpx.AsyncClient

    def make_test_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return real_async_client(
            transport=ASGITransport(app=app),
            base_url="http://test",
            timeout=kwargs.get("timeout", 10.0),
        )

    monkeypatch.setattr(httpx, "AsyncClient", make_test_client)

    # 1. get_attachment.
    dto = await backend.get_attachment(aid)
    assert dto is not None
    assert dto.id == aid
    assert dto.content_type == "application/pdf"

    # 2. get_processing on a fresh row → None (404).
    async with session_factory() as db:
        existing = await db.get(AttachmentProcessing, aid)
        if existing is not None:
            await db.delete(existing)
            await db.commit()
    proc = await backend.get_processing(aid)
    assert proc is None

    # 3. upsert_processing then read back.
    now = datetime.now(UTC)
    await backend.upsert_processing(
        aid,
        {
            "ocr_status": "running",
            "ocr_engine": "olmocr/0.1 shelf/sha-test",
            "progress_done": 3,
            "progress_total": 12,
        },
    )
    proc = await backend.get_processing(aid)
    assert proc is not None
    assert proc.ocr_status == "running"
    assert proc.progress_done == 3
    assert proc.progress_total == 12

    # 4. clear semantics.
    await backend.upsert_processing(
        aid,
        {"ocr_status": "done", "ocr_completed_at": now},
        clear=["progress_done", "progress_total"],
    )
    proc = await backend.get_processing(aid)
    assert proc is not None
    assert proc.ocr_status == "done"
    assert proc.progress_done is None
    assert proc.progress_total is None
