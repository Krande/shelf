"""Profile loading and the identity-matching chain.

The chain is the thing worth pinning: get it wrong and a push either
duplicates documents or overwrites the wrong one, and both fail quietly.
A fake client stands in for a server so these run with nothing up.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from shelf_cli.profiles import ProfileError, discover, dump, load, push, sha256_of


class FakeClient:
    """Records calls and answers lookups from whatever it was seeded with."""

    def __init__(
        self,
        *,
        standards: list[dict[str, Any]] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        existing_files: list[dict[str, Any]] | None = None,
    ) -> None:
        self._standards = standards or []
        self._attachments = attachments or []
        self._existing_files = existing_files or []
        self.created: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []
        self.revisions: list[tuple[str, dict[str, Any]]] = []
        self.uploads: list[tuple[str, str]] = []

    def resolve_standard(self, **kw: Any) -> list[dict[str, Any]]:
        self.last_standard_query = kw
        return self._standards

    def resolve_attachment(self, **kw: Any) -> list[dict[str, Any]]:
        self.last_attachment_query = kw
        return self._attachments

    def create_item(self, **kw: Any) -> dict[str, Any]:
        self.created.append(kw)
        return {"id": "new-item"}

    def update_item(self, item_id: str, **kw: Any) -> dict[str, Any]:
        self.updated.append({"item_id": item_id, **kw})
        return {"id": item_id}

    def set_revision(self, item_id: str, revision: dict[str, Any]) -> dict[str, Any]:
        self.revisions.append((item_id, revision))
        return {}

    def list_attachments(self, item_id: str) -> list[dict[str, Any]]:
        return self._existing_files

    def upload(self, item_id: str, path: Path, **kw: Any) -> dict[str, Any]:
        self.uploads.append((str(item_id), path.name))
        return {}


def write_profile(tmp_path: Path, body: dict[str, Any], name: str = "p.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def write_pdf(tmp_path: Path, name: str = "doc.pdf", content: bytes = b"%PDF-1.7\n") -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


REVISION = {
    "body": "NX Standards",
    "designation": "NX-ACME 1234",
    "label": "2015+A2:2020",
}


# ── Loading ──────────────────────────────────────────────────────────────


def test_load_reads_the_fields(tmp_path: Path) -> None:
    path = write_profile(
        tmp_path,
        {
            "space": "standards",
            "item_type": "standard",
            "data": {"title": "Widgets"},
            "revision": REVISION,
            "attachments": ["doc.pdf"],
        },
    )
    profile = load(path)
    assert profile.space == "standards"
    assert profile.item_type == "standard"
    assert profile.data == {"title": "Widgets"}
    # A bare string is sugar for {"path": ...}.
    assert profile.attachments == [{"path": "doc.pdf"}]


def test_a_revision_missing_part_of_its_identity_is_rejected(tmp_path: Path) -> None:
    path = write_profile(
        tmp_path, {"revision": {"body": "NX Standards", "designation": "NX-ACME 1234"}}
    )
    with pytest.raises(ProfileError, match="label"):
        load(path)


def test_a_utf8_bom_is_tolerated(tmp_path: Path) -> None:
    """PowerShell and Notepad write one; `json.loads` rejects it. These
    are hand-edited files on Windows machines."""
    path = tmp_path / "bom.json"
    path.write_text(
        json.dumps({"data": {"title": "Widgets — Part 2"}}), encoding="utf-8-sig"
    )
    assert load(path).data["title"] == "Widgets — Part 2"


def test_bad_json_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ProfileError, match=r"broken\.json"):
        load(path)


def test_discover_finds_a_directory_in_a_stable_order(tmp_path: Path) -> None:
    for name in ("b.json", "a.json", "c.json"):
        write_profile(tmp_path, {"data": {}}, name)
    assert [p.name for p in discover(tmp_path)] == ["a.json", "b.json", "c.json"]


# ── Matching: rule 1, explicit id ────────────────────────────────────────


def test_an_explicit_item_id_wins(tmp_path: Path) -> None:
    path = write_profile(
        tmp_path, {"match": {"item_id": "pinned"}, "revision": REVISION, "data": {}}
    )
    client = FakeClient(standards=[{"item_id": "from-lookup"}])
    result = push(client, load(path))
    assert result.item_id == "pinned"
    assert result.created is False


# ── Matching: rule 2, the document's own identity ────────────────────────


def test_a_known_edition_is_updated_not_duplicated(tmp_path: Path) -> None:
    path = write_profile(tmp_path, {"revision": REVISION, "data": {"title": "x"}})
    client = FakeClient(standards=[{"item_id": "existing"}])
    result = push(client, load(path))

    assert result.item_id == "existing"
    assert result.created is False
    assert client.created == []
    # Merged, so a field the instance has and the profile omits survives.
    assert client.updated[0]["merge"] is True


def test_an_unknown_edition_is_created_and_filed(tmp_path: Path) -> None:
    path = write_profile(tmp_path, {"revision": REVISION, "data": {"title": "x"}})
    client = FakeClient(standards=[])
    result = push(client, load(path))

    assert result.created is True
    assert client.created
    assert client.revisions == [("new-item", REVISION)]


def test_an_edition_in_two_spaces_refuses_to_guess(tmp_path: Path) -> None:
    path = write_profile(tmp_path, {"revision": REVISION, "data": {}})
    client = FakeClient(standards=[{"item_id": "a"}, {"item_id": "b"}])
    with pytest.raises(ProfileError, match="unambiguous"):
        push(client, load(path))


# ── Matching: rule 3, content identity ───────────────────────────────────


def test_a_document_with_no_business_key_matches_on_the_file(tmp_path: Path) -> None:
    """The case that makes the SHA load-bearing: a report has no
    designation, so without this it would be re-created on every push."""
    pdf = write_pdf(tmp_path)
    path = write_profile(
        tmp_path, {"item_type": "report", "data": {"title": "Q3"}, "attachments": ["doc.pdf"]}
    )
    client = FakeClient(attachments=[{"item_id": "already-there"}])
    result = push(client, load(path))

    assert result.item_id == "already-there"
    assert result.created is False
    assert client.last_attachment_query["sha256"] == sha256_of(pdf)


def test_content_identity_is_only_consulted_after_the_business_one(
    tmp_path: Path,
) -> None:
    """A re-download changes the bytes but not the document, so the
    edition must win over the hash when both could match."""
    write_pdf(tmp_path)
    path = write_profile(
        tmp_path, {"revision": REVISION, "data": {}, "attachments": ["doc.pdf"]}
    )
    client = FakeClient(
        standards=[{"item_id": "by-edition"}], attachments=[{"item_id": "by-hash"}]
    )
    assert push(client, load(path)).item_id == "by-edition"


def test_a_declared_hash_is_trusted_over_reading_the_file(tmp_path: Path) -> None:
    """So a profile can be pushed without the PDF beside it, when the
    document already exists on the far end."""
    path = write_profile(
        tmp_path,
        {"data": {}, "attachments": [{"path": "absent.pdf", "sha256": "a" * 64}]},
    )
    client = FakeClient(
        attachments=[{"item_id": "found"}],
        existing_files=[{"filename": "absent.pdf", "sha256": "a" * 64}],
    )
    result = push(client, load(path))
    assert result.item_id == "found"
    assert client.last_attachment_query["sha256"] == "a" * 64
    # Nothing uploaded, and the missing file was never opened.
    assert result.uploaded == [] and client.uploads == []


def test_a_declared_hash_the_instance_lacks_still_needs_the_file(
    tmp_path: Path,
) -> None:
    """The declaration says which bytes; it can't stand in for them."""
    path = write_profile(
        tmp_path,
        {
            "match": {"item_id": "i"},
            "data": {},
            "attachments": [{"path": "absent.pdf", "sha256": "b" * 64}],
        },
    )
    client = FakeClient(existing_files=[{"filename": "other.pdf", "sha256": "c" * 64}])
    with pytest.raises(ProfileError, match="the file itself is needed"):
        push(client, load(path))


