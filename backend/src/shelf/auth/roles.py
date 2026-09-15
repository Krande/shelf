"""Instance-wide roles.

Two halves: the bootstrap that promotes configured emails on login, and
the FastAPI dependency that gates admin-only routes.

The role lives on the user row and is re-read on every request rather
than being baked into the session JWT. That's deliberate — a demotion
takes effect on the next request instead of whenever the session happens
to expire. The linked-account list in the same session goes the other
way (see `session.py`); the asymmetry is the point.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import ROLE_ADMIN, User
from .deps import get_current_user


async def apply_admin_bootstrap(db: AsyncSession, user: User) -> User:
    """Promote `user` to admin if their email is in SHELF_ADMIN_EMAILS.

    Promote-only: a user whose email is no longer listed keeps the role.
    Removing admin is done through the admin UI (or by editing the row),
    so the env var is a way in rather than a source of truth that fights
    with in-app changes.
    """
    if user.role == ROLE_ADMIN:
        return user
    listed = {e.strip().casefold() for e in settings.admin_emails if e.strip()}
    if user.email.casefold() not in listed:
        return user
    user.role = ROLE_ADMIN
    await db.commit()
    await db.refresh(user)
    return user


async def require_admin(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Cookie-path sibling of `tokens.require_scope` — 403 unless admin.

    Bearer tokens deliberately can't satisfy this: admin is a session
    concern, and no token scope grants it.
    """
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
    return user
