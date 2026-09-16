"""Subscribing one space to another.

The shape the feature is for: a shared "Standards" space holds one copy
of each engineering standard, and every project space — and every person
who wants them in their own library — subscribes to it. Nothing is
copied. The standards live in one place, and a subscription is a read
grant pointing at them.

Two sides, two owners, two routes:

  * The **parent** owner sets `subscribable` on their space. That is the
    consent: it says "any space owner may point their space at mine, and
    everyone who can read their space will be able to read my items".
    Without it, subscribing is refused.

  * The **child** owner adds and removes the subscription. They are the
    one who decides what their project or personal library contains.

The parent owner keeps a listing of who subscribes and can drop any of
them, so `subscribable` is a standing offer rather than a door that can't
be closed. Turning the flag off stops new subscriptions and leaves
existing ones alone — deliberately, because access silently evaporating
across every project is a worse surprise than a stale subscription.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import get_current_user
from ..auth.spaces import (
    SPACE_ROLE_OWNER,
    SPACE_ROLE_VIEWER,
    readable_space_ids,
    require_space_role,
)
from ..db import get_session
from ..models import Space, SpaceInheritance, User

router = APIRouter(tags=["spaces"])


class SubscriptionResponse(BaseModel):
    space_id: str
    slug: str
    name: str
    subscribable: bool


class SubscribeRequest(BaseModel):
    # By slug rather than id: it's what the URL and the API tokens use,
    # and it's what a person can actually read off the screen.
    parent_slug: str


async def _space_by_slug(db: AsyncSession, slug: str) -> Space | None:
    return (
        await db.execute(select(Space).where(Space.slug == slug))
    ).scalar_one_or_none()


async def _owned(db: AsyncSession, user: User, slug: str) -> Space:
    space = await _space_by_slug(db, slug)
    await require_space_role(
        db, space, user.id, SPACE_ROLE_OWNER, label="Space not found"
    )
    assert space is not None  # require_space_role raises when it isn't
    return space


@router.get(
    "/api/spaces/{slug}/inherits", response_model=list[SubscriptionResponse]
)
async def list_inherited(
    slug: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[SubscriptionResponse]:
    """What this space subscribes to.

    Readable by anyone who can read the space, not just its owner —
    knowing where an inherited standard comes from is part of reading the
    library, and the items themselves are already visible.
    """
    space = await _space_by_slug(db, slug)
    await require_space_role(
        db, space, user.id, SPACE_ROLE_VIEWER, label="Space not found"
    )
    assert space is not None

    rows = (
        await db.execute(
            select(Space)
            .join(
                SpaceInheritance, SpaceInheritance.parent_space_id == Space.id
            )
            .where(SpaceInheritance.child_space_id == space.id)
            .order_by(SpaceInheritance.created_at)
        )
    ).scalars().all()
    return [_to_response(s) for s in rows]


@router.post(
    "/api/spaces/{slug}/inherits",
    response_model=SubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def subscribe(
    slug: str,
    payload: SubscribeRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> SubscriptionResponse:
    """Point `slug` at another space, gaining its items read-only."""
    child = await _owned(db, user, slug)
    parent = await _space_by_slug(db, payload.parent_slug)
    if parent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such space to inherit")

    if parent.id == child.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "A space cannot inherit itself"
        )
    if not parent.subscribable:
        # 403 rather than 404: the caller found the space by slug, so
        # hiding it now would only be confusing. What they can't do is
        # subscribe to it.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"{parent.name!r} is not open for other spaces to inherit. Its "
            f"owner has to turn that on first.",
        )

    existing = await db.get(SpaceInheritance, (child.id, parent.id))
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"{child.name!r} already inherits {parent.name!r}"
        )

    # A cycle can't widen access — inheritance doesn't chain, so B
    # inheriting A back grants A's readers nothing extra — but it does
    # produce two spaces each claiming the other's items, which reads as
    # a bug from either side. Refuse the second leg.
    reverse = await db.get(SpaceInheritance, (parent.id, child.id))
    if reverse is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{parent.name!r} already inherits {child.name!r}; two spaces "
            f"cannot inherit each other.",
        )

    db.add(
        SpaceInheritance(
            child_space_id=child.id, parent_space_id=parent.id, added_by=user.id
        )
    )
    await db.commit()
    return _to_response(parent)


@router.delete(
    "/api/spaces/{slug}/inherits/{parent_slug}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unsubscribe(
    slug: str,
    parent_slug: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Drop a subscription. The parent's items stay where they are."""
    child = await _owned(db, user, slug)
    parent = await _space_by_slug(db, parent_slug)
    if parent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subscription not found")

    link = await db.get(SpaceInheritance, (child.id, parent.id))
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subscription not found")

    # Pins in this space that name an item the space can no longer see
    # would become dangling. They're left alone on purpose: re-subscribing
    # restores them intact, and a project's record of which revision it
    # chose is worth more than tidiness. The pin listing filters to what
    # is actually visible.
    await db.delete(link)
    await db.commit()


