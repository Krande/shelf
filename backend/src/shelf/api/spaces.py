"""Space creation.

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
"""

import re
import unicodedata
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.roles import require_admin
from ..auth.spaces import SPACE_ROLE_OWNER
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
    # because the slug is what appears in URLs and in API-token scopes,
    # and a generated one can be unlovely.
    slug: str | None = None


class SpaceCreateResponse(BaseModel):
    id: str
    slug: str
    name: str
    is_personal: bool
    role: str
    is_owner: bool


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

    slug = (payload.slug or slugify(name)).strip().lower()
    if not slug:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Could not derive a slug from that name; pass one explicitly",
        )
    if not _SLUG_RE.match(slug):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Slug must be lowercase letters, digits and hyphens, and must "
            "start and end with a letter or digit",
        )
    if slug.startswith(PERSONAL_SLUG_PREFIX):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Slugs starting with {PERSONAL_SLUG_PREFIX!r} are reserved for "
            "personal spaces",
        )

    # Checked rather than caught: the unique index would also stop this,
    # but a 409 naming the slug is far more useful than an integrity error.
    clash = (
        await db.execute(select(Space.id).where(Space.slug == slug))
    ).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A space with the slug {slug!r} already exists"
        )

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
