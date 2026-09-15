"""Space membership management.

Who can see a space, and at what level. Only the space's owner may read
or change its membership — an editor can fill the space with content but
cannot widen access to it, which keeps "who else can see my library" a
decision the owner alone makes.

Instance admins are deliberately not privileged here either; see
auth/spaces.py for why.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import get_current_user
from ..auth.spaces import SPACE_ROLE_OWNER, require_space_role
from ..db import get_session
from ..models import Space, SpaceMembership, User

router = APIRouter(tags=["spaces"])


class MemberResponse(BaseModel):
    user_id: str
    email: str
    display_name: str
    role: str
    # True for the space's creator, who holds the owner role implicitly
    # and has no membership row to remove.
    is_owner: bool


class AddMemberRequest(BaseModel):
    # By email rather than user id: the owner knows who they want to add,
    # not that person's internal UUID, and there is no user-search
    # endpoint to look one up with (deliberately — it would expose the
    # whole user list to anyone with an account).
    email: str
    role: Literal["viewer", "editor"] = "viewer"


class UpdateMemberRequest(BaseModel):
    role: Literal["viewer", "editor"]


async def _owned_space(db: AsyncSession, user: User, slug: str) -> Space:
    space = (
        await db.execute(select(Space).where(Space.slug == slug))
    ).scalar_one_or_none()
    await require_space_role(
        db, space, user.id, SPACE_ROLE_OWNER, label="Space not found"
    )
    assert space is not None  # require_space_role raises when it isn't
    return space


async def _members_of(db: AsyncSession, space: Space) -> list[MemberResponse]:
    owner = await db.get(User, space.owner_id)
    rows = (
        await db.execute(
            select(SpaceMembership, User)
            .join(User, User.id == SpaceMembership.user_id)
            .where(SpaceMembership.space_id == space.id)
            .order_by(SpaceMembership.added_at)
        )
    ).all()

    out: list[MemberResponse] = []
    if owner is not None:
        out.append(
            MemberResponse(
                user_id=str(owner.id),
                email=owner.email,
                display_name=owner.display_name,
                role=SPACE_ROLE_OWNER,
                is_owner=True,
            )
        )
    out.extend(
        MemberResponse(
            user_id=str(member.id),
            email=member.email,
            display_name=member.display_name,
            role=membership.role,
            is_owner=False,
        )
        for membership, member in rows
    )
    return out


@router.get("/api/spaces/{slug}/members", response_model=list[MemberResponse])
async def list_members(
    slug: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[MemberResponse]:
    space = await _owned_space(db, user, slug)
    return await _members_of(db, space)


@router.post(
    "/api/spaces/{slug}/members",
    response_model=MemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_member(
    slug: str,
    payload: AddMemberRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> MemberResponse:
    space = await _owned_space(db, user, slug)

    member = (
        await db.execute(select(User).where(User.email == payload.email))
    ).scalar_one_or_none()
    if member is None:
        # Deliberately explicit rather than a silent no-op: the owner
        # mistyping an address should hear about it. It leaks only that
        # an address has no account, to someone who already has one.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No user with that email has signed in to this instance yet",
        )
    if member.id == space.owner_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That user owns this space and already has full access",
        )

    existing = await db.get(SpaceMembership, (space.id, member.id))
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "That user is already a member"
        )

    db.add(
        SpaceMembership(space_id=space.id, user_id=member.id, role=payload.role)
    )
    await db.commit()
    return MemberResponse(
        user_id=str(member.id),
        email=member.email,
        display_name=member.display_name,
        role=payload.role,
        is_owner=False,
    )


@router.patch(
    "/api/spaces/{slug}/members/{user_id}", response_model=MemberResponse
)
async def update_member(
    slug: str,
    user_id: uuid.UUID,
    payload: UpdateMemberRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> MemberResponse:
    space = await _owned_space(db, user, slug)
    membership = await db.get(SpaceMembership, (space.id, user_id))
    if membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")

    membership.role = payload.role
    await db.commit()

    member = await db.get(User, user_id)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    return MemberResponse(
        user_id=str(member.id),
        email=member.email,
        display_name=member.display_name,
        role=payload.role,
        is_owner=False,
    )


@router.delete(
    "/api/spaces/{slug}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_member(
    slug: str,
    user_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    space = await _owned_space(db, user, slug)
    membership = await db.get(SpaceMembership, (space.id, user_id))
    if membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    # Content the member created stays; only their access goes away.
    await db.delete(membership)
    await db.commit()
