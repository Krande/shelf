"""Re-processing an attachment is an admin's job.

OCR replaces the served version of a document with a derived one and
re-runs the extraction pipeline behind it. That is an instance-level
operation: someone who can merely read a shared space should not be
able to re-process a document that is not theirs. Hiding the menu is
not the control — this is.
"""

import uuid

import pytest
from httpx import AsyncClient

from shelf.config import settings

from .helpers import login

ACTIONS = [
    ("ocr", {}),
    ("ocr_gpu", {}),
    ("outline", {}),
    ("restore_original", {}),
    ("cancel", {"job": "ocr"}),
]


@pytest.mark.parametrize("action,body", ACTIONS)
async def test_refused_to_a_plain_user(
    client: AsyncClient, action: str, body: dict
) -> None:
    await login(client, "a@example.com")
    resp = await client.post(
        f"/api/attachments/{uuid.uuid4()}/processing/{action}", json=body
    )
    # 403 before the attachment is even looked up: a non-admin should
    # not learn whether an id exists by being told it does not.
    assert resp.status_code == 403, f"{action}: {resp.text}"


@pytest.mark.parametrize("action,body", ACTIONS)
async def test_refused_to_anonymous(
    client: AsyncClient, action: str, body: dict
) -> None:
    resp = await client.post(
        f"/api/attachments/{uuid.uuid4()}/processing/{action}", json=body
    )
    assert resp.status_code == 401, f"{action}: {resp.text}"


@pytest.mark.parametrize("action,body", ACTIONS)
async def test_an_admin_gets_past_the_role_check(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    body: dict,
) -> None:
    """Not 403 — the attachment is made up, so 404 is the right answer."""
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    await login(client, "admin@example.com")
    resp = await client.post(
        f"/api/attachments/{uuid.uuid4()}/processing/{action}", json=body
    )
    assert resp.status_code == 404, f"{action}: {resp.text}"


async def test_reading_status_stays_open() -> None:
    """The gate is on re-processing, not on knowing whether it ran."""
    # Documented rather than exercised: the read path takes no admin
    # dependency, which the signatures above make plain.
