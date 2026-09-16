"""Document profiles: metadata in a file, pushed to an instance.

A profile is one JSON document describing one item — its type, its
metadata fields, optionally the standard it's an edition of and the
files attached to it. You keep them wherever you like (a share, a private
repo, next to the PDFs) and push them when an instance is ready.

    {
      "space": "standards",
      "item_type": "standard",
      "data": {
        "title": "Guidance on the design of widgets — Part 2",
        "standardBody": "NX Standards",
        "designation": "NX-ACME 1234",
        "edition": "2015+A2:2020+NA:2020",
        "nationalAnnex": "NA:2020 (Ruritania)"
      },
      "revision": {
        "body": "NX Standards",
        "designation": "NX-ACME 1234",
        "label": "2015+A2:2020+NA:2020",
        "issued_on": "2020-10-01"
      },
      "attachments": [{"path": "./widgets-part-2.pdf"}]
    }

**Identity.** Pushing twice must update rather than duplicate, so a
profile has to say which document it is. Item ids can't do that — they
differ per instance, which is the whole situation this exists for — so
matching walks three rules, most specific first:

1. `match.item_id`, when the profile names one. Pins to one instance;
   useful for a one-off, useless for portability.
2. `revision`'s (body, designation, label) — the *document's* identity,
   for anything that is an edition of a standard. Survives the file
   being replaced: a re-download, an OCR pass, a corrected printing.
3. the SHA-256 of the profile's first attachment — *content* identity.

Rule 3 is what makes this work for everything that isn't a standard. A
report or a drawing has no designation to be known by, and then the bytes
are the only thing two instances can agree on: push the same file to dev
and to prod and both compute the same hash. It's second to rule 2 rather
than first because the bytes can change while the document doesn't —
publishers stamp per-download watermarks, and shelf's own OCR rewrites
blobs — so where a business identity exists it's the more durable of the
two.

**Attachment paths** are relative to the profile file, so a directory of
profiles and PDFs can be moved or copied whole.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .client import ShelfClient


class ProfileError(ValueError):
    """A profile that can't be acted on, named by file where possible."""


@dataclass
class Profile:
    path: Path
    item_type: str = "document"
    data: dict[str, Any] = field(default_factory=dict)
    space: str | None = None
    revision: dict[str, Any] | None = None
    attachments: list[dict[str, Any]] = field(default_factory=list)
    match_item_id: str | None = None

    @property
    def label(self) -> str:
        """Something human to print. The designation when it has one,
        else the title, else the filename."""
        rev = self.revision or {}
        if rev.get("designation"):
            return f"{rev['designation']} {rev.get('label', '')}".strip()
        title = self.data.get("title")
        return str(title) if title else self.path.name

    def resolve_path(self, entry: dict[str, Any]) -> Path:
        """An attachment's location on disk. Relative to the profile so a
        directory of profiles and files stays portable."""
        return (self.path.parent / entry["path"]).resolve()

    def primary_sha256(self) -> str | None:
        """Content identity: the hash of the first attachment.

        The first rather than all of them because a profile describes one
        document, and the leading file is the document — the rest are
        supplements. A profile with no attachments has no content
        identity, which is a real answer rather than an error.
        """
        if not self.attachments:
            return None
        entry = self.attachments[0]
        declared = entry.get("sha256")
        if declared:
            return str(declared).lower()
        source = self.resolve_path(entry)
        if not source.is_file():
            raise ProfileError(f"{self.path}: no such file — {source}")
        return sha256_of(source)


@dataclass
class PushResult:
    profile: Profile
    item_id: str
    created: bool
    uploaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


REQUIRED_REVISION_KEYS = ("body", "designation", "label")


