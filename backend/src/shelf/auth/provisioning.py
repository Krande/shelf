"""Minting a user row.

Three paths create users — the OIDC callback, the dev-login shortcut, and
the admin panel's "add by email" — and all three owe the new account the
same thing: a row plus the personal space that everything else assumes
exists. Keeping that in one place means a fourth path can't forget the
space and leave an account that can't hold anything.

Deliberately does not commit. The OIDC path has an `Identity` row to add
in the same transaction, so the flush-now-commit-later split is what lets
a failure there roll the user back too.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ROLE_USER, Space, User


def default_display_name(email: str) -> str:
    """The local part, for when nothing better was supplied."""
    return email.split("@", 1)[0]


async def create_user_with_personal_space(
    db: AsyncSession,
    *,
    email: str,
    display_name: str | None = None,
    role: str = ROLE_USER,
) -> User:
    """Add a `User` and their personal `Space`, flushed but not committed.

    The slug follows the `u-<8 hex>` shape `api/spaces.py` reserves, so a
    hand-named space can never collide with one of these.
    """
    user = User(
        email=email,
        display_name=display_name or default_display_name(email),
        role=role,
    )
    db.add(user)
    # Needed before the space: the slug and the FK both read user.id.
    await db.flush()
    db.add(
        Space(
            slug=f"u-{uuid.UUID(str(user.id)).hex[:8]}",
            name=f"{user.display_name}'s shelf",
            owner_id=user.id,
        )
    )
    return user
