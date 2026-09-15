"""Per-space authorization.

Until now every check in the app was the same line — "does the caller own
the space that owns this row?" — repeated in nine files. Spaces are the
unit of sharing, so that line becomes a role lookup, and it lives here
once rather than in each router's private `_resolve_*` helper.

Three roles, ranked:

    viewer   read items, attachments, notes, tags, search, export
    editor   + create, update, delete content in the space
    owner    + manage members, rename and delete the space itself

`Space.owner_id` stays authoritative for the creator: they are always
owner, with no membership row, and cannot be demoted by editing
`space_memberships`. Everyone else's role comes from that table.

**Instance admins get nothing here.** Being able to hand out roles is not
the same as being able to read everybody's library, and quietly granting
the latter would make the admin role far more dangerous than it looks.
An admin who needs access to a space asks its owner, like anyone else.

On failure the distinction matters: a caller with no role at all gets 404
(a space they cannot see should not be confirmed to exist), while a
caller whose role is merely too low for the operation gets 403 — they
already know the space is there.
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import Select, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Space, SpaceMembership

SPACE_ROLE_VIEWER = "viewer"
SPACE_ROLE_EDITOR = "editor"
SPACE_ROLE_OWNER = "owner"

# Ordered least to most privileged; the index is the rank.
SPACE_ROLES: tuple[str, ...] = (
    SPACE_ROLE_VIEWER,
    SPACE_ROLE_EDITOR,
    SPACE_ROLE_OWNER,
)

# Roles that can be handed out. Owner is excluded: it belongs to the
# creator via Space.owner_id, and a second owner would have no meaning
# the code could act on.
ASSIGNABLE_SPACE_ROLES: tuple[str, ...] = (SPACE_ROLE_VIEWER, SPACE_ROLE_EDITOR)


def rank(role: str | None) -> int:
    """Privilege rank; -1 for "no role at all", so comparisons work
    without special-casing None at each call site."""
    if role is None:
        return -1
    try:
        return SPACE_ROLES.index(role)
    except ValueError:
        # A role the code doesn't know about grants nothing. The CHECK
        # constraint should make this unreachable; treating it as
        # no-access is the safe reading if it ever isn't.
        return -1


async def space_role(
    db: AsyncSession, space: Space, user_id: uuid.UUID
) -> str | None:
    """The caller's effective role in `space`, or None if they have none."""
    if space.owner_id == user_id:
        return SPACE_ROLE_OWNER
    membership = await db.get(SpaceMembership, (space.id, user_id))
    return membership.role if membership is not None else None


async def require_space_role(
    db: AsyncSession,
    space: Space | None,
    user_id: uuid.UUID,
    minimum: str,
    *,
    label: str = "Not found",
) -> str:
    """Authorize `user_id` for `minimum` on `space`, returning their
    actual role.

    `space` may be None so callers can hand over the result of a lookup
    that found nothing and get the same 404 either way — a missing space
    and an invisible one are deliberately indistinguishable.
    """
    if space is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, label)
    role = await space_role(db, space, user_id)
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, label)
    if rank(role) < rank(minimum):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"This action needs the {minimum} role on this space; you have {role}.",
        )
    return role


def readable_space_ids(user_id: uuid.UUID) -> Select[tuple[uuid.UUID]]:
    """Every space id the caller can read, as a subquery.

    For list and aggregate endpoints that would otherwise need a role
    check per row. UNION (not UNION ALL) because the owner of a space
    could also hold a stray membership row for it.
    """
    owned = select(Space.id).where(Space.owner_id == user_id)
    member = select(SpaceMembership.space_id).where(
        SpaceMembership.user_id == user_id
    )
    return select(union(owned, member).subquery().c[0])


def writable_space_ids(user_id: uuid.UUID) -> Select[tuple[uuid.UUID]]:
    """Every space id the caller can change, as a subquery.

    The counterpart to `readable_space_ids` for bulk operations. A viewer
    membership is deliberately excluded: a bulk action that silently
    included read-only spaces would be a quiet privilege escalation, even
    when each individual endpoint gets it right.
    """
    owned = select(Space.id).where(Space.owner_id == user_id)
    editor = select(SpaceMembership.space_id).where(
        SpaceMembership.user_id == user_id,
        SpaceMembership.role == SPACE_ROLE_EDITOR,
    )
    return select(union(owned, editor).subquery().c[0])
