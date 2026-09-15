"""Attachment key layout.

New attachments live under their space, so a bucket policy, lifecycle
rule or "delete this space" sweep can address one space's objects without
consulting the database. Keys are stored per row, so nothing that was
written under the older flat layout is touched.
"""

import uuid

from shelf.services.storage import attachment_storage_key, derived_key, original_key


def test_key_leads_with_the_space() -> None:
    space, item, att = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    key = attachment_storage_key(space, item, att)
    assert key == f"spaces/{space}/items/{item}/attachments/{att}"
    assert key.startswith(f"spaces/{space}/")


def test_one_prefix_covers_a_whole_space() -> None:
    space = uuid.uuid4()
    keys = [
        attachment_storage_key(space, uuid.uuid4(), uuid.uuid4())
        for _ in range(3)
    ]
    assert all(k.startswith(f"spaces/{space}/") for k in keys)


def test_distinct_spaces_do_not_share_a_prefix() -> None:
    item, att = uuid.uuid4(), uuid.uuid4()
    a = attachment_storage_key(uuid.uuid4(), item, att)
    b = attachment_storage_key(uuid.uuid4(), item, att)
    assert not a.startswith(b.rsplit("/items/", 1)[0])


def test_filename_is_absent() -> None:
    """The name lives on the row; the URL is presigned. Putting original
    names in the path would leak them for nothing."""
    key = attachment_storage_key(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    assert key.count("/") == 5
    assert "." not in key


def test_derivations_inherit_the_parent_layout() -> None:
    """Workers build derived and original keys by extending the parent
    key, so they follow whatever layout that attachment was written
    with — including the older flat one."""
    key = attachment_storage_key(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    assert derived_key(key, "ocr").startswith(key + "/derived/ocr-")
    assert original_key(key) == key + ".original"

    legacy = "items/abc/attachments/def"
    assert derived_key(legacy, "ocr").startswith(legacy + "/derived/ocr-")
    assert original_key(legacy) == legacy + ".original"
