"""Turning a hit into something that opens at the right page.

No browser is launched: these pin the URLs and command lines, which is
where a page number or a path with a space in it gets lost.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from shelf_cli import opener


def test_reader_url_carries_page_and_find() -> None:
    url = opener.reader_url("https://shelf.example.com/", "abc", 12, "load case")
    assert url == "https://shelf.example.com/reader/abc?page=12&find=load%20case"


def test_pdf_url_uses_the_page_fragment(tmp_path: Path) -> None:
    path = tmp_path / "my spec.pdf"
    url = opener.pdf_url(path, 7, "a&b")
    assert url.startswith("file:///")
    assert "my%20spec.pdf#page=7&search=a%26b" in url


def test_cached_path_keeps_the_name_but_not_its_hazards(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SHELF_CACHE_DIR", str(tmp_path))
    path = opener.cached_path("att-1", '../evil"; rm -rf ~.pdf')
    assert path.parent == tmp_path / "att-1" / "original"
    assert '"' not in path.name and ";" not in path.name
    assert path.name.endswith(".pdf")


def test_cached_path_adds_the_extension(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SHELF_CACHE_DIR", str(tmp_path))
    assert opener.cached_path("a", "scan").name == "scan.pdf"


def test_fill_windows_command_line() -> None:
    edge = r'"C:\Program Files\Edge\msedge.exe" --single-argument %1'
    assert opener._fill(edge, "file:///x.pdf#page=3") == (
        r'"C:\Program Files\Edge\msedge.exe" --single-argument file:///x.pdf#page=3'
    )


def test_fill_desktop_exec_field_codes() -> None:
    cmd = ["/usr/lib/firefox/firefox", "--name", "firefox", "%u", "%i"]
    assert opener._fill(cmd, "U") == ["/usr/lib/firefox/firefox", "--name", "firefox", "U"]


def test_fill_appends_when_there_is_no_placeholder() -> None:
    assert opener._fill(["chromium"], "U") == ["chromium", "U"]


@pytest.mark.skipif(sys.platform == "win32", reason="posix splitting")
def test_viewer_template_keeps_a_spaced_path_whole() -> None:
    cmd = opener._viewer_command("okular -p {page} {path}", Path("/tmp/my spec.pdf"), 4)
    assert cmd == ["okular", "-p", "4", "/tmp/my spec.pdf"]


@pytest.mark.skipif(sys.platform != "win32", reason="windows command line")
def test_viewer_template_is_filled_as_written_on_windows() -> None:
    cmd = opener._viewer_command('SumatraPDF.exe -page {page} "{path}"', Path(r"C:\a b.pdf"), 4)
    assert cmd == r'SumatraPDF.exe -page 4 "C:\a b.pdf"'


class _Client:
    """Stands in for ShelfClient: `latest` is whatever the test sets."""

    def __init__(self, latest: str = "original") -> None:
        self.latest = latest
        self.calls: list[tuple[Path, str]] = []

    def attachment_versions(self, attachment_id: str) -> dict[str, object]:
        return {"filename": "doc.pdf", "latest": self.latest, "versions": []}

    def download(self, attachment_id: str, dest: Path, *, version: str = "original") -> Path:
        self.calls.append((dest, version))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"%PDF")
        return dest


def test_fetch_downloads_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SHELF_CACHE_DIR", str(tmp_path))
    client = _Client()

    first = opener.fetch(client, "a1", "doc.pdf")  # type: ignore[arg-type]
    second = opener.fetch(client, "a1", "doc.pdf")  # type: ignore[arg-type]
    assert first == second and len(client.calls) == 1
    opener.fetch(client, "a1", "doc.pdf", refresh=True)  # type: ignore[arg-type]
    assert len(client.calls) == 2


def test_fetch_takes_the_ocrd_copy_and_notices_a_newer_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SHELF_CACHE_DIR", str(tmp_path))
    client = _Client(latest="ocr-1")

    path = opener.fetch(client, "a1", "doc.pdf")  # type: ignore[arg-type]
    assert client.calls == [(path, "ocr-1")]
    assert path.parent == tmp_path / "a1" / "ocr-1"

    # An outline pass lands after the first open: fetched, not shadowed.
    client.latest = "outline-2"
    newer = opener.fetch(client, "a1", "doc.pdf")  # type: ignore[arg-type]
    assert newer != path
    assert client.calls[-1] == (newer, "outline-2")


def test_fetch_can_ask_for_the_original(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SHELF_CACHE_DIR", str(tmp_path))
    client = _Client(latest="ocr-1")
    path = opener.fetch(client, "a1", "doc.pdf", version="original")  # type: ignore[arg-type]
    assert client.calls == [(path, "original")]


def test_open_local_prefers_the_configured_viewer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spawned: list[object] = []
    monkeypatch.setattr(opener, "_spawn", spawned.append)
    monkeypatch.setattr(opener, "default_browser_command", lambda: ["firefox", "%u"])
    result = opener.open_local(tmp_path / "d.pdf", 3, viewer="zathura -P {page} {path}")
    assert result.at_page and "zathura" in str(spawned[0])


def test_open_local_falls_back_to_the_browser(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spawned: list[object] = []
    monkeypatch.setattr(opener, "_spawn", spawned.append)
    monkeypatch.setattr(opener, "default_browser_command", lambda: ["firefox", "%u"])
    result = opener.open_local(tmp_path / "d.pdf", 3)
    assert result.at_page
    cmd = spawned[0]
    assert isinstance(cmd, list) and cmd[0] == "firefox" and cmd[1].endswith("#page=3")
