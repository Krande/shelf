import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from .base import UUIDPK, Base, Timestamps

ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLES = (ROLE_ADMIN, ROLE_USER)


class User(UUIDPK, Timestamps, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'user')", name="ck_users_role"),
    )

    email: Mapped[str] = mapped_column(CITEXT(), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    # Instance-wide role. Not to be confused with api_tokens.scopes (per-token
    # bearer permissions) or space_memberships.role (per-space, still unused).
    role: Mapped[str] = mapped_column(
        Text, nullable=False, default=ROLE_USER, server_default=ROLE_USER
    )

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


class Identity(UUIDPK, Base):
    __tablename__ = "identities"
    __table_args__ = (UniqueConstraint("idp", "subject", name="uq_identities_idp_subject"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    idp: Mapped[str] = mapped_column(String, nullable=False)
    subject: Mapped[str] = mapped_column(String, nullable=False)
