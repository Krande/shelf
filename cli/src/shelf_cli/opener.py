"""Opening a search hit: in the shelf reader, or as a local PDF at its page.

**The web reader** is one URL — `/reader/<attachment>?page=N&find=<q>` —
and any browser opens it, so there is nothing to choose.

**A local PDF at a page** has no OS-level answer. `os.startfile` and
`xdg-open` hand a file to its default app and take no page, and every
dedicated viewer spells "go to page" its own way (`-page N`, `-p N`,
`/A page=N`, …). The one convention several viewers share is RFC 8118's
`#page=N` fragment on a URL, which Chrome, Edge and Firefox all honour on a
`file://` URL. So the default is: download once into a cache, then launch
the *default browser* — found by name, because asking the OS to open a
`file://` URL routes it by the `.pdf` association and drops the fragment
— at `file:///…/doc.pdf#page=N`.

For people who'd rather use a dedicated viewer, `[viewer] pdf` in
shelf.toml (or `SHELF_PDF_VIEWER`) is a command template with `{path}` and
`{page}` in it, so no viewer needs supporting here:

    SumatraPDF.exe -page {page} "{path}"    # Windows: quote {path} yourself
    okular -p {page} {path}                  # elsewhere each word is an argument
    evince -i {page} {path}
    zathura -P {page} {path}
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from .client import ShelfClient


def reader_url(base_url: str, attachment_id: str, page: int, query: str | None) -> str:
    url = f"{base_url.rstrip('/')}/reader/{quote(str(attachment_id))}?page={page}"
    if query:
        url += f"&find={quote(query)}"
    return url


def open_web(base_url: str, attachment_id: str, page: int, query: str | None) -> str:
    url = reader_url(base_url, attachment_id, page, query)
    webbrowser.open(url)
    return url


# ── cache ────────────────────────────────────────────────────────────────


def cache_dir() -> Path:
    """Per-user cache: `%LOCALAPPDATA%\\shelf\\pdf` on Windows,
    `$XDG_CACHE_HOME/shelf/pdf` (or `~/.cache/shelf/pdf`) elsewhere,
    `~/Library/Caches/shelf/pdf` on macOS. `SHELF_CACHE_DIR` overrides."""
    if env := os.environ.get("SHELF_CACHE_DIR"):
        return Path(env)
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Caches"
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return root / "shelf" / "pdf"


_UNSAFE = re.compile(r"[^\w.\- ()\[\]+,]")


def cached_path(attachment_id: str, filename: str) -> Path:
    """`<cache>/<attachment id>/<filename>`: the id keeps two files
    called `spec.pdf` apart, and the real name is what the browser tab
    and the viewer's title bar show. The name is reduced to a plain set
    of characters, since it ends up on a command line."""
    name = _UNSAFE.sub("_", Path(filename).name).strip(" .") or "document.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return cache_dir() / str(attachment_id) / name


def fetch(client: ShelfClient, attachment_id: str, filename: str, *, refresh: bool = False) -> Path:
    """The local copy of an attachment, downloading it the first time.

    Keyed by attachment id: shelf rewrites a blob in place when it OCRs
    it, but never renumbers pages doing so, so a stale copy still lands
    on the right page. `refresh` is there for when it matters.
    """
    path = cached_path(attachment_id, filename)
    if refresh or not path.exists():
        client.download(attachment_id, path)
    return path


# ── launching ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Opened:
    how: str
    # False when the file opened but not at the page — nothing on this
    # machine was found that takes one — so the caller can say which page.
    at_page: bool = True


def pdf_url(path: Path, page: int, query: str | None = None) -> str:
    url = f"{path.resolve().as_uri()}#page={page}"
    if query:
        # pdf.js (Firefox) and Acrobat highlight it; Chrome and Edge
        # ignore what they don't understand.
        url += f"&search={quote(query)}"
    return url


def open_local(
    path: Path, page: int, *, viewer: str | None = None, query: str | None = None
) -> Opened:
    if viewer:
        _spawn(_viewer_command(viewer, path, page))
        return Opened(f"viewer: {viewer.split()[0]}")

    url = pdf_url(path, page, query)
    browser = default_browser_command()
    if browser is not None:
        _spawn(_fill(browser, url))
        return Opened("default browser")

    # Nothing takes a page: open the file anyway, and say where to go.
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        _spawn(["open", str(path)])
    else:
        _spawn(["xdg-open", str(path)])
    return Opened("default PDF app", at_page=False)


def _viewer_command(template: str, path: Path, page: int) -> str | list[str]:
    fields = {"path": str(path), "page": str(page)}
    if sys.platform == "win32":
        # A Windows command line is one string that the program splits
        # itself, so the template is filled in as written — quotes and all.
        return template.format(**fields)
    # Elsewhere, split first and fill after, so a path with spaces stays
    # one argument without the template having to quote it.
    return [part.format(**fields) for part in shlex.split(template)]


def _fill(command: str | list[str], url: str) -> str | list[str]:
    """Put `url` where a browser's registered command wants it: `%1` on
    Windows, a `%u`-style field code in a .desktop Exec line, or on the
    end when there's no placeholder."""
    if isinstance(command, str):
        return command.replace("%1", url) if "%1" in command else f'{command} "{url}"'
    out: list[str] = []
    placed = False
    for part in command:
        if part in ("%u", "%U", "%f", "%F"):
            out.append(url)
            placed = True
        elif re.fullmatch(r"%[a-zA-Z]", part):
            continue  # %i, %c, %k: icon, name, desktop file — not needed
        else:
            out.append(part)
    return out if placed else [*out, url]


