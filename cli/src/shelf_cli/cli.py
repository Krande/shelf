"""`shelf` — command-line client for a shelf instance.

Config comes from three layers, most-specific first:

    CLI flag / env var   ->   shelf.toml   ->   built-in default

Usage:

    shelf whoami                                   # check the token works
    shelf items get <id>
    shelf items set <id> --set standardBody="NX Standards" \\
                         --set designation="NX-ACME 1234"
    shelf items set <id> --unset supersedes
    shelf items create --type standard --space standards --set title="…"
    shelf profiles push ./profiles/                # create or update
    shelf profiles push ./profiles/ --dry-run      # say what would happen
    shelf profiles pull <id> -o widgets.json       # capture what's there
    shelf search "widgets"
    shelf search "load case" --scope fulltext --hits 5
    shelf browse "load case"                       # interactive
    shelf open <attachment-id> --page 12           # local PDF, at page 12
    shelf open <attachment-id> --page 12 --web     # the shelf reader
    shelf download <attachment-id> -o ./pdfs/      # OCR'd copy where there is one
    shelf download <attachment-id> --version original
    shelf versions <attachment-id>                 # its OCR'd / outlined copies

Env used:
    SHELF_API_BASE_URL   instance to talk to, e.g. https://shelf.example.com
    SHELF_API_TOKEN      bearer token, minted under Settings -> API tokens
    SHELF_CLI_TOML       path to shelf.toml (default: ./shelf.toml)
    SHELF_PDF_VIEWER     command template for local PDFs, e.g.
                         'SumatraPDF.exe -page {page} "{path}"'
    SHELF_CACHE_DIR      where downloaded PDFs are kept
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .client import ApiError, ShelfClient
from .config import Config, ConfigError, resolve
from .profiles import ProfileError, discover, dump, load, push


def _parse_set(pairs: list[str] | None) -> dict[str, Any]:
    """`--set key=value` into a dict.

    Values are JSON when they parse as JSON and strings otherwise, so
    `--set numberOfPages=74` stores a number and `--set edition=2020-A1`
    stays a string without anyone having to quote-escape.
    """
    out: dict[str, Any] = {}
    for pair in pairs or []:
        key, sep, raw = pair.partition("=")
        if not sep or not key.strip():
            raise ConfigError(f"--set expects key=value, got {pair!r}")
        try:
            out[key.strip()] = json.loads(raw)
        except json.JSONDecodeError:
            out[key.strip()] = raw
    return out


def _config(args: argparse.Namespace) -> Config:
    cfg = resolve(
        base_url=args.api_base,
        token=args.token,
        space=getattr(args, "space", None),
        config_path=args.config,
    )
    args._space = cfg.space
    return cfg


def _client(args: argparse.Namespace) -> ShelfClient:
    cfg = _config(args)
    return ShelfClient(cfg.base_url, cfg.token)


def _emit(payload: Any) -> None:
    # ensure_ascii=False so a title with an em-dash prints as one rather
    # than as — — this output is meant to be read, and piped into
    # a file it should still be valid UTF-8 JSON.
    print(json.dumps(payload, indent=2, default=str, ensure_ascii=False))


# ── commands ─────────────────────────────────────────────────────────────


def cmd_whoami(args: argparse.Namespace) -> int:
    """Prove the token is accepted, and say what it can reach.

    Reads the space list rather than inferring one from collections: an
    instance that uses no collections would otherwise report "spaces:
    []" for a token that can see plenty, which is the opposite of
    reassuring.
    """
    with _client(args) as client:
        spaces = client.spaces()
        _emit(
            {
                "ok": True,
                "spaces": [
                    {
                        "slug": s["slug"],
                        "name": s["name"],
                        "access": "read-write" if s["writable"] else "read-only",
                    }
                    for s in spaces
                ],
            }
        )
    return 0


def cmd_items_get(args: argparse.Namespace) -> int:
    with _client(args) as client:
        _emit(client.get_item(args.item_id))
    return 0


def cmd_items_set(args: argparse.Namespace) -> int:
    data = _parse_set(args.set)
    for key in args.unset or []:
        data[key] = None
    if not data and not args.type:
        raise ConfigError("Nothing to do — pass --set, --unset or --type")
    with _client(args) as client:
        _emit(
            client.update_item(
                args.item_id,
                data=data or None,
                item_type=args.type,
                # Merge, so setting one field doesn't silently drop the
                # rest of a document's metadata. `--replace` opts out.
                merge=not args.replace,
            )
        )
    return 0


def cmd_items_create(args: argparse.Namespace) -> int:
    with _client(args) as client:
        _emit(
            client.create_item(
                item_type=args.type,
                data=_parse_set(args.set),
                space=args.space or args._space,
            )
        )
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    with _client(args) as client:
        _emit(
            client.search(
                args.query,
                limit=args.limit,
                offset=args.offset,
                scope=args.scope,
                hits=args.hits,
                space=args.in_space,
                collection=args.collection,
                subtree=args.subtree,
            )
        )
    return 0


def cmd_browse(args: argparse.Namespace) -> int:
    # Imported here so Textual's import cost lands on this command only.
    from .tui import run

    run(_config(args), " ".join(args.query))
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    """Open one attachment at a page — the TUI's two actions, scriptable."""
    from . import opener

    cfg = _config(args)
    if args.web:
        print(opener.open_web(cfg.base_url, args.attachment_id, args.page, args.find))
        return 0
    with ShelfClient(cfg.base_url, cfg.token) as client:
        # Cosmetic — the cache is keyed by id — but it's what the browser
        # tab shows, and search output carries it to pass along.
        filename = args.filename or f"{args.attachment_id}.pdf"
        path = opener.fetch(
            client, args.attachment_id, filename, version=args.version, refresh=args.refresh
        )
    opened = opener.open_local(path, args.page, viewer=cfg.pdf_viewer, query=args.find)
    print(path)
    if not opened.at_page:
        print(
            f"note: opened with the {opened.how}, which takes no page — go to p.{args.page}. "
            "Set [viewer] pdf in shelf.toml to use a viewer that does.",
            file=sys.stderr,
        )
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    """Save one attachment. `latest` by default: for a scan, the copy
    shelf has OCR'd, which is the one a viewer can search and copy from."""
    from .opener import safe_name

    with _client(args) as client:
        info = client.attachment_versions(args.attachment_id)
        version = str(info["latest"]) if args.version == "latest" else args.version
        dest = Path(args.output) if args.output else Path(safe_name(info["filename"]))
        if dest.is_dir():
            dest = dest / safe_name(info["filename"])
        client.download(args.attachment_id, dest, version=version)
    print(dest)
    if version != "original":
        kind = next((v["kind"] for v in info["versions"] if v["id"] == version), version)
        print(f"version: {kind} ({version})", file=sys.stderr)
    return 0


