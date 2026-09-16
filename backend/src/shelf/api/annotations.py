"""Annotation endpoints.

Annotations attach to PDF attachments, not items, because the rect
coordinates are in PDF user-space and only meaningful relative to a
specific file. The auth path resolves attachment → item → space →
owner the same way the storage endpoints do.

Highlights follow the same visibility rule as notes — see
`auth/visibility.py`. Marking up a shared standard is the obvious case:
read access is enough to highlight it, the markup starts private when the
PDF is inherited, and sharing it is the author's decision.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import get_current_user
from ..auth.spaces import (
    SPACE_ROLE_EDITOR,
    SPACE_ROLE_VIEWER,
    effective_role,
    rank,
    require_space_role,
)
from ..auth.visibility import default_visibility, visible_annotations
from ..db import get_session
from ..models import (
    VISIBILITY_SPACE,
    Annotation,
    AnnotationKind,
    Attachment,
    Item,
    Space,
    User,
)

router = APIRouter(tags=["annotations"])


class AnnotationCreate(BaseModel):
    kind: AnnotationKind
    page_number: int
    rects: list[list[float]]
    color: str = "#ffd400"
    text: str | None = None
    # Omitted means private on an inherited PDF, shared otherwise.
    visibility: Literal["private", "space"] | None = None


class AnnotationUpdate(BaseModel):
    color: str | None = None
    text: str | None = None
    rects: list[list[float]] | None = None


class AnnotationVisibilityUpdate(BaseModel):
    visibility: Literal["private", "space"]


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
    created_by: uuid.UUID | None = None
    visibility: str = VISIBILITY_SPACE
    is_mine: bool = False


def _to_response(ann: Annotation, user_id: uuid.UUID) -> AnnotationResponse:
    return AnnotationResponse(
        id=ann.id,
        attachment_id=ann.attachment_id,
        kind=ann.kind,
        page_number=ann.page_number,
        rects=ann.rects,
        color=ann.color,
        text=ann.text,
        created_at=ann.created_at,
        updated_at=ann.updated_at,
        created_by=ann.created_by,
        visibility=ann.visibility,
        is_mine=ann.created_by == user_id,
    )


async def _resolve_attachment(
    db: AsyncSession,
    user: User,
    attachment_id: uuid.UUID,
    minimum: str = SPACE_ROLE_VIEWER,
) -> tuple[Attachment, str, bool]:
    """`(attachment, role, inherited)` for a PDF the caller may reach."""
    att = await db.get(Attachment, attachment_id)
    if att is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    item = await db.get(Item, att.item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    space = await db.get(Space, item.space_id)
    role = await require_space_role(
        db, space, user.id, minimum, label="Attachment not found"
    )
    assert space is not None
    _, inherited = await effective_role(db, space, user.id)
    return att, role, inherited


async def _resolve_annotation(
    db: AsyncSession, user: User, annotation_id: uuid.UUID
) -> tuple[Annotation, str]:
    """An annotation the caller may read, with their role in its space.

    Someone else's private highlight 404s rather than 403-ing — it isn't
    theirs to know exists.
    """
    ann = await db.get(Annotation, annotation_id)
    if ann is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Annotation not found")
    # Auth via the attachment so deleted parent items + inaccessible
    # spaces are rejected uniformly.
    _, role, _inherited = await _resolve_attachment(db, user, ann.attachment_id)
    if ann.visibility != VISIBILITY_SPACE and ann.created_by != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Annotation not found")
    return ann, role


def _require_can_edit(ann: Annotation, user: User, role: str) -> None:
    """The author may change their own markup; otherwise it takes editor
    on the space, which covers the author-less rows that predate this."""
    if ann.created_by is not None and ann.created_by == user.id:
        return
    if rank(role) >= rank(SPACE_ROLE_EDITOR):
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN, "That highlight belongs to someone else"
    )


@router.get(
    "/api/attachments/{attachment_id}/annotations",
    response_model=list[AnnotationResponse],
)
async def list_annotations(
    attachment_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[AnnotationResponse]:
    """Markup shared with the PDF's space, plus the caller's own."""
    await _resolve_attachment(db, user, attachment_id)
    result = await db.execute(
        select(Annotation)
        .where(
            Annotation.attachment_id == attachment_id,
            visible_annotations(user.id),
        )
        .order_by(Annotation.page_number, Annotation.created_at)
    )
    return [_to_response(a, user.id) for a in result.scalars().all()]


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
) -> AnnotationResponse:
    """Highlight any PDF the caller can read.

    Viewer is enough, same as notes: an inherited standard is read-only
    as an item, but marking up your own copy of the view is not a write
    to it.
    """
    att, _role, inherited = await _resolve_attachment(db, user, attachment_id)
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
        visibility=payload.visibility or default_visibility(inherited=inherited),
    )
    db.add(ann)
    await db.commit()
    await db.refresh(ann)
    return _to_response(ann, user.id)


@router.patch(
    "/api/annotations/{annotation_id}", response_model=AnnotationResponse
)
async def update_annotation(
    annotation_id: uuid.UUID,
    payload: AnnotationUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> AnnotationResponse:
    ann, role = await _resolve_annotation(db, user, annotation_id)
    _require_can_edit(ann, user, role)
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
    return _to_response(ann, user.id)


@router.patch(
    "/api/annotations/{annotation_id}/visibility",
    response_model=AnnotationResponse,
)
async def set_annotation_visibility(
    annotation_id: uuid.UUID,
    payload: AnnotationVisibilityUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> AnnotationResponse:
    """Share this highlight with the PDF's space, or take it back.

    The author's call alone, exactly as for notes.
    """
    ann, _role = await _resolve_annotation(db, user, annotation_id)
    if ann.created_by is None or ann.created_by != user.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only the author can change who sees a highlight",
        )
    ann.visibility = payload.visibility
    ann.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(ann)
    return _to_response(ann, user.id)


@router.delete(
    "/api/annotations/{annotation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_annotation(
    annotation_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    ann, role = await _resolve_annotation(db, user, annotation_id)
    _require_can_edit(ann, user, role)
    await db.delete(ann)
    await db.commit()
