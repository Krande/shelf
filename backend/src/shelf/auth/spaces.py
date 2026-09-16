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

**Inheritance** adds a fourth way to reach a space. When space A
subscribes to space B (`SpaceInheritance`), everyone who can read A can
read B's items — always as a viewer, whatever their role in A. That is
the whole point of a shared Standards space: one copy of each standard,
read by every project and every person who subscribes.

Three rules keep that from becoming a hole:

  * It grants read and nothing else. An editor on the project space is
    still a viewer on the standards it inherits.
  * It does not chain. A inherits B, B inherits C — A does not see C.
  * It is opt-in on the side being read: `Space.subscribable`.

`space_role` is the direct answer and stays that way; `effective_role`
is the one that also considers inheritance. Anything on a read path
wants the latter, and everything that writes wants the former — which is
most of why they're two functions.
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import Select, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Space, SpaceInheritance, SpaceMembership

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
    """The caller's direct role in `space`, or None if they have none.

    Direct means owned or a membership row. Ignores inheritance — use
    `effective_role` where a subscription should count.
    """
    if space.owner_id == user_id:
        return SPACE_ROLE_OWNER
    membership = await db.get(SpaceMembership, (space.id, user_id))
    return membership.role if membership is not None else None


async def effective_role(
    db: AsyncSession, space: Space, user_id: uuid.UUID
) -> tuple[str | None, bool]:
    """`(role, inherited)` — the caller's role in `space` counting
    subscriptions.

    A direct role always wins, and reports `inherited=False` even when
    the caller also reaches the space through a subscription: someone who
    is an editor on the Standards space stays an editor there, whatever
    their project space subscribes to.

    Reaching it only through a subscription yields viewer, whatever role
    the caller holds in the space that subscribes. The second element is
    what lets a caller phrase "read-only because it's inherited"
    differently from "read-only because you're a viewer" — the fixes are
    not the same.
    """
    direct = await space_role(db, space, user_id)
    if direct is not None:
        return direct, False

    via = await db.scalar(
        select(SpaceInheritance.child_space_id)
        .where(
            SpaceInheritance.parent_space_id == space.id,
            SpaceInheritance.child_space_id.in_(readable_space_ids(user_id)),
        )
        .limit(1)
    )
    return (SPACE_ROLE_VIEWER, True) if via is not None else (None, False)


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
    role, inherited = await effective_role(db, space, user_id)
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, label)
    if rank(role) < rank(minimum):
        if inherited:
            # Naming the space is the whole message: the caller is
            # looking at this item in their own project or personal
            # space, where they may well be an editor, and "you have
            # viewer" alone reads like a bug.
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"{space.name!r} is inherited here and is read-only. Change "
                f"it in the space that owns it.",
            )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"This action needs the {minimum} role on this space; you have {role}.",
        )
    return role


# ── Inheritance ──────────────────────────────────────────────────────────────


async def parent_space_ids(
    db: AsyncSession, space_id: uuid.UUID
) -> tuple[uuid.UUID, ...]:
    """The spaces `space_id` subscribes to.

    Resolved to a tuple rather than left as a subquery: a space
    subscribes to a handful of others at most, and having the ids in hand
    keeps the item queries that use them readable.
    """
    rows = (
        await db.execute(
            select(SpaceInheritance.parent_space_id).where(
                SpaceInheritance.child_space_id == space_id
            )
        )
    ).scalars().all()
    return tuple(rows)


async def item_source_space_ids(
    db: AsyncSession, space_id: uuid.UUID
) -> tuple[uuid.UUID, ...]:
    """Every space whose items show up in `space_id`'s library — its own
    first, then the ones it inherits.

    Own-space-first matters to callers that break ties by source, and is
    free here.
    """
    return (space_id, *await parent_space_ids(db, space_id))


def readable_item_space_ids(user_id: uuid.UUID) -> Select[tuple[uuid.UUID]]:
    """Every space whose *items* the caller can read, as a subquery.

    Wider than `readable_space_ids`: it also contains the spaces that the
    caller's spaces subscribe to. This is the right bound for anything
    that reaches an item without going through a space slug — resolving
    an item by id, a cross-space search, an export.

    One hop only, matching `effective_role`. The nested
    `readable_space_ids` is the subscriber set; its parents are what get
    added, and *their* parents do not.
    """
    direct = select(Space.id).where(Space.owner_id == user_id)
    member = select(SpaceMembership.space_id).where(
        SpaceMembership.user_id == user_id
    )
    inherited = select(SpaceInheritance.parent_space_id).where(
        SpaceInheritance.child_space_id.in_(readable_space_ids(user_id))
    )
    return select(union(direct, member, inherited).subquery().c[0])


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
