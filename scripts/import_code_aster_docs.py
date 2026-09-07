#!/usr/bin/env python3
"""Idempotent importer: Code_Aster English U/R/D PDFs → Shelf collection.

Scrapes the Code_Aster documentation index at code-aster.org and
files every manual under one of three flat sub-collections:

    Simulation / FEM / Code Aster /
        User Manuals (U)
        Reference (R)
        Description (D)

The PDF's "[U4.41.01] …" code is preserved in the item title so the
sub-prefix structure is still legible without folder nesting.

Reads SHELF_API_TOKEN from the file at $SHELF_ENV_FILE (defaults to
the repo's .env). The token needs `upload` + `search` scopes; if it's
collection-scoped, the scope must include `Simulation / FEM /
Code Aster` (or whatever TARGET_PATH points at) and have
include_descendants=True so the script can mint sub-collections.

Re-running is safe: dedup is by item title, so already-imported docs
are skipped.
"""

import asyncio
import os
import re
import sys
import time
from pathlib import Path

import httpx

SHELF_BASE = os.environ.get("SHELF_BASE_URL", "http://localhost:8000")
DOCS_BASE = "https://code-aster.org/V2/doc/default/en"
TARGET_PATH = "Simulation / FEM / Code Aster"
CATEGORIES: list[tuple[str, str]] = [
    ("U", "User Manuals (U)"),
    ("R", "Reference (R)"),
    ("D", "Description (D)"),
]
PDF_LINK_RE = re.compile(
    r'<a href="(man_[a-z]/[a-z][0-9]+/[a-z][0-9]+\.[0-9]+\.[0-9]+\.pdf)" '
    r'class="doc"[^>]*>\[([^\]]+)\]\s*([^<]+?)</a>'
)
SUB_INDEX_RE = re.compile(r'index\.php\?man=([A-Z][0-9]+)')

# Tunables — keep concurrency modest so we don't hammer code-aster.org or
# the Shelf API (each /upload triggers OCR + extraction enqueues).
MAX_CONCURRENT_DOWNLOADS = 6
MAX_CONCURRENT_UPLOADS = 4
HTTP_TIMEOUT = 60.0


def load_token() -> str:
    if (env := os.environ.get("SHELF_API_TOKEN")):
        return env
    env_file = Path(
        os.environ.get(
            "SHELF_ENV_FILE",
            str(Path(__file__).resolve().parent.parent / ".env"),
        )
    )
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            if line.startswith("SHELF_API_TOKEN="):
                return line.split("=", 1)[1].strip()
    raise SystemExit(
        "SHELF_API_TOKEN not found in env or in $SHELF_ENV_FILE"
    )


async def fetch_text(client: httpx.AsyncClient, url: str) -> str:
    r = await client.get(url, follow_redirects=True)
    r.raise_for_status()
    return r.text


async def discover_pdfs(
    client: httpx.AsyncClient, category: str
) -> list[tuple[str, str, str]]:
    """Return [(rel_url, code, title), ...] for one category by walking
    the per-prefix sub-pages (the top-level index has paginated/empty
    placeholders, but each U1/U2/… page lists its full PDF set)."""
    top = await fetch_text(client, f"{DOCS_BASE}/index.php?man={category}")
    subs = sorted(
        {m for m in SUB_INDEX_RE.findall(top) if m.startswith(category) and m != category}
    )
    out: list[tuple[str, str, str]] = []
    for sub in subs:
        page = await fetch_text(client, f"{DOCS_BASE}/index.php?man={sub}")
        for rel, code, title in PDF_LINK_RE.findall(page):
            out.append((rel, code.strip(), title.strip()))
    return out


async def shelf_get(
    client: httpx.AsyncClient, token: str, path: str, **params: object
) -> object:
    r = await client.get(
        f"{SHELF_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params or None,
    )
    r.raise_for_status()
    return r.json()


