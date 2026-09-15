from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..models import User
from .session import InvalidSessionError, SessionClaims, parse_session

SessionCookie = Annotated[str | None, Cookie(alias=settings.session_cookie_name)]


def read_session(token: str | None) -> SessionClaims:
    """Decode a session cookie or raise 401. Shared by the dependencies
    below and by the auth routes, which need the claims before they have
    a user."""
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        return parse_session(token)
    except InvalidSessionError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid session: {e}") from e


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_session)],
    session_token: SessionCookie = None,
) -> User:
    claims = read_session(session_token)
    user = (
        await db.execute(select(User).where(User.id == claims.active_user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return user


async def get_session_claims(session_token: SessionCookie = None) -> SessionClaims:
    """The raw session, for the handful of routes that care about the
    whole linked set rather than just who's active.

    Kept separate from `get_current_user` on purpose: that one is
    consumed identically by every authenticated route in the app, and
    widening its return type would touch all of them for the benefit of
    three.
    """
    return read_session(session_token)
