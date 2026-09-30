"""Fail when a `shelf-cli` install command names a tag other than the release.

The README and cli/README.md tell people to

    pixi global install shelf-cli --git ... --subdirectory cli --tag vX.Y.Z

and that tag has to be the one the client they get belongs to. The release
rewrites it by running this script with --write (see [release].build_command
in deputy.toml), after semantic-release has bumped cli/pyproject.toml and
before it commits. A mismatch on a PR therefore means a hand edit, or a new
snippet this script can't find — both things to catch before the tag.

    python scripts/check_cli_version.py                 # check against cli/pyproject.toml
    python scripts/check_cli_version.py --expect 0.12.0 # check against a given version
    python scripts/check_cli_version.py --write         # rewrite to cli/pyproject.toml
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Every file that carries an install command. Keep in step with
# [release].assets in deputy.toml, or a rewrite is left out of the commit.
DOCS = ["README.md", "cli/README.md"]

TAG = re.compile(r"--tag[ =]v?(?P<version>\d+\.\d+\.\d+[\w.+-]*)")


def current_version() -> str:
    with (ROOT / "cli" / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["project"]["version"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--expect",
        help="version the install commands must name (default: cli/pyproject.toml)",
    )
    parser.add_argument(
        "--write", action="store_true", help="rewrite the tags instead of checking them"
    )
    args = parser.parse_args()
    expected = (args.expect or current_version()).removeprefix("v")

    problems: list[str] = []
    for rel in DOCS:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        changed = False
        # An install command wraps, so look for --tag within a few lines of
        # `pixi global install shelf-cli` rather than on the same line.
        starts = [
            i for i, line in enumerate(lines) if "pixi global install shelf-cli" in line
        ]
        if not starts:
            problems.append(f"{rel}: no `pixi global install shelf-cli` command found")
        for start in starts:
            for n in range(start, min(start + 4, len(lines))):
                m = TAG.search(lines[n])
                if m:
                    if m["version"] == expected:
                        pass
                    elif args.write:
                        lines[n] = (
                            lines[n][: m.start("version")]
                            + expected
                            + lines[n][m.end("version") :]
                        )
                        changed = True
                    else:
                        problems.append(
                            f"{rel}:{n + 1}: installs v{m['version']}, expected v{expected}"
                        )
                    break
            else:
                problems.append(
                    f"{rel}:{start + 1}: install command has no `--tag vX.Y.Z`"
                )
        if changed:
            # newline="" so a CRLF checkout is written back as it was read.
            with path.open("w", encoding="utf-8", newline="") as f:
                f.write("".join(lines))
            print(f"{rel}: install command now names v{expected}")

    if problems:
        print(
            "shelf-cli install commands are out of step with the release:",
            file=sys.stderr,
        )
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    if not args.write:
        print(f"shelf-cli install commands all name v{expected}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
