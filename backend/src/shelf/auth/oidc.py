"""OIDC code-flow plumbing.

Bootstraps an authlib OAuth registry from the configured providers and
provides `upsert_user_from_claims` — the side-effect-only function that
maps an OIDC userinfo payload onto a Shelf User+Identity, creating a
personal Space on first sight. The route handlers stay thin and the
upsert is unit-testable without an OIDC server.
"""

import uuid

from authlib.integrations.starlette_client import OAuth
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import OIDCProvider, settings
from ..models import Identity, Space, User


def build_oauth(providers: list[OIDCProvider]) -> OAuth:
    """Build an authlib OAuth registry. Separated from the module-level
    singleton so tests can construct a fresh registry per case."""
    oauth = OAuth()
    for p in providers:
        oauth.register(
            name=p.name,
            client_id=p.client_id,
            client_secret=p.client_secret,
            server_metadata_url=f"{p.issuer.rstrip('/')}/.well-known/openid-configuration",
            client_kwargs={"scope": " ".join(p.scopes)},
        )
    return oauth


oauth = build_oauth(settings.oidc_providers)


def provider_names() -> list[str]:
    return [p.name for p in settings.oidc_providers]


def is_known_provider(name: str) -> bool:
    return name in provider_names()


async def upsert_user_from_claims(
    db: AsyncSession,
    *,
    idp: str,
    sub: str,
    email: str,
    display_name: str,
) -> User:
    """Map an OIDC identity onto a Shelf User.

    Lookup order:
      1. Exact (idp, sub) match in `identities` → existing user.
      2. Match by email (case-insensitive via CITEXT) → link this idp to
         that user.
      3. Otherwise create a fresh User + personal Space, then link.

    Email-based linking is convenient for the single-user case and for
    moving from dev-login to OIDC without losing the existing user. In
    a multi-tenant deployment with untrusted email claims it would
    deserve a second look — providers like Authentik / Entra do verify
    email, so it's safe enough for Phase 1/2.
    """
    identity = (
        await db.execute(
            select(Identity).where(Identity.idp == idp, Identity.subject == sub)
        )
    ).scalar_one_or_none()

    if identity is not None:
        user = await db.get(User, identity.user_id)
        if user is None:
            raise RuntimeError(
                f"Identity {identity.id} references missing user {identity.user_id}"
            )
        return user

    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()

    if user is None:
        user = User(email=email, display_name=display_name)
        db.add(user)
        await db.flush()
        space = Space(
            slug=f"u-{uuid.UUID(str(user.id)).hex[:8]}",
            name=f"{user.display_name}'s shelf",
            owner_id=user.id,
        )
        db.add(space)

    db.add(Identity(user_id=user.id, idp=idp, subject=sub))
    await db.commit()
    await db.refresh(user)
    return user


def claims_to_email(claims: dict[str, object], idp: str, sub: str) -> str:
    """Pull an email out of OIDC userinfo claims, with a stable fallback.

    Most providers send `email`. If they don't (rare for OIDC; some
    minimal setups omit it), synthesise a stable per-(idp, sub) string
    so the User row still has a unique value. The user can update later.
    """
    email = claims.get("email")
    if isinstance(email, str) and "@" in email:
        return email
    return f"{sub}@{idp}.local"


def claims_to_display_name(claims: dict[str, object], email: str) -> str:
    for key in ("name", "preferred_username", "nickname"):
        v = claims.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return email.split("@", 1)[0]