@router.get(
    "/api/spaces/{slug}/subscribers", response_model=list[SubscriptionResponse]
)
async def list_subscribers(
    slug: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[SubscriptionResponse]:
    """Which spaces subscribe to this one. Owner only — it's the same
    question as "who can see my items", which is the owner's to ask."""
    space = await _owned(db, user, slug)
    rows = (
        await db.execute(
            select(Space)
            .join(SpaceInheritance, SpaceInheritance.child_space_id == Space.id)
            .where(SpaceInheritance.parent_space_id == space.id)
            .order_by(SpaceInheritance.created_at)
        )
    ).scalars().all()
    return [_to_response(s) for s in rows]


@router.delete(
    "/api/spaces/{slug}/subscribers/{child_slug}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_subscriber(
    slug: str,
    child_slug: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Revoke one space's subscription to yours."""
    parent = await _owned(db, user, slug)
    child = await _space_by_slug(db, child_slug)
    if child is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subscription not found")

    link = await db.get(SpaceInheritance, (child.id, parent.id))
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subscription not found")
    await db.delete(link)
    await db.commit()


@router.get("/api/spaces/subscribable", response_model=list[SubscriptionResponse])
async def list_subscribable(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[SubscriptionResponse]:
    """Spaces on this instance that are open to be inherited.

    Every authenticated caller sees the same list, including spaces they
    hold no role in — that's what makes a Standards space discoverable
    without the owner adding everyone as a member first. It exposes the
    name and slug of spaces whose owners have explicitly published them
    for exactly this, and nothing about their contents.

    Personal spaces are excluded regardless of the flag. Inheriting
    someone's personal shelf isn't the gesture this is for, and the slug
    prefix makes them cheap to leave out.
    """
    rows = (
        await db.execute(
            select(Space)
            .where(Space.subscribable.is_(True), ~Space.slug.startswith("u-"))
            .order_by(Space.name)
        )
    ).scalars().all()
    return [_to_response(s) for s in rows]


class SpaceSettingsRequest(BaseModel):
    subscribable: bool


@router.patch("/api/spaces/{slug}/settings", response_model=SubscriptionResponse)
async def update_space_settings(
    slug: str,
    payload: SpaceSettingsRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> SubscriptionResponse:
    """Open this space to subscriptions, or close it to new ones."""
    space = await _owned(db, user, slug)
    if space.slug.startswith("u-") and payload.subscribable:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "A personal space cannot be inherited. Create a shared space for "
            "anything meant to be read by other spaces.",
        )
    space.subscribable = payload.subscribable
    await db.commit()
    await db.refresh(space)
    return _to_response(space)


def _to_response(space: Space) -> SubscriptionResponse:
    return SubscriptionResponse(
        space_id=str(space.id),
        slug=space.slug,
        name=space.name,
        subscribable=space.subscribable,
    )


# Silences the unused-import check while keeping the symbol available to
# readers tracing how the subscriber set is bounded.
__all__ = ["readable_space_ids", "router"]
