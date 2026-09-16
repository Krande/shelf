"""Engineering standards: revisions, and which one a space uses.

Two questions this answers that the plain item model can't:

  * "What other editions of this are there, and is the one I'm reading
    the current one?" — `GET /api/items/{id}/revisions`.
  * "Which edition does this project build to?" — the pins under
    `/api/spaces/{slug}/pins`.

**Latest is relative to the caller.** A revision is "latest" among the
ones that caller can actually see, not among every row in the table.
Anything else would tell someone their copy is out of date and then 404
the newer one, which is worse than not mentioning it. `is_latest_known`
says whether the newest visible revision is also the newest on the
instance, so a project that has fallen behind the Standards space can be
told so without being shown what it can't open.

Ordering is by `issued_on`, nulls last. Labels don't sort — "Rev. 5",
"2020", "Ed. 3 Amd. 1" are all in use — so an undated revision is never
latest and never displaces a dated one.
"""

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import get_current_user
from ..auth.spaces import (
    SPACE_ROLE_EDITOR,
    SPACE_ROLE_OWNER,
    SPACE_ROLE_VIEWER,
    item_source_space_ids,
    readable_item_space_ids,
    require_space_role,
)
from ..db import get_session
from ..models import (
    Item,
    Space,
    SpaceStandardPin,
    StandardFamily,
    StandardRevision,
    User,
)

router = APIRouter(tags=["standards"])


class FamilyResponse(BaseModel):
    id: str
    body: str
    designation: str
    title: str | None


class RevisionResponse(BaseModel):
    item_id: str
    space_id: str
    label: str
    issued_on: str | None
    superseded: bool
    title: str | None
    # Newest revision of this family that the caller can read. At most one
    # revision in a response carries it.
    is_latest: bool
    # Set on the revision this space pins, when the request names a space.
    is_pinned: bool


class RevisionsResponse(BaseModel):
    family: FamilyResponse
    revisions: list[RevisionResponse]
    # False when the instance holds a newer revision than any the caller
    # can see — "there is a 2024 edition, ask whoever owns the Standards
    # space for access" rather than silently showing 2007 as current.
    is_latest_known: bool


class LinkRevisionRequest(BaseModel):
    body: str = Field(min_length=1, max_length=120)
    designation: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=120)
    # ISO date. Optional, but a revision without one can never be latest.
    issued_on: str | None = None
    title: str | None = Field(default=None, max_length=500)
    superseded: bool = False


class PinRequest(BaseModel):
    item_id: uuid.UUID


class PinResponse(BaseModel):
    family: FamilyResponse
    item_id: str
    label: str
    issued_on: str | None


def _family_response(family: StandardFamily) -> FamilyResponse:
    return FamilyResponse(
        id=str(family.id),
        body=family.body,
        designation=family.designation,
        title=family.title,
    )


def _order_newest_first[S: Select[Any]](stmt: S) -> S:
    """Newest first, undated last.

    `nulls_last()` on a descending sort is not the default in Postgres —
    DESC puts NULLs first — and an undated revision at the top of the
    dropdown reads as the current edition, which is exactly wrong.
    """
    return stmt.order_by(
        StandardRevision.issued_on.desc().nulls_last(),
        StandardRevision.created_at.desc(),
    )


