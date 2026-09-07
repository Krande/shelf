"""Pytest fixtures.

The `client` fixture spins up an httpx.AsyncClient that runs the FastAPI
ASGI app inline (no separate event loop, no socket), and TRUNCATEs the
core tables before each test so state does not leak between tests.

Tests that don't request `client` skip the DB reset entirely — keeping
unit tests (e.g. JWT roundtrip, storage in-memory) fast and DB-free.

Assumes Postgres is reachable at SHELF_DATABASE_URL and the schema has
been migrated (`pixi run alembic-up`).
"""

from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from shelf.db import engine
from shelf.main import app

_TABLES_TO_TRUNCATE = (
    "api_tokens",
    "annotations",
    "attachment_derivation",
    "attachment_pages",
    "attachment_processing",
    "attachments",
    "item_tags",
    "tags",
    "notes",
    "item_collections",
    "collections",
    "items",
    "space_memberships",
    "spaces",
    "identities",
    "users",
)


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with engine.begin() as conn:
        await conn.execute(
            text(f"TRUNCATE TABLE {', '.join(_TABLES_TO_TRUNCATE)} RESTART IDENTITY CASCADE")
        )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
