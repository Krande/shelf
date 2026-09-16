"""Instance administration.

Listing accounts, changing their instance role, and pre-provisioning one
from an email address before its owner has ever signed in.

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
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.provisioning import create_user_with_personal_space
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


class CreateUserRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    # Defaults to the local part of the address, same as every other path
    # that mints a user. The person can't correct it themselves yet, but
    # their first OIDC login doesn't overwrite it either — so an admin
    # typing a real name here is worth the field.
    display_name: str | None = Field(default=None, max_length=200)
    # Spelled out rather than `= ROLE_USER` so the default types as the
    # Literal; same reasoning as UpdateRoleRequest below.
    role: Literal["admin", "user"] = "user"


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


def _clean_email(raw: str) -> str:
    """Normalise and sanity-check an address typed by an admin.

    Not a full RFC 5322 parse — the point is to catch a typo before it
    becomes a row nobody can ever sign in as, not to adjudicate exotic
    addresses. Anything that survives this still has to match the
    provider's `email` claim exactly (modulo case) for the link-up on
    first login to find it.
    """
    email = raw.strip()
    local, sep, domain = email.rpartition("@")
    if not sep or not local or not domain or "." not in domain:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "That doesn't look like an email address",
        )
    if any(c.isspace() for c in email):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "An email address cannot contain spaces"
        )
    return email


@router.post(
    "/api/admin/users",
    response_model=AdminUserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    payload: CreateUserRequest,
    _admin: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> AdminUserResponse:
    """Pre-provision an account from an email address.

    Users normally appear here on their own: the OIDC callback creates
    the row the first time someone signs in (`auth/oidc.py`). Nothing is
    synced from the identity provider ahead of that, so until a new
    colleague has logged in once they don't exist to the member picker
    and can't be added to a shared space. This is the way to get them
    there first.

    The row is created with no `Identity` attached. When they do sign in,
    `upsert_user_from_claims` finds them by email — `users.email` is
    CITEXT, so case doesn't matter — and links the provider identity onto
    this row rather than making a second account. That match is on the
    address alone, so an address that doesn't match what the provider
    sends leaves a stray empty account behind; nothing breaks, but it's
    worth typing carefully.
    """
    email = _clean_email(payload.email)

    existing = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{existing.email} already has an account here",
        )

    display_name = (payload.display_name or "").strip() or None
    user = await create_user_with_personal_space(
        db, email=email, display_name=display_name, role=payload.role
    )
    await db.commit()
    await db.refresh(user)
    return _to_response(user)


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
