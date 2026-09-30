"""Record the `shelf` CLI as a set of user stories, for the docs.

    pixi run up                        # in one terminal
    pixi run cli-stories               # in another: every story
    pixi run cli-stories --list
    pixi run cli-stories --story browse

The companion of ui_stories.py, and it seeds the same demo library through
it. Each story runs one real `shelf` command against the local dev stack
and writes docs/screenshots/cli-<name>.svg: a terminal window showing the
command and what it printed, recorded by rich. `browse` is the terminal UI
itself, driven headless through textual's test pilot and saved with its own
screenshot support. SVG rather than PNG because it's what both of those
produce natively, it stays sharp at any size, and GitHub renders it as an
image.

The CLI authenticates with an API token like any script would, so each run
mints one for the demo user and revokes it afterwards — the token list in
Settings (and its screenshot) doesn't collect a new entry per run.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright
from rich.console import Console
from rich.syntax import Syntax
from rich.text import Text

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_stories as ui  # noqa: E402 — the seed, the demo, the paths

OUT = ui.OUT
WIDTH = 100
# Past this a story is showing the shape of the output rather than all of
# it; the rest is elided with a note saying how much was left out.
MAX_LINES = 30


@dataclass
class Ctx:
    base_url: str
    token: str
    demo: ui.Demo
    workdir: Path


@dataclass
class Story:
    name: str
    summary: str
    run: Callable[[Ctx, Path], None]


STORIES: dict[str, Story] = {}


def story(name: str, summary: str):
    def register(fn: Callable[[Ctx, Path], None]):
        STORIES[name] = Story(name, summary, fn)
        return fn

    return register


# ── Recording a command ──────────────────────────────────────────────────────


def shelf(ctx: Ctx, *args: str) -> str:
    """Run the real CLI and return what it printed."""
    env = {
        **os.environ,
        "SHELF_API_BASE_URL": ctx.base_url,
        "SHELF_API_TOKEN": ctx.token,
        "PYTHONIOENCODING": "utf-8",
    }
    proc = subprocess.run(
        [sys.executable, "-m", "shelf_cli", *args],
        cwd=ctx.workdir,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"shelf {' '.join(args)} exited {proc.returncode}: {proc.stderr}"
        )
    return proc.stdout + proc.stderr


def record(path: Path, shown: str, output: str, max_lines: int = MAX_LINES) -> None:
    """A terminal window with `$ <shown>` and the output under it."""
    console = Console(
        record=True,
        width=WIDTH,
        file=io.StringIO(),
        force_terminal=True,
        color_system="truecolor",
    )
    console.print(Text.assemble(("$ ", "bold green"), (shown, "bold")))
    lines = output.rstrip("\n").splitlines()
    elided = len(lines) - max_lines
    body = "\n".join(lines[:max_lines] if elided > 0 else lines)
    try:
        json.loads(output)
        console.print(
            Syntax(
                body,
                "json",
                theme="ansi_dark",
                background_color="default",
                word_wrap=True,
            )
        )
    except ValueError:
        console.print(Text(body))
    if elided > 0:
        console.print(Text(f"… {elided} more lines", style="dim italic"))
    console.save_svg(str(path), title="shelf")


def quote(arg: str) -> str:
    return f'"{arg}"' if " " in arg else arg


def command_story(
    name: str,
    summary: str,
    args: Callable[[Ctx], list[str]],
    max_lines: int = MAX_LINES,
):
    """A story that is one command: run it, record it."""

    def run(ctx: Ctx, path: Path) -> None:
        argv = args(ctx)
        shown = "shelf " + " ".join(map(quote, argv))
        record(path, shown, shelf(ctx, *argv), max_lines)

    story(name, summary)(run)


# ── Stories ──────────────────────────────────────────────────────────────────

command_story(
    "whoami",
    "Check a token works, and see which spaces it reaches",
    lambda c: ["whoami"],
)

command_story(
    "search",
    "Search titles, metadata and PDF text from the terminal",
    lambda c: [
        "search",
        "load case",
        "--scope",
        "fulltext",
        "--hits",
        "1",
        "--limit",
        "1",
    ],
    # Long enough to reach the page hit, which is the point of the example.
    max_lines=45,
)

command_story(
    "items-set",
    "Set metadata fields on a document without touching the rest",
    lambda c: [
        "items",
        "set",
        c.demo.standard_id,
        "--set",
        "numberOfPages=3",
        "--set",
        "language=en",
    ],
)


@story("profiles-push", "Push document profiles kept in files — dry run first")
def _profiles_push(ctx: Ctx, path: Path) -> None:
    # Two profiles: one describes an edition the instance already holds
    # (matched on the standard's own identity, so it would update), one a
    # document it has never seen (so it would be created).
    profiles = ctx.workdir / "profiles"
    profiles.mkdir(exist_ok=True)
    current = ui.STANDARDS[-1]
    (profiles / "nx-acme-1234-2020.json").write_text(
        json.dumps(
            {
                "space": ui.STANDARDS_SLUG,
                "item_type": "standard",
                "data": {
                    **ui.STANDARD_FIELDS,
                    "edition": current["edition"],
                    "issuedOn": current["issuedOn"],
                },
                "revision": {
                    "body": ui.STANDARD_FIELDS["standardBody"],
                    "designation": ui.STANDARD_FIELDS["designation"],
                    "label": current["edition"],
                    "issued_on": current["issuedOn"],
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (profiles / "nx-acme-5678-2022.json").write_text(
        json.dumps(
            {
                "space": ui.STANDARDS_SLUG,
                "item_type": "standard",
                "data": {
                    "title": "Guidance on the design of widgets — Part 3: Shells",
                    "standardBody": "NX Standards",
                    "designation": "NX-ACME 5678",
                    "edition": "2022",
                },
                "revision": {
                    "body": "NX Standards",
                    "designation": "NX-ACME 5678",
                    "label": "2022",
                    "issued_on": "2022-03-01",
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    record(
        path,
        "shelf profiles push ./profiles/ --dry-run",
        shelf(ctx, "profiles", "push", "profiles", "--dry-run"),
    )


@story("browse", "`shelf browse`: the same search as a terminal UI")
def _browse(ctx: Ctx, path: Path) -> None:
    from shelf_cli.config import Config
    from shelf_cli.tui import ShelfBrowser
    from textual.widgets import OptionList

    async def shoot() -> None:
        app = ShelfBrowser(Config(base_url=ctx.base_url, token=ctx.token), "load case")
        # Wide enough that the spaces sidebar shows (it hides under 130
        # columns) and result rows don't wrap.
        async with app.run_test(size=(160, 34)) as pilot:
            results = app.query_one("#results", OptionList)
            for _ in range(80):  # up to ~20 s for the search to come back
                await pilot.pause(0.25)
                if results.option_count > 0:
                    break
            else:
                raise RuntimeError("no results arrived in the TUI")
            # Into the results, so a row is highlighted as it would be in use.
            await pilot.press("down")
            await pilot.pause(0.5)
            app.save_screenshot(filename=path.name, path=str(path.parent))

    # A thread of its own: Playwright's sync API (still open for the token)
    # holds an event loop on this one, and textual needs to run its own.
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(asyncio.run, shoot()).result()


# ── Runner ───────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base-url",
        default="http://localhost:5173",
        help="the instance, as `pixi run up` prints it (default %(default)s)",
    )
    parser.add_argument(
        "--story",
        action="append",
        choices=sorted(STORIES),
        help="run only this story; repeatable",
    )
    parser.add_argument("--list", action="store_true", help="list the stories and exit")
    parser.add_argument("--out", type=Path, default=OUT, help="default %(default)s")
    args = parser.parse_args()

    if args.list:
        for s in STORIES.values():
            print(f"  {s.name:<20} {s.summary}")
        return 0

    selected = (
        [STORIES[n] for n in args.story] if args.story else list(STORIES.values())
    )
    args.out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        if not ui.check_stack(pw, args.base_url):
            return 1
        demo = ui.seed(pw, args.base_url)
        api = ui.session(pw, args.base_url)
        token = api.post(
            "/api/me/tokens",
            {"name": "cli-stories", "scopes": ["upload", "search", "download"]},
        )
        failed = []
        try:
            with tempfile.TemporaryDirectory() as tmp:
                ctx = Ctx(args.base_url, token["plaintext"], demo, Path(tmp))
                for s in selected:
                    path = args.out / f"cli-{s.name}.svg"
                    try:
                        s.run(ctx, path)
                        print(f"  ✓ {s.name:<20} {path.relative_to(ui.ROOT)}")
                    except Exception as exc:  # keep going; report every broken story
                        failed.append(s.name)
                        print(f"  ✗ {s.name:<20} {exc!r}", file=sys.stderr)
        finally:
            api.r.delete(f"/api/me/tokens/{token['id']}")
            api.r.dispose()

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