def test_a_profile_with_nothing_to_match_on_creates(tmp_path: Path) -> None:
    path = write_profile(tmp_path, {"data": {"title": "loose"}})
    client = FakeClient()
    assert push(client, load(path)).created is True


# ── Attachments ──────────────────────────────────────────────────────────


def test_a_file_already_present_is_not_re_uploaded(tmp_path: Path) -> None:
    pdf = write_pdf(tmp_path)
    path = write_profile(
        tmp_path, {"match": {"item_id": "i"}, "data": {}, "attachments": ["doc.pdf"]}
    )
    client = FakeClient(
        existing_files=[{"filename": "doc.pdf", "sha256": sha256_of(pdf)}]
    )
    result = push(client, load(path))
    assert result.uploaded == []
    assert result.skipped == ["doc.pdf"]
    assert client.uploads == []


def test_changed_contents_are_re_uploaded_under_the_same_name(
    tmp_path: Path,
) -> None:
    """What filename matching would have missed."""
    write_pdf(tmp_path, content=b"%PDF-1.7\nnew")
    path = write_profile(
        tmp_path, {"match": {"item_id": "i"}, "data": {}, "attachments": ["doc.pdf"]}
    )
    client = FakeClient(
        existing_files=[{"filename": "doc.pdf", "sha256": hashlib.sha256(b"old").hexdigest()}]
    )
    assert push(client, load(path)).uploaded == ["doc.pdf"]


