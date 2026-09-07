from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..models import User
from .session import InvalidSessionError, parse_session


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_session)],
    session_token: Annotated[str | None, Cookie(alias=settings.session_cookie_name)] = None,
) -> User:
    if not session_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        user_id = parse_session(session_token)
    except InvalidSessionError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid session: {e}") from e
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return user
