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
    `allowed_collection_ids` further constrains the token to a subset
    of the user's collections; NULL means "all collections the user
    can see".
    """

    __tablename__ = "api_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    prefix: Mapped[str] = mapped_column(String, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
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
