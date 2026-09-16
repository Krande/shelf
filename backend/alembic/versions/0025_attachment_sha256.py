"""attachments: sha256 of the uploaded bytes

Revision ID: 0025_attachment_sha256
Revises: 0024_token_space_scope
Create Date: 2026-09-16

Content identity for a file, so two instances can agree on what a
document *is* without sharing a database. That matters for pushing
curated metadata from one instance to another: a standard can be
identified by (body, designation, edition), but a report or a drawing
has no such business key, and then the bytes are the only thing both
ends can match on.

Indexed, not unique. The same file legitimately exists in two spaces —
copying an item puts it there deliberately — so this is for lookup, not
for constraining, and explicitly not for deduplicating storage: blobs are
keyed by space so a bucket policy can address one space's objects without
consulting the database.

Backfilled to NULL. The extract worker fills it in as it goes, since it
downloads the blob anyway; nothing depends on it being present.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_attachment_sha256"
down_revision: str | Sequence[str] | None = "0024_token_space_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "attachments", sa.Column("sha256", sa.String(length=64), nullable=True)
    )
    op.create_index("ix_attachments_sha256", "attachments", ["sha256"])


def downgrade() -> None:
    op.drop_index("ix_attachments_sha256", table_name="attachments")
    op.drop_column("attachments", "sha256")
