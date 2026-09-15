import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import get_current_user, get_session_claims
from ..auth.session import SessionClaims
from ..db import get_session
from ..models import Attachment, Identity, Item, Space, User
from ..services import storage

router = APIRouter(tags=["auth"])


class LinkedAccount(BaseModel):
    id: str
    email: str
    display_name: str
    # OIDC providers this account has an identity with. Lets the SPA send
    # "switch user" straight to the right provider's account picker
    # instead of asking which one. Empty for dev-login users, who have no
    # identity row at all.
    idps: list[str]


class MeResponse(BaseModel):
    id: str
    email: str
    display_name: str
    role: str
    is_admin: bool
    # Every identity linked to this browser session, the active one
    # included. The SPA's account switcher renders straight from this, so
    # it doesn't need a second request to draw the menu.
    accounts: list[LinkedAccount]


class SpaceResponse(BaseModel):
    id: str
    slug: str
    name: str
    is_personal: bool


@router.get("/api/me", response_model=MeResponse)
async def me(
    user: Annotated[User, Depends(get_current_user)],
    claims: Annotated[SessionClaims, Depends(get_session_claims)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> MeResponse:
    # Resolve the linked ids against the database rather than trusting the
    # cookie's copy of them: a user deleted since the session was minted
    # should drop out of the menu instead of 404-ing on switch.
    rows = (
        await db.execute(select(User).where(User.id.in_(claims.account_ids)))
    ).scalars().all()
    by_id = {u.id: u for u in rows}

    # One round trip for every linked account's providers, rather than one
    # per account.
    idps_by_user: dict[uuid.UUID, list[str]] = {}
    for user_id, idp in (
        await db.execute(
            select(Identity.user_id, Identity.idp)
            .where(Identity.user_id.in_(claims.account_ids))
            .order_by(Identity.idp)
        )
    ).all():
        idps_by_user.setdefault(user_id, []).append(idp)

    accounts = [
        LinkedAccount(
            id=str(linked.id),
            email=linked.email,
            display_name=linked.display_name,
            idps=idps_by_user.get(linked.id, []),
        )
        for account_id in claims.account_ids
        if (linked := by_id.get(account_id)) is not None
    ]

    return MeResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        is_admin=user.is_admin,
        accounts=accounts,
    )


@router.get("/api/me/spaces", response_model=list[SpaceResponse])
async def my_spaces(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[SpaceResponse]:
    # Phase 1: only spaces the user owns. Membership-based access (shared
    # spaces) lands in Phase 2 with the SpaceMembership join.
    result = await db.execute(
        select(Space).where(Space.owner_id == user.id).order_by(Space.created_at)
    )
    return [
        SpaceResponse(
            id=str(s.id),
            slug=s.slug,
            name=s.name,
            is_personal=s.slug.startswith("u-"),
        )
        for s in result.scalars().all()
    ]


class OrphanCleanupResult(BaseModel):
    deleted: int


@router.post(
    "/api/me/attachments/cleanup-orphans",
    response_model=OrphanCleanupResult,
)
async def cleanup_orphan_attachments(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    older_than_minutes: Annotated[int, Query(ge=1, le=24 * 60)] = 60,
) -> OrphanCleanupResult:
    """Delete attachment rows still pending (uploaded_at NULL) on items
    in spaces the caller owns.

    Pending rows happen when a client crashes between /register and
    PUT, or between PUT and /complete — the row sticks around showing
    "(pending)" forever. This is the user-driven sweep; a future
    cron job can run the same logic across all users.

    `older_than_minutes` (default 60) protects in-flight uploads from
    getting reaped mid-PUT.
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=older_than_minutes)
    rows = (
        await db.execute(
            select(Attachment)
            .join(Item, Item.id == Attachment.item_id)
            .join(Space, Space.id == Item.space_id)
            .where(
                Space.owner_id == user.id,
                Attachment.uploaded_at.is_(None),
                Attachment.created_at < cutoff,
            )
        )
    ).scalars().all()

    deleted = 0
    for att in rows:
        # Best-effort object delete: most pending rows will have no
        # body in the bucket (that's why they're pending), so 404
        # from the storage backend is expected.
        try:
            await storage.delete_object(att.storage_key)
        except Exception:
            pass
        await db.delete(att)
        deleted += 1
    await db.commit()
    return OrphanCleanupResult(deleted=deleted)