def _spawn(command: str | list[str]) -> None:
    """Start and forget, with no console attached to ours — a browser
    that writes to stderr must not scribble over the terminal UI."""
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)  # type: ignore[call-overload]


# ── finding the default browser ─────────────────────────────────────────


def default_browser_command() -> str | list[str] | None:
    """The default browser's launch command, or None when there isn't
    one this can find. A string on Windows (a registry command line), a
    list elsewhere."""
    if sys.platform == "win32":
        return _windows_browser()
    if sys.platform == "darwin":
        # `open` hands a file:// URL to the .pdf app, not the browser,
        # and asking LaunchServices for the https handler takes pyobjc.
        # Preview doesn't take a page, so say so rather than pretend.
        return None
    return _linux_browser()


def _windows_browser() -> str | None:
    import winreg

    base = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations"
    # `UserChoiceLatest` is where Windows 11 24H2 onward keeps the choice;
    # `UserChoice` is where everything earlier does.
    for scheme in ("https", "http"):
        for leaf in ("UserChoiceLatest", "UserChoice"):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{base}\{scheme}\{leaf}") as k:
                    prog_id = winreg.QueryValueEx(k, "ProgId")[0]
                with winreg.OpenKey(
                    winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\shell\open\command"
                ) as k:
                    command = str(winreg.QueryValueEx(k, "")[0])
            except OSError:
                continue
            if command:
                return command
    return None


_KNOWN_BROWSERS = (
    "firefox",
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "microsoft-edge-stable",
    "brave-browser",
)


def _linux_browser() -> list[str] | None:
    desktop = _xdg_default_browser()
    if desktop is not None:
        exec_line = _desktop_exec(desktop)
        if exec_line:
            return shlex.split(exec_line)
    if env := os.environ.get("BROWSER"):
        first = env.split(os.pathsep)[0]
        return shlex.split(first)
    for name in _KNOWN_BROWSERS:
        if found := shutil.which(name):
            return [found]
    return None


def _xdg_default_browser() -> str | None:
    if not shutil.which("xdg-settings"):
        return None
    try:
        out = subprocess.run(
            ["xdg-settings", "get", "default-web-browser"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out or None


def _desktop_exec(desktop_id: str) -> str | None:
    """The `Exec=` of the `[Desktop Entry]` group of a .desktop file,
    looked up across the XDG data dirs the way a launcher would."""
    dirs = [os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")]
    dirs += (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    dirs += [
        "/var/lib/flatpak/exports/share",
        str(Path.home() / ".local/share/flatpak/exports/share"),
    ]
    for d in dirs:
        path = Path(d) / "applications" / desktop_id
        if not path.is_file():
            continue
        in_entry = False
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("["):
                in_entry = line == "[Desktop Entry]"
            elif in_entry and line.startswith("Exec="):
                return line.removeprefix("Exec=")
    return None