async def _readable_item(db: AsyncSession, user: User, item_id: uuid.UUID) -> Item:
    """An item the caller can read, inherited spaces included."""
    item = await db.get(Item, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    space = await db.get(Space, item.space_id)
    await require_space_role(
        db, space, user.id, SPACE_ROLE_VIEWER, label="Item not found"
    )
    return item


@router.get("/api/items/{item_id}/revisions", response_model=RevisionsResponse)
async def item_revisions(
    item_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    space: Annotated[
        str | None,
        Query(
            description=(
                "Slug of the space the item is being read in. Only affects "
                "which revision is marked pinned; visibility is the caller's "
                "either way."
            )
        ),
    ] = None,
) -> RevisionsResponse:
    """Every edition of this item's standard that the caller can read."""
    item = await _readable_item(db, user, item_id)

    revision = await db.get(StandardRevision, item.id)
    if revision is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "That item is not a revision of a standard"
        )
    family = await db.get(StandardFamily, revision.family_id)
    if family is None:  # RESTRICT on the FK makes this unreachable
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Standard not found")

    pinned_item_id: uuid.UUID | None = None
    if space is not None:
        target = (
            await db.execute(select(Space).where(Space.slug == space))
        ).scalar_one_or_none()
        await require_space_role(
            db, target, user.id, SPACE_ROLE_VIEWER, label="Space not found"
        )
        assert target is not None
        pin = await db.get(SpaceStandardPin, (target.id, family.id))
        pinned_item_id = pin.item_id if pin is not None else None

    rows = (
        await db.execute(
            _order_newest_first(
                select(StandardRevision, Item)
                .join(Item, Item.id == StandardRevision.item_id)
                .where(
                    StandardRevision.family_id == family.id,
                    Item.deleted_at.is_(None),
                    Item.space_id.in_(readable_item_space_ids(user.id)),
                )
            )
        )
    ).all()

    # The newest one that could be called current: dated, not withdrawn.
    # Computed over the visible set, then compared against the same
    # question asked of the whole instance.
    latest_visible = next(
        (
            rev
            for rev, _ in rows
            if rev.issued_on is not None and not rev.superseded
        ),
        None,
    )
    newest_anywhere = (
        await db.execute(
            _order_newest_first(
                select(StandardRevision)
                .join(Item, Item.id == StandardRevision.item_id)
                .where(
                    StandardRevision.family_id == family.id,
                    StandardRevision.superseded.is_(False),
                    StandardRevision.issued_on.is_not(None),
                    Item.deleted_at.is_(None),
                )
            ).limit(1)
        )
    ).scalar_one_or_none()

    is_latest_known = newest_anywhere is None or (
        latest_visible is not None
        and newest_anywhere.item_id == latest_visible.item_id
    )

    return RevisionsResponse(
        family=_family_response(family),
        revisions=[
            RevisionResponse(
                item_id=str(rev.item_id),
                space_id=str(it.space_id),
                label=rev.label,
                issued_on=rev.issued_on.isoformat() if rev.issued_on else None,
                superseded=rev.superseded,
                title=it.data.get("title") if isinstance(it.data, dict) else None,
                is_latest=latest_visible is not None
                and rev.item_id == latest_visible.item_id,
                is_pinned=rev.item_id == pinned_item_id,
            )
            for rev, it in rows
        ],
        is_latest_known=is_latest_known,
    )