def load(path: Path) -> Profile:
    """Parse and validate one profile file."""
    try:
        # utf-8-sig, not utf-8: PowerShell's Set-Content, Notepad and a
        # few editors write a UTF-8 BOM, and `json.loads` rejects it
        # outright. Profiles are hand-edited files on Windows machines,
        # so meeting them there is worth one codec name. Reading a file
        # without a BOM is unaffected.
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise ProfileError(f"{path}: not valid JSON — {e}") from e
    if not isinstance(raw, dict):
        raise ProfileError(f"{path}: expected an object at the top level")

    data = raw.get("data", {})
    if not isinstance(data, dict):
        raise ProfileError(f"{path}: 'data' must be an object")

    revision = raw.get("revision")
    if revision is not None:
        if not isinstance(revision, dict):
            raise ProfileError(f"{path}: 'revision' must be an object")
        missing = [k for k in REQUIRED_REVISION_KEYS if not revision.get(k)]
        if missing:
            raise ProfileError(
                f"{path}: 'revision' needs {', '.join(missing)} — that triple is "
                f"what identifies the edition across instances"
            )

    attachments = raw.get("attachments", [])
    if not isinstance(attachments, list):
        raise ProfileError(f"{path}: 'attachments' must be a list")
    normalised: list[dict[str, Any]] = []
    for entry in attachments:
        if isinstance(entry, str):
            entry = {"path": entry}
        if not isinstance(entry, dict) or not entry.get("path"):
            raise ProfileError(f"{path}: each attachment needs a 'path'")
        normalised.append(entry)

    match = raw.get("match") or {}
    return Profile(
        path=path,
        item_type=str(raw.get("item_type", "document")),
        data=data,
        space=raw.get("space"),
        revision=revision,
        attachments=normalised,
        match_item_id=match.get("item_id") if isinstance(match, dict) else None,
    )


def discover(target: Path) -> list[Path]:
    """Every profile under `target` — the file itself, or *.json in a
    directory, sorted so a run is reproducible and diffable."""
    if target.is_file():
        return [target]
    if not target.exists():
        raise ProfileError(f"No such path: {target}")
    return sorted(p for p in target.rglob("*.json") if p.is_file())


def push(
    client: ShelfClient,
    profile: Profile,
    *,
    default_space: str | None = None,
    dry_run: bool = False,
) -> PushResult:
    """Create or update the item this profile describes.

    Metadata is merged rather than replaced, so a field the instance has
    and the profile doesn't is left alone — a profile is a statement
    about the fields it mentions, not a claim to be the whole record.
    """
    space = profile.space or default_space
    existing_id = _find_existing(client, profile, space)

    if dry_run:
        # Still does the reads — a dry run that guesses is worth nothing.
        # It just doesn't create, update or upload. An item that doesn't
        # exist yet has no attachments to compare against, so everything
        # would be uploaded.
        uploaded, skipped = (
            _sync_attachments(client, profile, existing_id, dry_run=True)
            if existing_id
            else ([a["path"] for a in profile.attachments], [])
        )
        return PushResult(
            profile=profile,
            item_id=existing_id or "(new)",
            created=existing_id is None,
            uploaded=uploaded,
            skipped=skipped,
        )

    if existing_id is None:
        item = client.create_item(
            item_type=profile.item_type, data=profile.data, space=space
        )
        item_id = str(item["id"])
        created = True
    else:
        client.update_item(
            existing_id,
            data=profile.data,
            item_type=profile.item_type,
            merge=True,
        )
        item_id = existing_id
        created = False

    if profile.revision is not None:
        client.set_revision(item_id, profile.revision)

    uploaded, skipped = _sync_attachments(client, profile, item_id)
    return PushResult(
        profile=profile,
        item_id=item_id,
        created=created,
        uploaded=uploaded,
        skipped=skipped,
    )


def _find_existing(
    client: ShelfClient, profile: Profile, space: str | None
) -> str | None:
    """The three matching rules, most specific first. See the module
    docstring for why they're in this order."""
    if profile.match_item_id:
        return profile.match_item_id

    if profile.revision is not None:
        matches = client.resolve_standard(
            body=profile.revision["body"],
            designation=profile.revision["designation"],
            label=profile.revision["label"],
            space=space,
        )
        if matches:
            return _one(profile, matches, "item_id", "edition")

    digest = profile.primary_sha256()
    if digest is not None:
        found = client.resolve_attachment(sha256=digest, space=space)
        if found:
            return _one(profile, found, "item_id", "file")

    return None


