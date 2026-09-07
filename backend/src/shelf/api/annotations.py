"""Annotation endpoints.

Annotations attach to PDF attachments, not items, because the rect
coordinates are in PDF user-space and only meaningful relative to a
specific file. The auth path resolves attachment → item → space →
owner the same way the storage endpoints do.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import get_current_user
from ..db import get_session
from ..models import Annotation, AnnotationKind, Attachment, Item, Space, User

router = APIRouter(tags=["annotations"])


class AnnotationCreate(BaseModel):
    kind: AnnotationKind
    page_number: int
    rects: list[list[float]]
    color: str = "#ffd400"
    text: str | None = None


class AnnotationUpdate(BaseModel):
    color: str | None = None
    text: str | None = None
    rects: list[list[float]] | None = None


class AnnotationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    attachment_id: uuid.UUID
    kind: str
    page_number: int
    rects: list[list[float]]
    color: str
    text: str | None
    created_at: datetime
    updated_at: datetime


async def _resolve_attachment(
    db: AsyncSession, user: User, attachment_id: uuid.UUID
) -> Attachment:
    att = await db.get(Attachment, attachment_id)
    if att is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    item = await db.get(Item, att.item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    space = await db.get(Space, item.space_id)
    if space is None or space.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    return att


async def _resolve_annotation(
    db: AsyncSession, user: User, annotation_id: uuid.UUID
) -> Annotation:
    ann = await db.get(Annotation, annotation_id)
    if ann is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Annotation not found")
    # Auth via the attachment so deleted parent items + cross-user
    # access are rejected uniformly.
    await _resolve_attachment(db, user, ann.attachment_id)
    return ann


@router.get(
    "/api/attachments/{attachment_id}/annotations",
    response_model=list[AnnotationResponse],
)
async def list_annotations(
    attachment_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[Annotation]:
    await _resolve_attachment(db, user, attachment_id)
    result = await db.execute(
        select(Annotation)
        .where(Annotation.attachment_id == attachment_id)
        .order_by(Annotation.page_number, Annotation.created_at)
    )
    return list(result.scalars().all())


@router.post(
    "/api/attachments/{attachment_id}/annotations",
    response_model=AnnotationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_annotation(
    attachment_id: uuid.UUID,
    payload: AnnotationCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Annotation:
    att = await _resolve_attachment(db, user, attachment_id)
    if payload.page_number < 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "page_number must be >= 1"
        )
    if not payload.rects:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "rects must be non-empty"
        )
    for r in payload.rects:
        if len(r) != 4:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "each rect must be [x, y, w, h]",
            )

    ann = Annotation(
        attachment_id=att.id,
        kind=payload.kind.value,
        page_number=payload.page_number,
        rects=payload.rects,
        color=payload.color,
        text=payload.text,
        created_by=user.id,
    )
    db.add(ann)
    await db.commit()
    await db.refresh(ann)
    return ann


@router.patch(
    "/api/annotations/{annotation_id}", response_model=AnnotationResponse
)
async def update_annotation(
    annotation_id: uuid.UUID,
    payload: AnnotationUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Annotation:
    ann = await _resolve_annotation(db, user, annotation_id)
    if payload.color is not None:
        ann.color = payload.color
    if payload.text is not None:
        ann.text = payload.text
    if payload.rects is not None:
        if not payload.rects:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "rects must be non-empty"
            )
        for r in payload.rects:
            if len(r) != 4:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "each rect must be [x, y, w, h]",
                )
        ann.rects = payload.rects
    ann.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(ann)
    return ann


@router.delete(
    "/api/annotations/{annotation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_annotation(
    annotation_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    ann = await _resolve_annotation(db, user, annotation_id)
    await db.delete(ann)
    await db.commit()
