"""The page size a viewer will lay a page out at.

These dimensions drive the reader's scroll geometry: the client sizes
every page box from them before it has opened the PDF. Report a page
portrait that renders landscape and every page below it sits at the
wrong offset, so the scrollbar stops matching the document.
"""

from shelf.worker.extract import _display_size


class _Box:
    def __init__(self, width: float, height: float) -> None:
        self.width = width
        self.height = height


class _Page:
    """The parts of a pypdf page `_display_size` looks at."""

    def __init__(
        self,
        mediabox: _Box,
        rotation: int = 0,
        cropbox: _Box | None = None,
    ) -> None:
        self.mediabox = mediabox
        self.rotation = rotation
        self.cropbox = cropbox


A4 = _Box(595.0, 842.0)


def test_unrotated_page_is_its_own_size() -> None:
    assert _display_size(_Page(A4)) == (595.0, 842.0)


def test_quarter_turns_swap_the_axes() -> None:
    """A landscape sheet is routinely stored portrait plus /Rotate 90."""
    assert _display_size(_Page(A4, rotation=90)) == (842.0, 595.0)
    assert _display_size(_Page(A4, rotation=270)) == (842.0, 595.0)


def test_half_turn_does_not() -> None:
    assert _display_size(_Page(A4, rotation=180)) == (595.0, 842.0)


def test_rotation_is_taken_modulo_a_full_turn() -> None:
    # /Rotate is not required to be in [0, 360).
    assert _display_size(_Page(A4, rotation=450)) == (842.0, 595.0)
    assert _display_size(_Page(A4, rotation=-90)) == (842.0, 595.0)


def test_cropbox_wins_over_a_larger_mediabox() -> None:
    """What gets displayed is the crop, which can be much smaller."""
    page = _Page(_Box(1000.0, 1000.0), cropbox=_Box(595.0, 842.0))
    assert _display_size(page) == (595.0, 842.0)


def test_cropbox_is_rotated_too() -> None:
    page = _Page(_Box(1000.0, 1000.0), rotation=90, cropbox=_Box(595.0, 842.0))
    assert _display_size(page) == (842.0, 595.0)


def test_falls_back_to_the_mediabox_without_a_cropbox() -> None:
    page = _Page(A4, cropbox=None)
    assert _display_size(page) == (595.0, 842.0)
