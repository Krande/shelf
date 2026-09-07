"""Unit tests for the OIDC user-upsert helper.

The HTTP code-flow itself is exercised end-to-end against a real
provider (Authentik / Azure AD) — mocking authlib's redirect dance is
more work than the test is worth. What we *do* care about and unit-test
is the deterministic mapping from claims onto User+Identity+Space rows.
"""

from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from shelf.auth.oidc import (
    claims_to_display_name,
    claims_to_email,
    upsert_user_from_claims,
)
from shelf.config import settings
from shelf.models import Identity, Space, User


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """Per-test isolated DB session, on the same Postgres as the rest of
    the suite. Truncates the relevant tables on entry."""
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE TABLE items, space_memberships, spaces, identities, users "
                "RESTART IDENTITY CASCADE"
            )
        )
    async with session_factory() as session:
        yield session
    await engine.dispose()


def test_claims_to_email_prefers_email_field() -> None:
    assert claims_to_email({"email": "a@b.com"}, idp="authentik", sub="x") == "a@b.com"


def test_claims_to_email_falls_back_when_missing() -> None:
    assert claims_to_email({}, idp="authentik", sub="abc") == "abc@authentik.local"


def test_claims_to_display_name_prefers_name() -> None:
    assert claims_to_display_name({"name": "Alice"}, email="a@b.com") == "Alice"


def test_claims_to_display_name_falls_back_to_email_local_part() -> None:
    assert claims_to_display_name({}, email="alice@b.com") == "alice"


async def test_upsert_creates_user_identity_and_personal_space(db: AsyncSession) -> None:
    user = await upsert_user_from_claims(
        db, idp="authentik", sub="auth-1", email="alice@example.com", display_name="Alice"
    )
    assert user.email == "alice@example.com"

    identity = (
        await db.execute(
            select(Identity).where(Identity.idp == "authentik", Identity.subject == "auth-1")
        )
    ).scalar_one()
    assert identity.user_id == user.id

    space = (
        await db.execute(select(Space).where(Space.owner_id == user.id))
    ).scalar_one()
    assert space.slug.startswith("u-")
    assert space.name == "Alice's shelf"


async def test_upsert_returns_existing_user_for_known_identity(db: AsyncSession) -> None:
    first = await upsert_user_from_claims(
        db, idp="authentik", sub="auth-1", email="alice@example.com", display_name="Alice"
    )
    second = await upsert_user_from_claims(
        db, idp="authentik", sub="auth-1", email="alice@example.com", display_name="Different"
    )
    assert first.id == second.id

    identity_count = (
        await db.execute(
            select(Identity).where(Identity.user_id == first.id)
        )
    ).all()
    assert len(identity_count) == 1


async def test_upsert_links_existing_user_by_email_on_first_login(db: AsyncSession) -> None:
    """Email-based linking lets a dev-login-created user keep working
    when they switch to OIDC, and lets a user log in via Authentik or
    Azure AD interchangeably without ending up with two separate User
    rows."""
    seed = User(email="bob@example.com", display_name="Bob")
    db.add(seed)
    await db.commit()
    await db.refresh(seed)

    linked = await upsert_user_from_claims(
        db, idp="authentik", sub="auth-bob", email="bob@example.com", display_name="Bob"
    )
    assert linked.id == seed.id

    identities = (
        await db.execute(select(Identity).where(Identity.user_id == seed.id))
    ).scalars().all()
    assert len(identities) == 1
    assert identities[0].idp == "authentik"
    assert identities[0].subject == "auth-bob"
