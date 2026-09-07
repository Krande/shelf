"""API token helpers — mint, verify, dependency.

Tokens are opaque random bytes encoded as `shelf_<48 hex>` and only
ever returned to the caller once. We store the SHA-256 of the
plaintext (the input has 192 bits of entropy, so a single hash is
fine) plus a 12-char prefix for display.

Scopes are coarse labels: "upload" | "search" | "download". Tokens
optionally carry `allowed_collection_ids` to restrict access to a
subset of the user's collections.
"""

import hashlib
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated, NamedTuple

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from ..db import get_session
from ..models import ApiToken, User

_PLAINTEXT_PREFIX = "shelf_"
_PLAINTEXT_RAND_HEX = 48
_PLAINTEXT_LEN = len(_PLAINTEXT_PREFIX) + _PLAINTEXT_RAND_HEX  # 54
_DISPLAY_PREFIX_LEN = 12


class MintedToken(NamedTuple):
    plaintext: str
    token_hash: str
    prefix: str


def mint() -> MintedToken:
    raw = secrets.token_hex(_PLAINTEXT_RAND_HEX // 2)
    plaintext = _PLAINTEXT_PREFIX + raw
    h = hashlib.sha256(plaintext.encode()).hexdigest()
    return MintedToken(plaintext, h, plaintext[:_DISPLAY_PREFIX_LEN])


def hash_for(plaintext: str) -> str | None:
    """Return the storage hash for a token plaintext, or None if the
    format is obviously invalid. Returning None on bad format lets
    callers 401 without leaking timing information about valid hashes
    via the lookup path."""
    if not plaintext.startswith(_PLAINTEXT_PREFIX):
        return None
    if len(plaintext) != _PLAINTEXT_LEN:
        return None
    return hashlib.sha256(plaintext.encode()).hexdigest()


class TokenAuth(NamedTuple):
    user: User
    token: ApiToken


async def _resolve_bearer(
    request: Request, db: AsyncSession
) -> TokenAuth:
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    plaintext = auth.split(None, 1)[1].strip()
    h = hash_for(plaintext)
    if h is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")

    token = (
        await db.execute(select(ApiToken).where(ApiToken.token_hash == h))
    ).scalar_one_or_none()
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    now = datetime.now(UTC)
    if token.expires_at is not None and token.expires_at < now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")

    user = await db.get(User, token.user_id)
    if user is None:
        # Stale token whose user was deleted — treat as invalid rather
        # than 500.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")

    # Best-effort touch — we accept the cost of a write per request
    # because fleet-wide token activity is the only signal a user has
    # to spot a leaked token. Switch to an async background task if
    # this ever shows up in latency budgets.
    token.last_used_at = now
    await db.commit()

    return TokenAuth(user=user, token=token)


def require_scope(
    scope: str,
) -> Callable[[Request, AsyncSession], Awaitable[TokenAuth]]:
    """Dependency factory that returns (user, token) only when the
    bearer carries the named scope."""

    async def dep(
        request: Request,
        db: Annotated[AsyncSession, Depends(get_session)],
    ) -> TokenAuth:
        auth = await _resolve_bearer(request, db)
        if scope not in auth.token.scopes:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Token missing required scope: {scope}",
            )
        return auth

    return dep


async def require_worker_token(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ApiToken:
    """Worker-scope token auth.

    Returns just the token (not a user/token pair) because workers
    operate on any user's attachments — the per-user filter that
    other scopes apply doesn't make sense here. The token is still
    issued to a user (any user, doesn't matter which); we only check
    that the token carries the ``worker`` scope and isn't expired.

    Worker tokens are minted by the operator out-of-band via
    ``python -m shelf.admin.mint_worker_token``; the public token
    endpoint refuses to mint them so a normal user can't escalate.
    """
    auth = await _resolve_bearer(request, db)
    if "worker" not in auth.token.scopes:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Token missing required scope: worker",
        )
    return auth.token