def cmd_versions(args: argparse.Namespace) -> int:
    with _client(args) as client:
        _emit(client.attachment_versions(args.attachment_id))
    return 0


def cmd_profiles_push(args: argparse.Namespace) -> int:
    paths = discover(Path(args.path))
    if not paths:
        print(f"No profiles found under {args.path}", file=sys.stderr)
        return 1

    failures = 0
    with _client(args) as client:
        for path in paths:
            try:
                profile = load(path)
                result = push(
                    client,
                    profile,
                    default_space=args.space or args._space,
                    dry_run=args.dry_run,
                )
            except (ProfileError, ApiError) as e:
                failures += 1
                print(f"FAIL  {path}: {e}", file=sys.stderr)
                continue
            verb = "would create" if args.dry_run and result.created else (
                "would update" if args.dry_run else
                "created" if result.created else "updated"
            )
            extra = ""
            if result.uploaded:
                extra += f" +{len(result.uploaded)} file(s)"
            if result.skipped:
                extra += f" ({len(result.skipped)} already present)"
            print(f"{verb:14} {result.profile.label}  [{result.item_id}]{extra}")

    if failures:
        print(f"\n{failures} of {len(paths)} profile(s) failed", file=sys.stderr)
    return 1 if failures else 0


def cmd_profiles_pull(args: argparse.Namespace) -> int:
    with _client(args) as client:
        item = client.get_item(args.item_id)
        revisions: dict[str, Any] | None = None
        try:
            revisions = client.get_revisions(args.item_id)
        except ApiError as e:
            # 404 just means "not filed under a standard", which is a
            # normal state and not worth failing a capture over.
            if e.status_code != 404:
                raise
        profile = dump(item, revisions)

    text = json.dumps(profile, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(text)
    return 0


# ── wiring ───────────────────────────────────────────────────────────────

_VERSION_HELP = (
    "latest (default): what the shelf reader shows — OCR'd and bookmarked where "
    "that has run; original: the bytes as uploaded; or an id from `shelf versions`"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="shelf", description=__doc__.split("\n")[0])
    parser.add_argument("--api-base", help="instance URL (env: SHELF_API_BASE_URL)")
    parser.add_argument("--token", help="API token (env: SHELF_API_TOKEN)")
    parser.add_argument("--config", help="path to shelf.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("whoami", help="check the token and report what it sees").set_defaults(
        func=cmd_whoami
    )

    items = sub.add_parser("items", help="read and write item metadata")
    items_sub = items.add_subparsers(dest="items_command", required=True)

    get = items_sub.add_parser("get", help="print one item as JSON")
    get.add_argument("item_id")
    get.set_defaults(func=cmd_items_get)

    set_ = items_sub.add_parser("set", help="set metadata fields on an item")
    set_.add_argument("item_id")
    set_.add_argument(
        "--set",
        action="append",
        metavar="KEY=VALUE",
        help="set a field; repeatable. JSON values are parsed, anything else is a string",
    )
    set_.add_argument(
        "--unset", action="append", metavar="KEY", help="remove a field; repeatable"
    )
    set_.add_argument("--type", help="change the item type")
    set_.add_argument(
        "--replace",
        action="store_true",
        help="replace the whole metadata blob instead of merging into it",
    )
    set_.set_defaults(func=cmd_items_set)

    create = items_sub.add_parser("create", help="create an item")
    create.add_argument("--type", default="document")
    create.add_argument("--space", help="target space slug")
    create.add_argument("--set", action="append", metavar="KEY=VALUE")
    create.set_defaults(func=cmd_items_create)

    profiles = sub.add_parser("profiles", help="push and pull document profiles")
    profiles_sub = profiles.add_subparsers(dest="profiles_command", required=True)

    push_cmd = profiles_sub.add_parser(
        "push", help="create or update items from profile files"
    )
    push_cmd.add_argument("path", help="a profile file, or a directory of them")
    push_cmd.add_argument("--space", help="default space for profiles that omit one")
    push_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would happen without writing anything",
    )
    push_cmd.set_defaults(func=cmd_profiles_push)

    pull_cmd = profiles_sub.add_parser(
        "pull", help="capture an existing item as a profile file"
    )
    pull_cmd.add_argument("item_id")
    pull_cmd.add_argument("-o", "--output", help="write here instead of stdout")
    pull_cmd.set_defaults(func=cmd_profiles_pull)

    search = sub.add_parser(
        "search",
        help="search items the token can read, PDF text included",
    )
    search.add_argument("query", nargs="?")
    search.add_argument("--limit", type=int, default=50)
    search.add_argument("--offset", type=int, default=0)
    search.add_argument(
        "--scope",
        action="append",
        choices=["title", "creators", "abstract", "extra", "fulltext"],
        help="search only these fields; repeatable. Default: all of them",
    )
    search.add_argument(
        "--hits",
        type=int,
        default=0,
        metavar="N",
        help="include each result's first N matching PDF pages (max 50)",
    )
    # `--space` would collide with the config-default space `_client`
    # reads off args, which means "where to write", not "where to look".
    search.add_argument("--in-space", metavar="SLUG", help="only items living in this space")
    search.add_argument("--collection", metavar="ID", help="only items filed in this collection")
    search.add_argument(
        "--subtree",
        action="store_true",
        help="with --collection: include every collection nested below it",
    )
    search.set_defaults(func=cmd_search)

    browse = sub.add_parser(
        "browse", help="interactive search: browse hits, open them in shelf or locally"
    )
    browse.add_argument("query", nargs="*", help="start with this search")
    browse.set_defaults(func=cmd_browse)

    open_cmd = sub.add_parser("open", help="open an attachment at a page")
    open_cmd.add_argument("attachment_id")
    open_cmd.add_argument("--page", type=int, default=1)
    open_cmd.add_argument("--find", help="search term to highlight, where the viewer can")
    open_cmd.add_argument(
        "--web", action="store_true", help="open in the shelf reader instead of locally"
    )
    open_cmd.add_argument("--filename", help="name for the cached copy")
    open_cmd.add_argument(
        "--refresh", action="store_true", help="download again even if cached"
    )
    open_cmd.add_argument("--version", default="latest", help=_VERSION_HELP)
    open_cmd.set_defaults(func=cmd_open)

    download = sub.add_parser("download", help="save an attachment, OCR'd copy by default")
    download.add_argument("attachment_id")
    download.add_argument(
        "-o", "--output", help="file or directory to save to (default: its name, here)"
    )
    download.add_argument("--version", default="latest", help=_VERSION_HELP)
    download.set_defaults(func=cmd_download)

    versions = sub.add_parser("versions", help="list an attachment's OCR'd and outlined copies")
    versions.add_argument("attachment_id")
    versions.set_defaults(func=cmd_versions)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.func(args)
        return int(result or 0)
    except (ConfigError, ProfileError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except ApiError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
