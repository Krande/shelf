"""Pytest fixtures.

The `client` fixture spins up an httpx.AsyncClient that runs the FastAPI
ASGI app inline (no separate event loop, no socket), and TRUNCATEs the
core tables before each test so state does not leak between tests.

Tests that don't request `client` skip the DB reset entirely — keeping
unit tests (e.g. JWT roundtrip, storage in-memory) fast and DB-free.

**The suite runs against its own database, always.** `_as_test_database`
rewrites whatever `SHELF_DATABASE_URL` names into a sibling ending in
`_test`, and does it before `shelf.db` builds the engine, so the app
under test and the TRUNCATE below address the same throwaway database.
There is deliberately no way to opt out: this fixture destroys every row
it can reach, and the default URL points at the database `pixi run up`
serves. Pointing the two at one database wipes a running dev stack's
data and logs out its sessions, which is easy to do by accident and
gives no hint of what happened.

The database is created and migrated on first use, so a fresh checkout
needs no setup step and a new migration is picked up without one.
"""

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from shelf.config import settings


def _as_test_database(url: str) -> str:
    """Rewrite a connection URL to name a `_test` database.

    Idempotent, so an already-suffixed URL is left alone rather than
    growing a second one.
    """
    parts = urlsplit(url)
    name = parts.path.lstrip("/")
    if not name:
        raise RuntimeError(f"No database name in SHELF_DATABASE_URL: {url!r}")
    if not name.endswith("_test"):
        name = f"{name}_test"
    return urlunsplit(parts._replace(path=f"/{name}"))


# Before `shelf.db` is imported, so the engine it builds at import time —
# the one the app under test uses — points at the test database too.
settings.database_url = _as_test_database(settings.database_url)
# Alembic reads settings via the environment in its own process.
os.environ["SHELF_DATABASE_URL"] = settings.database_url

from shelf.db import engine  # noqa: E402  -- must follow the redirect above
from shelf.main import app  # noqa: E402  -- imports shelf.db in turn

_BACKEND_DIR = Path(__file__).resolve().parent.parent


async def _create_database_if_missing(url: str) -> None:
    """CREATE DATABASE, connecting to `postgres` to do it.

    A database cannot be created from inside itself, and the target is
    the one that does not exist yet.
    """
    parts = urlsplit(url)
    name = parts.path.lstrip("/")
    admin = create_async_engine(
        urlunsplit(parts._replace(path="/postgres")),
        isolation_level="AUTOCOMMIT",
    )
    try:
        async with admin.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": name},
            )
            if not exists:
                # The name is derived from our own configuration rather
                # than from anything a test supplies, and identifiers
                # cannot be bound as parameters.
                await conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def _test_database_ready() -> None:
    """Create the test database if needed and bring it to head.

    A subprocess because alembic's env.py calls `asyncio.run`, which
    cannot be re-entered from inside a running loop. Cheap when there is
    nothing to apply, and it means `pytest` works on a clean checkout
    without a separate migrate step.
    """
    asyncio.run(_create_database_if_missing(settings.database_url))
    done = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        # Not check=True: a CalledProcessError here reports the exit code
        # and hides the traceback that says which migration broke, which
        # is the only part worth reading.
        raise RuntimeError(
            "Could not migrate the test database "
            f"({settings.database_url}):\n{done.stdout}\n{done.stderr}"
        )


@pytest.fixture(autouse=True)
def _shipped_role_defaults(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin the role knobs to their shipped defaults for every test.

    `pixi run test` reads backend/.env, and `pixi run up` encourages
    SHELF_DEV_LOGIN_ROLE=admin locally — without this, a developer whose
    .env carries it would see every "a new account is not an admin"
    assertion fail for reasons that have nothing to do with their change.
    Tests that care about other values set them explicitly.
    """
    monkeypatch.setattr(settings, "dev_login_role", "user")
    monkeypatch.setattr(settings, "admin_emails", [])
    yield

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
