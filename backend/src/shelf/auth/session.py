"""HMAC-signed session JWTs.

Issued on login, carried in an HttpOnly cookie, validated on every
authenticated request. Payload is intentionally tiny: just `sub` (user
id), `iat`, `exp`.
"""

import uuid
from datetime import UTC, datetime, timedelta

from authlib.jose import JoseError, jwt

from ..config import settings


class InvalidSessionError(Exception):
    """Raised when a session token is missing, tampered, or expired."""


def issue_session(user_id: uuid.UUID, ttl: timedelta | None = None) -> str:
    ttl = ttl if ttl is not None else timedelta(seconds=settings.session_ttl_seconds)
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    header = {"alg": "HS256"}
    token: bytes = jwt.encode(header, payload, settings.session_secret_key.encode())
    return token.decode()


def parse_session(token: str) -> uuid.UUID:
    try:
        claims = jwt.decode(token, settings.session_secret_key.encode())
        claims.validate()
    except JoseError as e:
        raise InvalidSessionError(str(e)) from e
    sub = claims.get("sub")
    if not isinstance(sub, str):
        raise InvalidSessionError("sub claim missing or not a string")
    try:
        return uuid.UUID(sub)
    except ValueError as e:
        raise InvalidSessionError("sub is not a UUID") from e