def test_a_null_hash_falls_back_to_the_filename(tmp_path: Path) -> None:
    """Null means unknown, not different — legacy rows and pending
    presigned uploads must not be re-uploaded wholesale."""
    write_pdf(tmp_path)
    path = write_profile(
        tmp_path, {"match": {"item_id": "i"}, "data": {}, "attachments": ["doc.pdf"]}
    )
    client = FakeClient(existing_files=[{"filename": "doc.pdf", "sha256": None}])
    assert push(client, load(path)).skipped == ["doc.pdf"]


def test_a_missing_file_names_itself(tmp_path: Path) -> None:
    path = write_profile(
        tmp_path, {"match": {"item_id": "i"}, "data": {}, "attachments": ["gone.pdf"]}
    )
    with pytest.raises(ProfileError, match=r"gone\.pdf"):
        push(FakeClient(), load(path))


def test_attachment_paths_are_relative_to_the_profile(tmp_path: Path) -> None:
    """So a directory of profiles and PDFs can be moved whole."""
    nested = tmp_path / "sub"
    nested.mkdir()
    write_pdf(nested)
    path = write_profile(nested, {"match": {"item_id": "i"}, "data": {}, "attachments": ["doc.pdf"]})
    assert push(FakeClient(), load(path)).uploaded == ["doc.pdf"]


# ── Dry run and capture ──────────────────────────────────────────────────


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    path = write_profile(tmp_path, {"revision": REVISION, "data": {}})
    client = FakeClient(standards=[])
    result = push(client, load(path), dry_run=True)
    assert result.created is True
    assert client.created == [] and client.updated == [] and client.uploads == []


def test_dump_round_trips_an_item_into_a_profile() -> None:
    item = {"id": "i1", "item_type": "standard", "data": {"title": "Widgets"}}
    revisions = {
        "family": {"body": "NX Standards", "designation": "NX-ACME 1234"},
        "revisions": [
            {"item_id": "i1", "label": "2015+A2:2020", "issued_on": "2020-10-01",
             "superseded": False},
            {"item_id": "other", "label": "2015", "issued_on": "2015-01-01"},
        ],
    }
    profile = dump(item, revisions)
    assert profile["data"] == {"title": "Widgets"}
    # The *item's own* revision, not whichever came first.
    assert profile["revision"]["label"] == "2015+A2:2020"
    assert profile["revision"]["designation"] == "NX-ACME 1234"


def test_dump_omits_the_revision_for_a_plain_item() -> None:
    profile = dump({"id": "i1", "item_type": "report", "data": {}}, None)
    assert "revision" not in profile