def _one(
    profile: Profile, matches: list[dict[str, Any]], key: str, kind: str
) -> str:
    """Take the single match, or refuse to guess between several.

    The same document legitimately exists in more than one space — that
    is what copying an item does — so picking the first would silently
    update whichever the database happened to return.
    """
    distinct = {str(m[key]) for m in matches}
    if len(distinct) > 1:
        raise ProfileError(
            f"{profile.path}: that {kind} matches {len(distinct)} items across "
            f'spaces. Add "space" to the profile so the push is unambiguous.'
        )
    return str(matches[0][key])


def _sync_attachments(
    client: ShelfClient, profile: Profile, item_id: str, *, dry_run: bool = False
) -> tuple[list[str], list[str]]:
    """Upload the files the item doesn't already carry.

    Matched on SHA-256 where the instance has one, so re-running a push
    with the same files on disk uploads nothing — and so a file whose
    *contents* changed is re-uploaded even though its name didn't, which
    filename matching would have missed.

    Falls back to filename when the instance's hash is null: rows
    predating the column, and presigned uploads whose extraction hasn't
    run. Null means "unknown", not "different" — treating it as a
    mismatch would re-upload every legacy attachment on the first push.
    """
    if not profile.attachments:
        return [], []

    existing = client.list_attachments(item_id)
    have_hashes = {a["sha256"] for a in existing if a.get("sha256")}
    have_names = {a["filename"] for a in existing}

    uploaded: list[str] = []
    skipped: list[str] = []
    for entry in profile.attachments:
        source = profile.resolve_path(entry)
        declared = entry.get("sha256")

        # Settle "does the far end already have this?" before touching
        # the disk. A profile that declares its hashes is then pushable
        # without the files beside it — worth having, because the file
        # is the big part and re-sending it to an instance that already
        # has the bytes is the thing this check exists to avoid.
        if declared and str(declared).lower() in have_hashes and not entry.get("force"):
            skipped.append(source.name)
            continue

        if not source.is_file():
            raise ProfileError(
                f"{profile.path}: no such file — {source}"
                + (
                    " (its sha256 doesn't match anything on the instance, so "
                    "the file itself is needed)"
                    if declared
                    else ""
                )
            )

        digest = str(declared or sha256_of(source)).lower()
        already = digest in have_hashes or (
            not have_hashes and source.name in have_names
        )
        if already and not entry.get("force"):
            skipped.append(source.name)
            continue
        if not dry_run:
            client.upload(item_id, source, content_type=entry.get("content_type"))
        uploaded.append(source.name)
    return uploaded, skipped


def sha256_of(path: Path, *, chunk: int = 1024 * 1024) -> str:
    """SHA-256 of a file, read in chunks so a large PDF doesn't have to
    fit in memory twice."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def dump(item: dict[str, Any], revisions: dict[str, Any] | None = None) -> dict[str, Any]:
    """Turn an item fetched from an instance back into a profile.

    The round trip is the point: capture what you've curated on one
    instance, keep the file somewhere safe, push it to another.
    """
    profile: dict[str, Any] = {
        "item_type": item.get("item_type", "document"),
        "data": item.get("data", {}),
    }
    if revisions:
        family = revisions.get("family", {})
        current = next(
            (
                r
                for r in revisions.get("revisions", [])
                if str(r.get("item_id")) == str(item.get("id"))
            ),
            None,
        )
        if family and current:
            profile["revision"] = {
                "body": family.get("body"),
                "designation": family.get("designation"),
                "label": current.get("label"),
                "issued_on": current.get("issued_on"),
                "superseded": current.get("superseded", False),
            }
    return profile
