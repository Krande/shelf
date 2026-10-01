"""Write a ZIP archive as a stream of byte chunks.

The stdlib ``zipfile`` writes to anything with a ``write`` method, and
when that target can't seek it switches to data descriptors: each
entry's CRC and sizes follow its bytes instead of being patched into the
header afterwards. That is exactly what streaming needs -- an entry can
go out before its CRC is known, and nothing already sent is revisited.

Entries are stored, not deflated. Nearly everything this serves is PDF,
which is compressed internally already; deflate would cost a core's
worth of CPU on the event loop to save a few percent.
"""

import io
import time
import zipfile
from collections.abc import AsyncIterator


class _Sink(io.RawIOBase):
    """Collects what ``zipfile`` writes until the caller drains it.

    Deliberately unseekable (``tell`` raises), which is what makes
    ``zipfile`` emit data descriptors rather than seek back.
    """

    def __init__(self) -> None:
        self._parts: list[bytes] = []

    def writable(self) -> bool:
        return True

    def write(self, b: bytes) -> int:  # type: ignore[override]
        self._parts.append(bytes(b))
        return len(b)

    def drain(self) -> bytes:
        out = b"".join(self._parts)
        self._parts.clear()
        return out


class ZipStream:
    """An archive written incrementally; every method returns or yields
    the bytes it produced, for the caller to send on."""

    def __init__(self) -> None:
        self._sink = _Sink()
        self._zf = zipfile.ZipFile(
            self._sink, "w", compression=zipfile.ZIP_STORED
        )

    def _info(self, name: str) -> zipfile.ZipInfo:
        info = zipfile.ZipInfo(name, date_time=time.localtime()[:6])
        info.compress_type = zipfile.ZIP_STORED
        info.external_attr = 0o644 << 16
        return info

    async def add(
        self, name: str, size: int | None, chunks: AsyncIterator[bytes]
    ) -> AsyncIterator[bytes]:
        """Stream one entry from ``chunks``. ``size``, when known, lets
        ``zipfile`` pick ZIP64 headers up front for a very large entry."""
        info = self._info(name)
        if size is not None:
            info.file_size = size
        with self._zf.open(info, "w") as entry:
            async for chunk in chunks:
                entry.write(chunk)
                out = self._sink.drain()
                if out:
                    yield out
        out = self._sink.drain()
        if out:
            yield out

    def add_bytes(self, name: str, data: bytes) -> bytes:
        """A small entry built in memory (an index, a notice)."""
        self._zf.writestr(self._info(name), data)
        return self._sink.drain()

    def close(self) -> bytes:
        """The central directory; the archive is complete after this."""
        self._zf.close()
        return self._sink.drain()