@router.put("/api/items/{item_id}/revision", response_model=RevisionsResponse)
async def link_revision(
    item_id: uuid.UUID,
    payload: LinkRevisionRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> RevisionsResponse:
    """Declare this item to be one edition of one standard.

    Idempotent, and the family is found-or-created from (body,
    designation) — both CITEXT, so "Acme" and "ACME" are one body and
    a second upload of the same standard joins the existing revision
    history instead of starting a parallel one.

    Needs editor on the item's own space: this is metadata about the
    item, and an inherited copy is read-only like everything else.
    """
    item = await db.get(Item, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    space = await db.get(Space, item.space_id)
    await require_space_role(
        db, space, user.id, SPACE_ROLE_EDITOR, label="Item not found"
    )

    await upsert_revision(db, item, payload)
    await db.commit()
    return await item_revisions(item_id, user, db, space=None)


async def upsert_revision(
    db: AsyncSession, item: Item, payload: LinkRevisionRequest
) -> StandardFamily:
    """File `item` under a standard, creating the family if needed.

    Split out from the route so the token API (`/api/v1`) files a
    revision through exactly this code. Family keying — found-or-created
    on (body, designation), both CITEXT — is the part that must not
    drift between the two: two callers disagreeing about what counts as
    the same standard would fork a revision history in half, and the
    symptom (a dropdown missing editions that plainly exist) points
    nowhere near the cause.

    Does not commit; the caller owns the transaction.
    """
    issued_on = _parse_date(payload.issued_on)
    body = payload.body.strip()
    designation = payload.designation.strip()

    family = (
        await db.execute(
            select(StandardFamily).where(
                StandardFamily.body == body,
                StandardFamily.designation == designation,
            )
        )
    ).scalar_one_or_none()
    if family is None:
        family = StandardFamily(
            body=body, designation=designation, title=payload.title
        )
        db.add(family)
        await db.flush()
    elif payload.title and not family.title:
        # Fill a blank title from whoever supplies one first; never
        # overwrite, since the family is shared across every space.
        family.title = payload.title

    revision = await db.get(StandardRevision, item.id)
    if revision is None:
        revision = StandardRevision(item_id=item.id, family_id=family.id)
        db.add(revision)
    revision.family_id = family.id
    revision.label = payload.label.strip()
    revision.issued_on = issued_on
    revision.superseded = payload.superseded
    return family


@router.delete(
    "/api/items/{item_id}/revision", status_code=status.HTTP_204_NO_CONTENT
)
async def unlink_revision(
    item_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Detach an item from its standard. The item and family both stay."""
    item = await db.get(Item, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    space = await db.get(Space, item.space_id)
    await require_space_role(
        db, space, user.id, SPACE_ROLE_EDITOR, label="Item not found"
    )

    revision = await db.get(StandardRevision, item.id)
    if revision is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not linked to a standard")

    # Any space pinning this item loses its pin: a pin naming an item
    # that is no longer a revision of the family means nothing.
    for pin in (
        await db.execute(
            select(SpaceStandardPin).where(SpaceStandardPin.item_id == item.id)
        )
    ).scalars().all():
        await db.delete(pin)
    await db.delete(revision)
    await db.commit()


# ── Pins ─────────────────────────────────────────────────────────────────────


@router.get("/api/spaces/{slug}/pins", response_model=list[PinResponse])
async def list_pins(
    slug: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[PinResponse]:
    """The revisions this space has chosen, one per standard.

    Filtered to what the space can actually see: dropping a subscription
    leaves its pins in place so re-subscribing restores them, and those
    rows would otherwise show up here naming items the space can no
    longer open.
    """
    space = (
        await db.execute(select(Space).where(Space.slug == slug))
    ).scalar_one_or_none()
    await require_space_role(
        db, space, user.id, SPACE_ROLE_VIEWER, label="Space not found"
    )
    assert space is not None

    sources = await item_source_space_ids(db, space.id)
    rows = (
        await db.execute(
            select(SpaceStandardPin, StandardFamily, StandardRevision)
            .join(StandardFamily, StandardFamily.id == SpaceStandardPin.family_id)
            .join(
                StandardRevision,
                StandardRevision.item_id == SpaceStandardPin.item_id,
            )
            .join(Item, Item.id == SpaceStandardPin.item_id)
            .where(
                SpaceStandardPin.space_id == space.id,
                Item.deleted_at.is_(None),
                Item.space_id.in_(sources),
            )
            .order_by(StandardFamily.body, StandardFamily.designation)
        )
    ).all()

    return [
        PinResponse(
            family=_family_response(family),
            item_id=str(pin.item_id),
            label=revision.label,
            issued_on=revision.issued_on.isoformat() if revision.issued_on else None,
        )
        for pin, family, revision in rows
    ]


@router.put("/api/spaces/{slug}/pins/{family_id}", response_model=PinResponse)
async def set_pin(
    slug: str,
    family_id: uuid.UUID,
    payload: PinRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> PinResponse:
    """Choose the revision of one standard that this space uses.

    Needs owner, not editor. Which edition a project builds to is a
    project-level decision with consequences outside the library, and it
    changes what everyone else in the space sees by default.

    The item has to be a revision of `family_id` and has to be visible in
    this space — pinning something the space can't open would produce a
    library that hides four revisions in favour of a fifth nobody can
    read.
    """
    space = (
        await db.execute(select(Space).where(Space.slug == slug))
    ).scalar_one_or_none()
    await require_space_role(
        db, space, user.id, SPACE_ROLE_OWNER, label="Space not found"
    )
    assert space is not None

    family = await db.get(StandardFamily, family_id)
    if family is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Standard not found")

    revision = await db.get(StandardRevision, payload.item_id)
    if revision is None or revision.family_id != family.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"That item is not a revision of {family.designation}",
        )

    item = await db.get(Item, payload.item_id)
    sources = await item_source_space_ids(db, space.id)
    if item is None or item.deleted_at is not None or item.space_id not in sources:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{family.designation} {revision.label} isn't in this space or "
            f"anything it inherits",
        )

    pin = await db.get(SpaceStandardPin, (space.id, family.id))
    if pin is None:
        pin = SpaceStandardPin(
            space_id=space.id, family_id=family.id, item_id=item.id
        )
        db.add(pin)
    pin.item_id = item.id
    pin.pinned_by = user.id
    await db.commit()

    return PinResponse(
        family=_family_response(family),
        item_id=str(item.id),
        label=revision.label,
        issued_on=revision.issued_on.isoformat() if revision.issued_on else None,
    )


@router.delete(
    "/api/spaces/{slug}/pins/{family_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def clear_pin(
    slug: str,
    family_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Stop pinning a standard; every revision becomes visible again."""
    space = (
        await db.execute(select(Space).where(Space.slug == slug))
    ).scalar_one_or_none()
    await require_space_role(
        db, space, user.id, SPACE_ROLE_OWNER, label="Space not found"
    )
    assert space is not None

    pin = await db.get(SpaceStandardPin, (space.id, family_id))
    if pin is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not pinned")
    await db.delete(pin)
    await db.commit()


def _parse_date(raw: str | None) -> date | None:
    """Parse an ISO date, rejecting anything else with a 400."""
    if raw is None or not raw.strip():
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "issued_on must be an ISO date, e.g. 2020-11-01",
        ) from e