async def shelf_post_json(
    client: httpx.AsyncClient, token: str, path: str, body: dict
) -> dict:
    r = await client.post(
        f"{SHELF_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    )
    r.raise_for_status()
    return r.json()


async def find_or_create_collection(
    client: httpx.AsyncClient,
    token: str,
    by_path: dict[str, dict],
    parent: dict,
    name: str,
) -> dict:
    full_path = f"{by_path_lookup_path(by_path, parent)} / {name}"
    if full_path in by_path:
        return by_path[full_path]
    coll = await shelf_post_json(
        client,
        token,
        "/api/v1/collections",
        {"name": name, "parent_id": parent["id"]},
    )
    by_path[coll["path"]] = coll
    return coll


def by_path_lookup_path(by_path: dict[str, dict], coll: dict) -> str:
    for path, c in by_path.items():
        if c["id"] == coll["id"]:
            return path
    raise KeyError(coll["id"])


async def existing_filenames_in_collection(
    client: httpx.AsyncClient, token: str, collection_id: str
) -> set[str]:
    """Pull every item in the collection (paged) and gather their PDF
    attachment filenames so we can dedup before uploading."""
    seen: set[str] = set()
    offset = 0
    while True:
        rows = await shelf_get(
            client,
            token,
            "/api/v1/search",
            collection=collection_id,
            limit=200,
            offset=offset,
        )
        if not rows:
            break
        for item in rows:
            # /api/v1/search doesn't return attachments — fall back to
            # title since the importer always sets title=PDF basename.
            title = (item.get("data") or {}).get("title")
            if title:
                seen.add(str(title))
        if len(rows) < 200:
            break
        offset += len(rows)
    return seen


async def head_ok(client: httpx.AsyncClient, url: str) -> bool:
    try:
        r = await client.head(url, follow_redirects=True)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


async def download_pdf(
    client: httpx.AsyncClient, url: str
) -> bytes | None:
    try:
        r = await client.get(url, follow_redirects=True)
        if r.status_code != 200:
            return None
        if not r.content.startswith(b"%PDF"):
            return None
        return r.content
    except httpx.HTTPError:
        return None


async def upload_one(
    upload_client: httpx.AsyncClient,
    token: str,
    *,
    title: str,
    filename: str,
    body: bytes,
    collection_id: str,
) -> bool:
    files = {"file": (filename, body, "application/pdf")}
    data = {"title": title, "collection_id": collection_id}
    try:
        r = await upload_client.post(
            f"{SHELF_BASE}/api/v1/upload",
            headers={"Authorization": f"Bearer {token}"},
            files=files,
            data=data,
        )
    except httpx.HTTPError as e:
        print(f"  ! upload error {filename}: {e}", flush=True)
        return False
    if r.status_code != 201:
        print(f"  ! upload {filename} → HTTP {r.status_code}: {r.text[:200]}", flush=True)
        return False
    return True


async def process_pdf(
    sem_dl: asyncio.Semaphore,
    sem_up: asyncio.Semaphore,
    download_client: httpx.AsyncClient,
    upload_client: httpx.AsyncClient,
    token: str,
    *,
    rel: str,
    code: str,
    title: str,
    collection_id: str,
    skip_titles: set[str],
    counters: dict[str, int],
) -> None:
    full_title = f"[{code}] {title}"
    if full_title in skip_titles:
        counters["skipped"] += 1
        return
    pdf_url = f"{DOCS_BASE}/{rel}"
    async with sem_dl:
        body = await download_pdf(download_client, pdf_url)
    if body is None:
        counters["missing"] += 1
        return
    filename = Path(rel).name
    async with sem_up:
        ok = await upload_one(
            upload_client,
            token,
            title=full_title,
            filename=filename,
            body=body,
            collection_id=collection_id,
        )
    if ok:
        counters["uploaded"] += 1
        skip_titles.add(full_title)
        if counters["uploaded"] % 25 == 0:
            print(f"  … {counters['uploaded']} uploaded so far", flush=True)
    else:
        counters["failed"] += 1


async def main() -> int:
    token = load_token()
    started = time.time()

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as scrape_client:
        print("[1/4] Discovering PDFs on code-aster.org…", flush=True)
        per_cat: dict[str, list[tuple[str, str, str]]] = {}
        for cat, _label in CATEGORIES:
            per_cat[cat] = await discover_pdfs(scrape_client, cat)
            print(f"  {cat}: {len(per_cat[cat])} PDFs", flush=True)

        print("[2/4] Resolving Shelf collection tree…", flush=True)
        rows = await shelf_get(scrape_client, token, "/api/v1/collections")
        by_path = {c["path"]: c for c in rows}
        if TARGET_PATH not in by_path:
            print(f"FATAL: collection '{TARGET_PATH}' not found. Available paths:")
            for p in sorted(by_path):
                if "Code Aster" in p or "FEM" in p:
                    print(f"  - {p}")
            return 2
        root = by_path[TARGET_PATH]
        print(f"  → {TARGET_PATH} = {root['id']}", flush=True)

        # One collection per category — flat structure inside Code Aster.
        targets: dict[str, str] = {}  # cat → coll id
        for cat, label in CATEGORIES:
            cat_coll = await find_or_create_collection(
                scrape_client, token, by_path, root, label
            )
            targets[cat] = cat_coll["id"]

        print("[3/4] Loading existing item titles for dedup…", flush=True)
        skip_titles_per_coll: dict[str, set[str]] = {}
        for cat, cid in targets.items():
            skip_titles_per_coll[cid] = await existing_filenames_in_collection(
                scrape_client, token, cid
            )

        print("[4/4] Downloading + uploading…", flush=True)
        sem_dl = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
        sem_up = asyncio.Semaphore(MAX_CONCURRENT_UPLOADS)
        counters = {"uploaded": 0, "skipped": 0, "missing": 0, "failed": 0}

        async with (
            httpx.AsyncClient(timeout=HTTP_TIMEOUT) as dl_client,
            httpx.AsyncClient(timeout=HTTP_TIMEOUT) as up_client,
        ):
            tasks: list[asyncio.Task] = []
            for cat, _ in CATEGORIES:
                cid = targets[cat]
                skip = skip_titles_per_coll[cid]
                for rel, code, title in per_cat[cat]:
                    tasks.append(
                        asyncio.create_task(
                            process_pdf(
                                sem_dl,
                                sem_up,
                                dl_client,
                                up_client,
                                token,
                                rel=rel,
                                code=code,
                                title=title,
                                collection_id=cid,
                                skip_titles=skip,
                                counters=counters,
                            )
                        )
                    )
            print(f"  scheduled {len(tasks)} PDFs", flush=True)
            await asyncio.gather(*tasks)

    elapsed = int(time.time() - started)
    print(
        f"\nDone in {elapsed}s — uploaded={counters['uploaded']} "
        f"skipped={counters['skipped']} missing={counters['missing']} "
        f"failed={counters['failed']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
