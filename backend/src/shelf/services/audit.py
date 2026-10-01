"""The audit log: one call per thing worth recording.

`record` only adds a row to the caller's session — it never commits.
Call it before the handler's own `await db.commit()`, so the entry and
the change it describes land or fail together. That is also why this
isn't a queue message: NATS is optional (dev and tests run without it),
and an audit trail with gaps whenever the broker is down isn't one.

Reads are logged after the fact the same way, with a commit of their
own; see `record_read`.
"""

import uuid
from enum import StrEnum
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AuditEvent, Item, User

Via = Literal["web", "api"]


def item_label(item: Item) -> str:
    """The item's title for the audit log, captured now so the entry
    still reads as something after the item is gone."""
    data = item.data if isinstance(item.data, dict) else {}
    title = data.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return "(untitled)"


class AuditAction(StrEnum):
    # Spaces and sharing
    space_create = "space.create"
    space_update = "space.update"
    space_member_add = "space.member.add"
    space_member_role = "space.member.role"
    space_member_remove = "space.member.remove"
    space_subscribe = "space.subscribe"
    space_unsubscribe = "space.unsubscribe"
    space_subscriber_remove = "space.subscriber.remove"

    # Documents and files
    item_create = "item.create"
    item_update = "item.update"
    item_trash = "item.trash"
    item_restore = "item.restore"
    item_delete = "item.delete"
    item_copy = "item.copy"
    item_revision = "item.revision"
    attachment_upload = "attachment.upload"
    attachment_delete = "attachment.delete"

    # Reads. A "view" is the reader opening the file in the browser; a
    # "download" is the file leaving it — the Download button, a ZIP, an
    # export or an API client.
    attachment_view = "attachment.view"
    attachment_download = "attachment.download"
    collection_download = "collection.download"
    export_zip = "export.zip"
    export_item = "export.item"
    export_space = "export.space"

    # Organisation
    collection_create = "collection.create"
    collection_update = "collection.update"
    collection_delete = "collection.delete"
    item_collections = "item.collections"
    tag_create = "tag.create"
    tag_update = "tag.update"
    tag_delete = "tag.delete"
    item_tags = "item.tags"
    standard_revision_set = "standard.revision.set"
    standard_revision_delete = "standard.revision.delete"
    standard_pin_set = "standard.pin.set"
    standard_pin_remove = "standard.pin.remove"

    # Administration
    user_create = "user.create"
    user_update = "user.update"
    processing_ocr = "processing.ocr"
    processing_outline = "processing.outline"
    processing_restore = "processing.restore"
    processing_cancel = "processing.cancel"


def record(
    db: AsyncSession,
    actor: User | None,
    action: AuditAction,
    *,
    space_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    label: str | None = None,
    details: dict[str, Any] | None = None,
    via: Via = "web",
) -> None:
    """Add an audit row to `db`. The caller's commit writes it."""
    db.add(
        AuditEvent(
            actor_id=actor.id if actor else None,
            actor_email=actor.email if actor else None,
            action=str(action),
            space_id=space_id,
            target_type=target_type,
            target_id=target_id,
            target_label=label,
            # Empty dicts are noise in the UI; store NULL instead.
            details=details or None,
            via=via,
        )
    )


async def record_read(
    db: AsyncSession,
    actor: User | None,
    action: AuditAction,
    **kwargs: Any,
) -> None:
    """`record` plus a commit, for GET handlers that change nothing else.

    A read has no write of its own to ride along with, so it commits by
    itself — after the access checks have passed, so a refused request
    is not logged as a download.
    """
    record(db, actor, action, **kwargs)
    await db.commit()
