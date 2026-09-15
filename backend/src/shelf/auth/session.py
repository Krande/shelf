"""HMAC-signed session JWTs.

Issued on login, carried in an HttpOnly cookie, validated on every
authenticated request. The payload stays small: `sub` (the *active* user
id), `accts` (every identity linked to this browser session), `iat`,
`exp`.

`accts` is what makes account switching work. It only ever grows from a
completed OIDC callback in this browser, and it's inside the signature,
so a cookie can't be edited into granting access to an account its owner
never authenticated as. Switching picks a different `sub` out of the
list it already holds.

Contrast with the admin role, which is *not* in here and is re-read from
the database per request (see `roles.py`): a role change should land
immediately, whereas the linked set is a property of this login session
and lives and dies with it.

There is no server-side session store, so a token stays valid until
`exp`. That's why switching accounts re-uses the original expiry instead
of minting a fresh TTL — otherwise a switch every 23 hours would keep
one session alive forever.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from authlib.jose import JoseError, jwt

from ..config import settings


class InvalidSessionError(Exception):
    """Raised when a session token is missing, tampered, or expired."""


@dataclass(frozen=True)
class SessionClaims:
    """Decoded session payload."""

    active_user_id: uuid.UUID
    account_ids: tuple[uuid.UUID, ...]
    expires_at: datetime


def issue_session(
    user_id: uuid.UUID,
    *,
    account_ids: tuple[uuid.UUID, ...] | list[uuid.UUID] | None = None,
    ttl: timedelta | None = None,
    expires_at: datetime | None = None,
) -> str:
    """Mint a session token.

    `account_ids` defaults to just `user_id` — a plain login. Pass the
    existing set (plus the new arrival) to link an account.

    `expires_at` pins the expiry instead of deriving it from `ttl`, so a
    switch can carry the original one forward.
    """
    now = datetime.now(UTC)
    if expires_at is None:
        ttl = ttl if ttl is not None else timedelta(seconds=settings.session_ttl_seconds)
        expires_at = now + ttl

    ids = tuple(account_ids) if account_ids is not None else (user_id,)
    ids = dedupe_accounts(ids, active=user_id)

    payload = {
        "sub": str(user_id),
        "accts": [str(i) for i in ids],
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    header = {"alg": "HS256"}
    token: bytes = jwt.encode(header, payload, settings.session_secret_key.encode())
    return token.decode()


def dedupe_accounts(
    ids: tuple[uuid.UUID, ...] | list[uuid.UUID],
    *,
    active: uuid.UUID,
) -> tuple[uuid.UUID, ...]:
    """Order-preserving dedupe, guaranteeing `active` is present and
    trimming to `max_linked_accounts`.

    The active account is never the one dropped by the cap: it's moved to
    the front before trimming, so linking a ninth account evicts the
    least recently added rather than the one you just signed in as.
    """
    ordered = [active] + [i for i in ids if i != active]
    seen: set[uuid.UUID] = set()
    out: list[uuid.UUID] = []
    for i in ordered:
        if i in seen:
            continue
        seen.add(i)
        out.append(i)
    return tuple(out[: max(1, settings.max_linked_accounts)])


def parse_session(token: str) -> SessionClaims:
    try:
        claims = jwt.decode(token, settings.session_secret_key.encode())
        claims.validate()
    except JoseError as e:
        raise InvalidSessionError(str(e)) from e

    sub = claims.get("sub")
    if not isinstance(sub, str):
        raise InvalidSessionError("sub claim missing or not a string")
    try:
        active = uuid.UUID(sub)
    except ValueError as e:
        raise InvalidSessionError("sub is not a UUID") from e

    exp = claims.get("exp")
    if not isinstance(exp, int):
        raise InvalidSessionError("exp claim missing or not an integer")

    # Tokens minted before account linking existed carry no `accts`. Treat
    # them as a single-account session rather than rejecting them, so an
    # upgrade doesn't log everybody out. Anything unparseable in the list
    # is dropped: a malformed entry shouldn't invalidate a valid session,
    # and it can't grant anything either way since every id is re-checked
    # against the database before use.
    raw = claims.get("accts")
    ids: list[uuid.UUID] = []
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, str):
                continue
            try:
                ids.append(uuid.UUID(entry))
            except ValueError:
                continue

    return SessionClaims(
        active_user_id=active,
        account_ids=dedupe_accounts(ids, active=active),
        expires_at=datetime.fromtimestamp(exp, tz=UTC),
    )
