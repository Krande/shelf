import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import UUIDPK, Base, Timestamps


class ApiToken(UUIDPK, Timestamps, Base):
    """User-issued API token for scripted access (curl, importers,
    etc.).

    The plaintext is only ever returned at creation time; we keep a
    SHA-256 hash and a 12-char prefix for display (so the user can
    recognise their tokens in a list). Scopes are simple labels
    ("upload", "search", "download") — coarse-grained on purpose.
    Two optional allow-lists narrow a token below what its user can
    reach. Both default to NULL, meaning "everything the user can":

    * `allowed_space_ids` — the coarse one. A token for an import script
      that should only ever touch one project space, or a read-only
      token for a tool that should see the shared Standards space and
      nothing of your own.
    * `allowed_collection_ids` — finer, within a space.

    Neither can widen access: both are intersected with what the user
    can read at request time, so a token outlives neither a revoked
    membership nor a dropped subscription.
    """

    __tablename__ = "api_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    prefix: Mapped[str] = mapped_column(String, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # Space ids as strings, not slugs: slugs are renameable, and an
    # allow-list that silently stops matching when someone tidies a name
    # would be a nasty way to lose access.
    allowed_space_ids: Mapped[list[str] | None] = mapped_column(
        JSONB, nullable=True
    )
    allowed_collection_ids: Mapped[list[str] | None] = mapped_column(
        JSONB, nullable=True
    )
    # When True and allowed_collection_ids is set, the gate widens to
    # also accept any collection nested under one of those ids. Walked
    # at request time so collections added after mint are covered too.
    include_descendants: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (Index("ix_api_tokens_user", "user_id"),)
