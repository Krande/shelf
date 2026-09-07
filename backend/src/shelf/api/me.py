from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import get_current_user
from ..db import get_session
from ..models import Attachment, Item, Space, User
from ..services import storage

router = APIRouter(tags=["auth"])


class MeResponse(BaseModel):
    id: str
    email: str
    display_name: str


class SpaceResponse(BaseModel):
    id: str
    slug: str
    name: str
    is_personal: bool


@router.get("/api/me", response_model=MeResponse)
async def me(user: Annotated[User, Depends(get_current_user)]) -> MeResponse:
    return MeResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
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
