"""Space and collection profiles: a description, and default library columns.

A Standards space is read by Designation and Edition; a paper library by
Creator. The library table used to have one column set per browser, so
whichever library someone opened last decided what every other one
looked like. A profile lets the people who curate a space say what its
table shows, once, for everyone.

**Inheritance** is resolved by the SPA, which already holds every
collection and space it needs: a collection without its own `columns`
takes its nearest ancestor's, then its space's, then the built-in
default. A collection a space inherits falls back to the space it lives
in, not the one it is browsed from — Standards' folders look like
Standards wherever they appear. Descriptions do not inherit; they say
what one place is for.

A reader's own column choices sit on top, in their browser, keyed by the
profile they were made against — see the column picker.

**Who may set one:** editors, not just the owner. A profile changes how a
space is presented, not who can see it, which is the same footing as
filing items into it.
"""

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import get_current_user
from ..auth.spaces import SPACE_ROLE_EDITOR, require_space_role
from ..db import get_session
from ..models import Space, User
from ..services.audit import AuditAction, record

router = APIRouter(tags=["profiles"])

# The table's own columns, plus any item metadata field as `field:<name>`
# (`field:designation`). Field names are the free-form keys of an item's
# `data`, so they're checked for shape rather than against a list: a
# field the SPA doesn't know yet is still a column someone may want.
BUILTIN_COLUMNS = frozenset(
    {"title", "creator", "type", "space", "collection", "tags", "updated"}
)
_FIELD_COLUMN = re.compile(r"^field:[A-Za-z][A-Za-z0-9_]{0,63}$")
MAX_COLUMNS = 30


def clean_columns(columns: list[str] | None) -> list[str] | None:
    """Validate a column list, dropping repeats and keeping order.

    None and an empty list both mean "not set, inherit": a profile that
    shows no columns at all is never what anyone meant.
    """
    if not columns:
        return None
    seen: list[str] = []
    for raw in columns:
        col = raw.strip()
        if col not in BUILTIN_COLUMNS and not _FIELD_COLUMN.match(col):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Unknown column {raw!r}: use one of "
                f"{', '.join(sorted(BUILTIN_COLUMNS))}, or field:<name>",
            )
        if col not in seen:
            seen.append(col)
    if len(seen) > MAX_COLUMNS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"At most {MAX_COLUMNS} columns"
        )
    return seen


class SpaceProfileUpdate(BaseModel):
    """Partial: an omitted field is left alone, an explicit null (or an
    empty string / list) clears it."""

    description: str | None = Field(default=None, max_length=4000)
    columns: list[str] | None = None


class SpaceProfile(BaseModel):
    description: str | None
    columns: list[str] | None


@router.patch("/api/spaces/{slug}/profile", response_model=SpaceProfile)
async def update_space_profile(
    slug: str,
    payload: SpaceProfileUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> SpaceProfile:
    space = (
        await db.execute(select(Space).where(Space.slug == slug))
    ).scalar_one_or_none()
    await require_space_role(
        db, space, user.id, SPACE_ROLE_EDITOR, label="Space not found"
    )
    assert space is not None  # require_space_role raises when it isn't

    provided = payload.model_fields_set
    changed: list[str] = []
    if "description" in provided:
        description = (payload.description or "").strip() or None
        if description != space.description:
            changed.append("description")
        space.description = description
    if "columns" in provided:
        columns = clean_columns(payload.columns)
        if columns != space.columns:
            changed.append("columns")
        space.columns = columns

    if changed:
        record(
            db,
            user,
            AuditAction.space_update,
            space_id=space.id,
            target_type="space",
            target_id=space.id,
            label=space.name,
            details={"fields": changed},
        )
    await db.commit()
    return SpaceProfile(description=space.description, columns=space.columns)
