"""Thin typed wrapper over a shelf instance's `/api/v1` surface.

Deliberately thin: it maps one method to one endpoint and does no
caching, no retry and no local state. Anything cleverer belongs in
`profiles.py`, where it can be tested without a server.

Errors carry the server's own message. A 4xx from shelf almost always
explains itself ("This token can't write to any space — check its space
allow-list"), and swallowing that in favour of a status code would make
the CLI worse than curl.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import httpx


class ApiError(RuntimeError):
    def __init__(self, status_code: int, detail: str, method: str, path: str) -> None:
        super().__init__(f"{method} {path} -> {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class ShelfClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 60.0) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            follow_redirects=True,
        )

    def __enter__(self) -> ShelfClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # ── plumbing ─────────────────────────────────────────────────────────

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise ApiError(
                response.status_code, _detail(response), method, path
            )
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    # ── items ────────────────────────────────────────────────────────────

    def create_item(
        self,
        *,
        item_type: str,
        data: dict[str, Any],
        space: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"item_type": item_type, "data": data}
        if space:
            payload["space_slug"] = space
        return self._request("POST", "/api/v1/items", json=payload)

    def get_item(self, item_id: str | uuid.UUID) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/items/{item_id}")

    def update_item(
        self,
        item_id: str | uuid.UUID,
        *,
        data: dict[str, Any] | None = None,
        item_type: str | None = None,
        merge: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"merge": merge}
        if data is not None:
            payload["data"] = data
        if item_type is not None:
            payload["item_type"] = item_type
        return self._request("PATCH", f"/api/v1/items/{item_id}", json=payload)

    # ── standards ────────────────────────────────────────────────────────

    def resolve_standard(
        self, *, body: str, designation: str, label: str, space: str | None = None
    ) -> list[dict[str, Any]]:
        params = {"body": body, "designation": designation, "label": label}
        if space:
            params["space"] = space
        found = self._request("GET", "/api/v1/standards/resolve", params=params)
        return list(found.get("items", []))

    def set_revision(
        self, item_id: str | uuid.UUID, revision: dict[str, Any]
    ) -> dict[str, Any]:
        return self._request(
            "PUT", f"/api/v1/items/{item_id}/revision", json=revision
        )

    def get_revisions(self, item_id: str | uuid.UUID) -> dict[str, Any]:
        """Every edition of this item's standard.

        Raises ApiError(404) when the item isn't filed under a standard,
        which callers treat as a normal state rather than a failure.
        """
        return self._request("GET", f"/api/v1/items/{item_id}/revisions")

    def spaces(self) -> list[dict[str, Any]]:
        """Spaces this token can reach, each flagged writable or not."""
        return list(self._request("GET", "/api/v1/spaces"))

    def resolve_attachment(
        self, *, sha256: str, space: str | None = None
    ) -> list[dict[str, Any]]:
        """Items already carrying a file with these bytes. Content
        identity, for documents with no business key to match on."""
        params = {"sha256": sha256}
        if space:
            params["space"] = space
        found = self._request("GET", "/api/v1/attachments/resolve", params=params)
        return list(found.get("attachments", []))

    # ── attachments ──────────────────────────────────────────────────────

    def list_attachments(self, item_id: str | uuid.UUID) -> list[dict[str, Any]]:
        return list(self._request("GET", f"/api/v1/items/{item_id}/attachments"))

    def upload(
        self, item_id: str | uuid.UUID, path: Path, *, content_type: str | None = None
    ) -> dict[str, Any]:
        """Push a file through the API rather than presigning it.

        The proxied route is the right one for a CLI: it's a single
        request, it works from anywhere the API is reachable, and the
        files this moves are documents rather than the multi-gigabyte
        blobs presigning exists for.
        """
        with path.open("rb") as fh:
            return self._request(
                "POST",
                "/api/v1/upload",
                files={"file": (path.name, fh, content_type or _guess_type(path))},
                data={"item_id": str(item_id)},
            )

    # ── search ───────────────────────────────────────────────────────────

    def search(
        self,
        q: str | None = None,
        limit: int = 50,
        *,
        offset: int = 0,
        scope: list[str] | None = None,
        hits: int = 0,
        space: str | None = None,
        collection: str | None = None,
        subtree: bool = False,
    ) -> list[dict[str, Any]]:
        return list(
            self._request(
                "GET",
                "/api/v1/search",
                params=_search_params(q, limit, offset, scope, hits, space, collection, subtree),
            )
        )

    def item_hits(self, item_id: str | uuid.UUID, q: str) -> list[dict[str, Any]]:
        """Every PDF page of one item that contains `q`."""
        return list(
            self._request("GET", f"/api/v1/items/{item_id}/hits", params={"q": q})
        )

    def collections(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/api/v1/collections"))

    # ── download ─────────────────────────────────────────────────────────

    def download(self, attachment_id: str | uuid.UUID, dest: Path) -> Path:
        """Write an attachment's bytes to `dest`, via a `.part` file so an
        interrupted download never looks like a finished one.

        The API answers with a redirect to a presigned bucket URL, which
        is followed by hand rather than by the client: the presigned URL
        carries its own signature, and a bucket that happens to sit on the
        API's host would be sent our bearer token too and refuse the
        request for carrying two kinds of auth.
        """
        path = f"/api/v1/download/{attachment_id}"
        response = self._client.get(path, follow_redirects=False)
        if response.status_code >= 400:
            raise ApiError(response.status_code, _detail(response), "GET", path)
        url = response.headers.get("location") if response.is_redirect else None
        if not url:
            raise ApiError(response.status_code, "no download location", "GET", path)

        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        with httpx.stream("GET", url, timeout=self._client.timeout, follow_redirects=True) as r:
            if r.status_code >= 400:
                raise ApiError(r.status_code, r.reason_phrase, "GET", "<presigned>")
            with part.open("wb") as fh:
                for chunk in r.iter_bytes(1 << 16):
                    fh.write(chunk)
        part.replace(dest)
        return dest


class AsyncShelfClient:
    """The read-only slice of the API the terminal browser needs, async.

    Async so a search superseded by the next keystroke is *cancelled* —
    its connection freed for the one that replaced it — rather than left
    to finish in a thread and be thrown away. One instance per session,
    so every request after the first reuses a warm keep-alive connection.
    """

    def __init__(self, base_url: str, token: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise ApiError(response.status_code, _detail(response), method, path)
        return response.json() if response.content else None

    async def search(
        self,
        q: str | None = None,
        limit: int = 50,
        *,
        offset: int = 0,
        scope: list[str] | None = None,
        hits: int = 0,
        space: str | None = None,
        collection: str | None = None,
        subtree: bool = False,
    ) -> list[dict[str, Any]]:
        return list(
            await self._request(
                "GET",
                "/api/v1/search",
                params=_search_params(q, limit, offset, scope, hits, space, collection, subtree),
            )
        )

    async def item_hits(self, item_id: str, q: str) -> list[dict[str, Any]]:
        return list(
            await self._request("GET", f"/api/v1/items/{item_id}/hits", params={"q": q})
        )

    async def list_attachments(self, item_id: str) -> list[dict[str, Any]]:
        return list(await self._request("GET", f"/api/v1/items/{item_id}/attachments"))

    async def spaces(self) -> list[dict[str, Any]]:
        return list(await self._request("GET", "/api/v1/spaces"))

    async def collections(self) -> list[dict[str, Any]]:
        return list(await self._request("GET", "/api/v1/collections"))


def _search_params(
    q: str | None,
    limit: int,
    offset: int,
    scope: list[str] | None,
    hits: int,
    space: str | None = None,
    collection: str | None = None,
    subtree: bool = False,
) -> list[tuple[str, str | int]]:
    # A list of pairs, since `scope` repeats.
    params: list[tuple[str, str | int]] = [("limit", limit)]
    if offset:
        params.append(("offset", offset))
    if q:
        params.append(("q", q))
    params.extend(("scope", s) for s in scope or [])
    if hits:
        params.append(("hits", hits))
    if space:
        params.append(("space", space))
    if collection:
        params.append(("collection", collection))
        if subtree:
            params.append(("collection_scope", "subtree"))
    return params


def _detail(response: httpx.Response) -> str:
    """FastAPI puts the message in `detail`; fall back to the raw body."""
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip()[:400] or response.reason_phrase
    if isinstance(payload, dict) and "detail" in payload:
        return str(payload["detail"])
    return str(payload)[:400]


def _guess_type(path: Path) -> str:
    import mimetypes

    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"
