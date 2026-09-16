"""Space creation and renaming.

Until now the only space anyone had was the personal one minted at first
login. A shared space needs to be created deliberately, by someone, and
that someone is an instance admin: spaces are cheap to make and awkward
to clean up, and letting every account mint them turns the instance into
a sprawl nobody owns.

Creating a space is not the same as reading one. An admin who creates a
space owns it and can share it; that gives them no access at all to
spaces other people own — see auth/spaces.py.

To let every user create their own instead, swap `require_admin` for
`get_current_user` below; nothing else here depends on the caller being
an admin.

**Renaming** is open to the space's owner and to instance admins. That is
a deliberate exception to "instance admins get nothing here", and a
narrow one: a name and a slug are how a space is *labelled*, not a way
into what it holds. An admin who renames a shared space still cannot list
one item in it. The override exists because shared spaces outlive the
person who created them, and a typo'd slug with no way to fix it is a
poor thing to be stuck with. It stops at shared spaces — nobody gets to
rename somebody else's personal shelf.
"""

import re
import unicodedata
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import get_current_user
from ..auth.roles import require_admin
from ..auth.spaces import SPACE_ROLE_OWNER, effective_role
from ..db import get_session
from ..models import Space, User

router = APIRouter(tags=["spaces"])

# Personal spaces are minted as `u-<hex>`; keeping the prefix reserved
# means a hand-named space can never be mistaken for one, in the UI or in
# `is_personal`.
PERSONAL_SLUG_PREFIX = "u-"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$|^[a-z0-9]$")


def slugify(name: str) -> str:
    """A URL-safe slug from a display name.

    Accents are folded rather than dropped, so "Résumés" becomes
    "resumes" instead of "rsums".
    """
    folded = unicodedata.normalize("NFKD", name)
    ascii_only = folded.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()
    return cleaned[:64].strip("-")


class SpaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    # Optional: derived from the name when omitted. Worth accepting
    # because the slug is what appears in URLs, and a generated one can
    # be unlovely.
    slug: str | None = None


class SpaceUpdate(BaseModel):
    """Both optional — send one, the other, or both. Omitting a field
    leaves it alone, which is what distinguishes this from a PUT."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=64)


class SpaceCreateResponse(BaseModel):
    id: str
    slug: str
    name: str
    is_personal: bool
    # None when the caller has no role here at all — an instance admin
    # renaming a space they aren't a member of, which is allowed and
    # still grants them nothing.
    role: str | None
    is_owner: bool


def _validate_slug(slug: str) -> str:
    """Normalise and check a slug, or raise the 400 that explains it.

    Shared by create and rename so the two can't drift into accepting
    different things — a slug that could be created but not renamed to
    would be a confusing rule to discover.
    """
    cleaned = slug.strip().lower()
    if not cleaned:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Could not derive a slug from that name; pass one explicitly",
        )
    if not _SLUG_RE.match(cleaned):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Slug must be lowercase letters, digits and hyphens, and must "
            "start and end with a letter or digit",
        )
    if cleaned.startswith(PERSONAL_SLUG_PREFIX):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Slugs starting with {PERSONAL_SLUG_PREFIX!r} are reserved for "
            "personal spaces",
        )
    return cleaned


async def _require_free_slug(
    db: AsyncSession, slug: str, *, excluding: Space | None = None
) -> None:
    """409 if some other space already holds `slug`.

    Checked rather than caught: the unique index would also stop this,
    but a 409 naming the slug is far more useful than an integrity error.
    """
    clash = (
        await db.execute(select(Space).where(Space.slug == slug))
    ).scalar_one_or_none()
    if clash is not None and (excluding is None or clash.id != excluding.id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A space with the slug {slug!r} already exists"
        )


@router.post(
    "/api/spaces",
    response_model=SpaceCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_space(
    payload: SpaceCreate,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> SpaceCreateResponse:
    """Create a shared space owned by the caller.

    The creator becomes its owner, so they can add members and set roles
    straight away through /api/spaces/{slug}/members.
    """
    name = payload.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name required")

    slug = _validate_slug(payload.slug or slugify(name))
    await _require_free_slug(db, slug)

    space = Space(slug=slug, name=name, owner_id=admin.id)
    db.add(space)
    await db.commit()
    await db.refresh(space)

    return SpaceCreateResponse(
        id=str(space.id),
        slug=space.slug,
        name=space.name,
        is_personal=False,
        role=SPACE_ROLE_OWNER,
        is_owner=True,
    )


@router.patch("/api/spaces/{slug}", response_model=SpaceCreateResponse)
async def update_space(
    slug: str,
    payload: SpaceUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> SpaceCreateResponse:
    """Rename a space, change its slug, or both.

    The owner may do this to their own space; an instance admin may do it
    to any shared space. See the module docstring for why that exception
    exists and how far it goes — it is a label change, not access.

    Changing the slug changes the space's URL. Nothing stored points at a
    slug (API tokens carry scope labels and collection ids, never slugs),
    so no access breaks — but a link someone bookmarked or pasted into a
    ticket will 404, and there is no redirect from the old one.

    A personal space's slug is fixed. `is_personal` is derived from the
    `u-` prefix, so renaming one would quietly turn it into a shared
    space in every listing that asks.
    """
    space = (
        await db.execute(select(Space).where(Space.slug == slug))
    ).scalar_one_or_none()
    if space is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Space not found")

    is_personal = space.slug.startswith(PERSONAL_SLUG_PREFIX)
    role, _inherited = await effective_role(db, space, user.id)
    is_owner = space.owner_id == user.id

    if not is_owner:
        # 404 rather than 403 for someone with no role and no admin: a
        # space they cannot see should not be confirmed to exist, which
        # is the rule everywhere else (auth/spaces.py).
        if not user.is_admin:
            if role is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Space not found")
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Only the space's owner can rename it",
            )
        if is_personal:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "A personal space can only be renamed by the person it "
                "belongs to",
            )

    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name required")
        space.name = name

    if payload.slug is not None:
        if is_personal:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "A personal space's slug is fixed; its name can still be "
                "changed.",
            )
        new_slug = _validate_slug(payload.slug)
        if new_slug != space.slug:
            await _require_free_slug(db, new_slug, excluding=space)
            space.slug = new_slug

    await db.commit()
    await db.refresh(space)

    return SpaceCreateResponse(
        id=str(space.id),
        slug=space.slug,
        name=space.name,
        is_personal=is_personal,
        role=SPACE_ROLE_OWNER if is_owner else role,
        is_owner=is_owner,
    )
