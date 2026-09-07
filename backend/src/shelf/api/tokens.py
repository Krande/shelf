"""Cookie-auth endpoints for managing API tokens.

These are owner-facing: a logged-in user lists / creates / revokes
their own tokens. Token-bearer endpoints live under /api/v1/* and are
defined alongside the action they authorise.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import get_current_user
from ..auth.tokens import mint
from ..db import get_session
from ..models import ApiToken, Collection, User

router = APIRouter(tags=["tokens"])

Scope = Literal["upload", "search", "download"]
_VALID_SCOPES: set[str] = {"upload", "search", "download"}
# Scopes the public mint endpoint refuses. ``worker`` grants
# cross-user access to attachment_processing rows so it can't be a
# self-service scope; operators provision worker tokens via the
# admin CLI (`python -m shelf.admin.mint_worker_token`) instead.
_OPERATOR_ONLY_SCOPES: set[str] = {"worker"}


class ApiTokenCreate(BaseModel):
    name: str
    scopes: list[Scope]
    allowed_collection_ids: list[uuid.UUID] | None = None
    include_descendants: bool = False
    expires_at: datetime | None = None


class ApiTokenResponse(BaseModel):
    """Redacted view used by GET /api/me/tokens."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    prefix: str
    scopes: list[str]
    allowed_collection_ids: list[str] | None
    include_descendants: bool
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class ApiTokenCreated(ApiTokenResponse):
    """Same as the redacted view, plus the one-time plaintext."""

    plaintext: str


@router.get("/api/me/tokens", response_model=list[ApiTokenResponse])
async def list_tokens(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[ApiToken]:
    result = await db.execute(
        select(ApiToken)
        .where(ApiToken.user_id == user.id)
        .order_by(ApiToken.created_at.desc())
    )
    return list(result.scalars().all())


@router.post(
    "/api/me/tokens",
    response_model=ApiTokenCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_token(
    payload: ApiTokenCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ApiTokenCreated:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name required")

    if not payload.scopes:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "At least one scope is required (upload / search / download)",
        )
    bad = [s for s in payload.scopes if s not in _VALID_SCOPES]
    if bad:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown scope(s): {', '.join(bad)}",
        )
    operator_only = [s for s in payload.scopes if s in _OPERATOR_ONLY_SCOPES]
    if operator_only:
        # Defence in depth: even though operator-only scopes aren't in
        # _VALID_SCOPES (so the previous check already rejects them),
        # the explicit message saves a round trip the day someone
        # widens _VALID_SCOPES and forgets to update this gate.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Scope(s) reserved for operator provisioning: "
            f"{', '.join(operator_only)}",
        )

    if payload.allowed_collection_ids is not None:
        # Empty list would mean "no collections" — silently treat that
        # as a configuration error rather than minting a useless token.
        if not payload.allowed_collection_ids:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "allowed_collection_ids is empty — drop the field for unrestricted access",
            )
        # Every collection must belong to the calling user.
        for cid in payload.allowed_collection_ids:
            coll = await db.get(Collection, cid)
            if coll is None:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Unknown collection {cid}",
                )
            # Confirm ownership via the space.
            from ..models import Space  # local import to avoid cycle

            space = await db.get(Space, coll.space_id)
            if space is None or space.owner_id != user.id:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Collection {cid} is not yours",
                )

    minted = mint()
    token = ApiToken(
        user_id=user.id,
        name=name,
        token_hash=minted.token_hash,
        prefix=minted.prefix,
        scopes=list(payload.scopes),
        allowed_collection_ids=(
            [str(c) for c in payload.allowed_collection_ids]
            if payload.allowed_collection_ids
            else None
        ),
        # include_descendants is meaningless without an allow-list; the
        # gate code branches on `allowed_collection_ids is not None`
        # first, so flipping this flag with no list is a no-op anyway.
        # Persist what the user asked for to avoid surprising round-trips.
        include_descendants=payload.include_descendants,
        expires_at=payload.expires_at,
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)

    return ApiTokenCreated(
        id=token.id,
        name=token.name,
        prefix=token.prefix,
        scopes=token.scopes,
        allowed_collection_ids=token.allowed_collection_ids,
        include_descendants=token.include_descendants,
        expires_at=token.expires_at,
        last_used_at=token.last_used_at,
        created_at=token.created_at,
        plaintext=minted.plaintext,
    )


@router.delete(
    "/api/me/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_token(
    token_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    token = await db.get(ApiToken, token_id)
    if token is None or token.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Token not found")
    await db.delete(token)
    await db.commit()
