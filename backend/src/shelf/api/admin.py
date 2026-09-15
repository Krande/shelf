"""Instance administration.

The first routes in shelf that aren't scoped to spaces the caller owns.
Everything here is gated on `require_admin`, which is cookie-session only
— no bearer scope grants admin, so a leaked API token can't reach it.

Paths live under /api/ because the SPA catch-all in `main.py` only
declines to serve index.html for a fixed prefix tuple; a top-level
/admin/* would be swallowed by the frontend router instead of 404-ing.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.roles import require_admin
from ..db import get_session
from ..models import ROLE_ADMIN, User

router = APIRouter(tags=["admin"])


class AdminUserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    role: str
    created_at: str


class UpdateRoleRequest(BaseModel):
    # Literal rather than a plain str validated in the handler: FastAPI
    # rejects anything else with a 422 that names the valid values, and
    # the constraint shows up in the OpenAPI schema. Kept in step with
    # models.ROLES and the ck_users_role CHECK.
    role: Literal["admin", "user"]


def _to_response(user: User) -> AdminUserResponse:
    return AdminUserResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        created_at=user.created_at.isoformat(),
    )


@router.get("/api/admin/users", response_model=list[AdminUserResponse])
async def list_users(
    _admin: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AdminUserResponse]:
    rows = (
        await db.execute(
            select(User).order_by(User.created_at).limit(limit).offset(offset)
        )
    ).scalars().all()
    return [_to_response(u) for u in rows]


@router.patch("/api/admin/users/{user_id}", response_model=AdminUserResponse)
async def update_user_role(
    user_id: uuid.UUID,
    payload: UpdateRoleRequest,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> AdminUserResponse:
    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    if user.role == payload.role:
        return _to_response(user)

    # Demoting the last admin would leave the instance with no way back in
    # short of SHELF_ADMIN_EMAILS + a restart, or `pixi run grant-admin`.
    # Cheap to check, and the mistake is easy to make with two admins on
    # screen and one of them yourself.
    if user.role == ROLE_ADMIN and payload.role != ROLE_ADMIN:
        admin_count = (
            await db.execute(
                select(func.count()).select_from(User).where(User.role == ROLE_ADMIN)
            )
        ).scalar_one()
        if admin_count <= 1:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Cannot demote the last remaining admin",
            )

    user.role = payload.role
    await db.commit()
    await db.refresh(user)
    return _to_response(user)
