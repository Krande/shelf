"""The instance user directory.

Backs the member picker in Settings -> Spaces: adding someone to a space
means choosing them, and choosing means seeing who exists.

**Visible to every authenticated user**, not just admins, because
everyone owns their personal space and so everyone may need to share one.
That is a deliberate trade: on a self-hosted instance the people with
accounts are colleagues, and a roster of names and addresses among them
is unremarkable. It does mean any account can enumerate the others, so an
instance handing out accounts to people who shouldn't see each other
wants something narrower than this.

Only id, email and display name are exposed — never roles, never
identities, never anything about what a user has in their library.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import get_current_user
from ..db import get_session
from ..models import User

router = APIRouter(tags=["users"])


class DirectoryUser(BaseModel):
    id: str
    email: str
    display_name: str


@router.get("/api/users", response_model=list[DirectoryUser])
async def list_users(
    _caller: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[DirectoryUser]:
    """Everyone with an account, for picking from.

    `q` filters on display name or email; the picker sends the whole list
    on a small instance and can switch to typeahead if one ever gets big
    enough to want it.
    """
    stmt = select(User)
    if q and q.strip():
        needle = f"%{_escape_ilike(q.strip())}%"
        stmt = stmt.where(
            or_(
                User.display_name.ilike(needle, escape="\\"),
                # email is CITEXT, so ilike is redundant on it but
                # harmless, and keeps the two clauses symmetrical.
                User.email.ilike(needle, escape="\\"),
            )
        )
    # Case-insensitively: under a C-collation database "Zoe" sorts before
    # "ada", which reads as unsorted in a dropdown of people's names.
    stmt = stmt.order_by(
        func.lower(User.display_name), func.lower(User.email)
    ).limit(limit)

    return [
        DirectoryUser(
            id=str(u.id), email=u.email, display_name=u.display_name
        )
        for u in (await db.execute(stmt)).scalars().all()
    ]


def _escape_ilike(s: str) -> str:
    """Quote LIKE wildcards so a typed `%` matches a literal one rather
    than everything. Same treatment as the item search in items.py."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
