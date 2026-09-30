"""Screenshot the web UI as a set of user stories, for the docs.

    pixi run up                       # in one terminal
    pixi run ui-stories               # in another: every story
    pixi run ui-stories --list
    pixi run ui-stories --story reader --story search
    pixi run ui-stories --theme light # *-light.png

Each story is a function below that opens one screen in the state a user
would see it and writes docs/screenshots/<name>.png. They run against the
local dev stack, so what they show is the SPA as it is on this checkout.

Before the stories run, `seed` puts a small demo library into that stack
through the same session endpoints the SPA uses: a demo user (an admin, since
`pixi run up` makes dev-login users admins), a shared "Standards" space with
two editions of one standard, a couple of papers with generated PDFs,
collections, tags, a highlight and a note. Every step looks before it creates,
so re-running adds nothing and the screenshots stay the same between runs.
The PDFs are drawn with reportlab at seed time, so no binary lives in the repo.

The worker has to extract the PDFs' text before search can find a passage in
them; seeding waits for that, and says so if no worker picks them up.
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import APIRequestContext, Page, Playwright, sync_playwright
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
VIEWPORT = {"width": 1440, "height": 900}

DEMO_EMAIL = "ada@demo.shelf.example"
DEMO_NAME = "Ada Lovelace"
MEMBER_EMAIL = "grace@demo.shelf.example"
MEMBER_NAME = "Grace Hopper"
# A slug of its own rather than "standards", so seeding into a dev database
# that already has a Standards space never touches it.
STANDARDS_SLUG = "demo-standards"
STANDARDS_NAME = "Standards"


# ── Demo content ─────────────────────────────────────────────────────────────

LOREM = (
    "The assessment follows the nominal stress approach. Each detail is "
    "assigned a category from the tables in Annex A, and the stress ranges "
    "from every load case are counted with the rainflow method before they "
    "are compared with the S-N curve for that category. Where the geometry "
    "is not covered by a tabulated detail, the hot spot stress is used "
    "instead, extrapolated from read-out points at 0.4t and 1.0t from the "
    "weld toe."
)

ARTICLE = {
    "title": "Fatigue assessment of welded joints under variable amplitude loading",
    "item_type": "journalArticle",
    "data": {
        "creators": [
            {"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"},
            {"creatorType": "author", "firstName": "Charles", "lastName": "Babbage"},
        ],
        "date": "2021-03-15",
        "publicationTitle": "Journal of Structural Integrity",
        "volume": "14",
        "issue": "2",
        "pages": "112-131",
        "abstractNote": (
            "A comparison of damage accumulation rules for welded steel joints "
            "under variable amplitude loading, with recommendations for the "
            "load case combinations used in offshore design."
        ),
    },
    "sections": [
        ("1 Introduction", LOREM),
        (
            "2 Variable amplitude loading",
            "Offshore structures see variable amplitude loading from waves, "
            "wind and operations. " + LOREM,
        ),
        ("3 Load case combinations", "Every load case is combined with... " + LOREM),
        ("4 Conclusions", LOREM),
    ],
    "highlight": (2, "variable amplitude loading from waves"),
    "note": (
        "<p>Section 3 is the argument for keeping the storm load case separate "
        "from operational fatigue. Worth citing in the jacket report.</p>"
    ),
    "tags": ["fatigue", "offshore"],
    "collection": "Fatigue",
}

REPORT = {
    "title": "Load case definitions for offshore jacket structures",
    "item_type": "report",
    "data": {
        "creators": [{"creatorType": "author", "name": "Structures Group"}],
        "date": "2023-09-01",
        "institution": "Demo Engineering",
        "reportNumber": "DE-REP-0042",
    },
    "sections": [
        ("1 Scope", LOREM),
        ("2 Environmental load cases", "Each load case is defined by... " + LOREM),
        ("3 Accidental load cases", LOREM),
    ],
    "tags": ["offshore"],
    "collection": "Offshore",
}

STANDARD_FIELDS = {
    "standardBody": "NX Standards",
    "designation": "NX-ACME 1234",
    "title": "Guidance on the design of widgets — Part 2: Plated widgets",
}
STANDARDS = [
    {
        "edition": "2015",
        "issuedOn": "2015-06-01",
        "sections": [("1 Scope", LOREM), ("2 Design load cases", LOREM)],
    },
    {
        "edition": "2015+A2:2020",
        "issuedOn": "2020-10-01",
        "amendments": "A1:2018, A2:2020",
        "sections": [
            ("1 Scope", LOREM),
            ("2 Design load cases", "The characteristic load case is... " + LOREM),
            ("3 Resistance of plated widgets", LOREM),
        ],
    },
]

TAG_COLORS = {"fatigue": "#e8590c", "offshore": "#1c7ed6"}


def make_pdf(
    title: str, sections: list[tuple[str, str]], find: tuple[int, str] | None = None
) -> tuple[bytes, list[list[float]]]:
    """A small A4 document: one section per page, a running title, a heading.

    Returns the bytes, plus where `find`'s phrase landed as shelf stores
    highlight rects — PDF user space, origin bottom-left, [x, y, w, h] —
    which is also reportlab's coordinate system, so no flipping.
    """
    buf = io.BytesIO()
    width, height = A4
    left, size, leading = 72, 11, 15
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    c.setTitle(title)
    rects: list[list[float]] = []
    for page_number, (heading, body) in enumerate(sections, start=1):
        c.setFont("Helvetica", 8)
        c.setFillGray(0.4)
        c.drawString(left, height - 60, title)
        c.setFillGray(0)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(left, height - 110, heading)
        c.setFont("Helvetica", size)
        y = height - 140
        for line in simpleSplit(body, "Helvetica", size, width - 2 * left):
            c.drawString(left, y, line)
            if find and find[0] == page_number and find[1] in line:
                x = left + stringWidth(line[: line.index(find[1])], "Helvetica", size)
                w = stringWidth(find[1], "Helvetica", size)
                rects.append([x, y - 0.25 * size, w, 1.2 * size])
            y -= leading
        c.showPage()
    c.save()
    if find and not rects:
        raise RuntimeError(f"{find[1]!r} doesn't fit on one line of page {find[0]}")
    return buf.getvalue(), rects


# ── Seeding ──────────────────────────────────────────────────────────────────


@dataclass
class Demo:
    personal_slug: str
    article_id: str
    article_attachment_id: str
    highlight_id: str
    report_id: str
    standard_id: str  # the current edition


class Api:
    """The SPA's session endpoints, through a browser context's cookie jar."""

    def __init__(self, request: APIRequestContext):
        self.r = request

    def _check(self, resp, what: str):
        if not resp.ok:
            raise RuntimeError(f"{what}: HTTP {resp.status} {resp.text()[:300]}")
        return resp.json() if resp.text() else None

    def get(self, path: str, **params):
        return self._check(self.r.get(path, params=params or None), f"GET {path}")

    def post(self, path: str, body=None):
        return self._check(self.r.post(path, data=body), f"POST {path}")

    def put(self, path: str, body):
        return self._check(self.r.put(path, data=body), f"PUT {path}")

    def patch(self, path: str, body):
        return self._check(self.r.patch(path, data=body), f"PATCH {path}")


def dev_login(api: Api, email: str, name: str) -> None:
    resp = api.r.post("/auth/dev-login", data={"email": email, "display_name": name})
    if resp.status == 404:
        sys.exit("Dev login is disabled on this instance (SHELF_DEV_LOGIN_ENABLED).")
    api._check(resp, "dev login")


def ensure_space(api: Api) -> None:
    spaces = api.get("/api/me/spaces")
    if not any(s["slug"] == STANDARDS_SLUG for s in spaces):
        api.post("/api/spaces", {"name": STANDARDS_NAME, "slug": STANDARDS_SLUG})


def ensure_item(api: Api, slug: str, item_type: str, data: dict) -> tuple[str, bool]:
    """(item id, created?) — matched on title and, for standards, edition."""
    listing = api.get(f"/api/spaces/{slug}/items", limit=200, revisions="all")
    for item in listing["items"]:
        d = item["data"]
        if d.get("title") == data["title"] and d.get("edition") == data.get("edition"):
            return item["id"], False
    item = api.post(f"/api/spaces/{slug}/items", {"item_type": item_type, "data": data})
    return item["id"], True


def ensure_pdf(api: Api, item_id: str, filename: str, pdf: bytes) -> str:
    for att in api.get(f"/api/items/{item_id}/attachments"):
        if att["filename"] == filename:
            return att["id"]
    reg = api.post(
        f"/api/items/{item_id}/attachments",
        {
            "filename": filename,
            "content_type": "application/pdf",
            "size_bytes": len(pdf),
        },
    )
    # Straight to the object store, as the browser does; no shelf cookie.
    put = api.r.put(
        reg["upload_url"], data=pdf, headers={"Content-Type": "application/pdf"}
    )
    if not put.ok:
        raise RuntimeError(
            f"upload to object store: HTTP {put.status} {put.text()[:300]}"
        )
    att_id = reg["attachment"]["id"]
    api.post(f"/api/attachments/{att_id}/complete")
    return att_id


def ensure_tags(api: Api, slug: str, item_id: str, names: list[str]) -> None:
    tags = {t["name"]: t["id"] for t in api.get(f"/api/spaces/{slug}/tags")}
    for name in names:
        if name not in tags:
            tag = api.post(
                f"/api/spaces/{slug}/tags",
                {"name": name, "color": TAG_COLORS.get(name)},
            )
            tags[name] = tag["id"]
    api.put(f"/api/items/{item_id}/tags", {"tag_ids": [tags[n] for n in names]})


def ensure_collections(api: Api, slug: str) -> dict[str, str]:
    """Offshore, with Fatigue nested inside it."""
    existing = {c["name"]: c["id"] for c in api.get(f"/api/spaces/{slug}/collections")}
    if "Offshore" not in existing:
        existing["Offshore"] = api.post(
            f"/api/spaces/{slug}/collections", {"name": "Offshore"}
        )["id"]
    if "Fatigue" not in existing:
        existing["Fatigue"] = api.post(
            f"/api/spaces/{slug}/collections",
            {"name": "Fatigue", "parent_id": existing["Offshore"]},
        )["id"]
    return existing


def wait_for_extraction(
    api: Api, attachment_ids: list[str], timeout: float = 120
) -> None:
    pending = set(attachment_ids)
    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
        rows = api.get("/api/me/extraction/attachments", limit=500)
        rows = (
            rows if isinstance(rows, list) else rows.get("items", rows.get("rows", []))
        )
        done = {
            r["id"]
            for r in rows
            if r.get("extraction_status") in {"extracted", "empty", "failed", "skipped"}
        }
        pending -= done
        if pending:
            time.sleep(2)
    if pending:
        print(
            f"  ! {len(pending)} PDF(s) not extracted after {timeout:.0f}s — is the "
            "worker running? Search screenshots will be missing page hits.",
            file=sys.stderr,
        )


def check_stack(pw: Playwright, base_url: str) -> bool:
    """Whether a dev stack answers at base_url; says what to do if not."""
    probe = pw.request.new_context(base_url=base_url)
    try:
        ok = probe.get("/health", timeout=5000).ok
    except Exception:
        ok = False
    probe.dispose()
    if not ok:
        print(
            f"Nothing answering at {base_url}/health — start the stack with "
            "`pixi run up` (or pass --base-url).",
            file=sys.stderr,
        )
    return ok


def session(pw: Playwright, base_url: str) -> Api:
    """The demo user's session, over plain HTTP — no browser needed.
    Dispose of it with `api.r.dispose()`."""
    api = Api(pw.request.new_context(base_url=base_url))
    dev_login(api, DEMO_EMAIL, DEMO_NAME)
    return api


def seed(pw: Playwright, base_url: str) -> Demo:
    """Put the demo library in place, idempotently. Plain HTTP, so the
    CLI stories (scripts/cli_stories.py) seed the same library the web
    stories do without starting a browser."""
    print("Seeding demo data…")
    # The member has to have signed in once before they can be added to a space.
    member = Api(pw.request.new_context(base_url=base_url))
    dev_login(member, MEMBER_EMAIL, MEMBER_NAME)
    member.r.dispose()

    api = session(pw, base_url)
    personal = next(s["slug"] for s in api.get("/api/me/spaces") if s["is_personal"])

    # Standards space, shared with a viewer and open to subscribers.
    ensure_space(api)
    members = api.get(f"/api/spaces/{STANDARDS_SLUG}/members")
    if not any(m.get("email") == MEMBER_EMAIL for m in members):
        api.post(
            f"/api/spaces/{STANDARDS_SLUG}/members",
            {"email": MEMBER_EMAIL, "role": "viewer"},
        )
    api.patch(f"/api/spaces/{STANDARDS_SLUG}/settings", {"subscribable": True})
    subs = api.get(f"/api/spaces/{personal}/inherits")
    if not any(s.get("parent_slug", s.get("slug")) == STANDARDS_SLUG for s in subs):
        api.post(f"/api/spaces/{personal}/inherits", {"parent_slug": STANDARDS_SLUG})

    attachments: list[str] = []
    standard_id = ""
    for ed in STANDARDS:
        data = {**STANDARD_FIELDS, **{k: v for k, v in ed.items() if k != "sections"}}
        item_id, _ = ensure_item(api, STANDARDS_SLUG, "standard", data)
        pdf, _ = make_pdf(f"{data['designation']}:{data['edition']}", ed["sections"])
        attachments.append(
            ensure_pdf(api, item_id, f"NX-ACME-1234-{ed['issuedOn'][:4]}.pdf", pdf)
        )
        api.put(
            f"/api/items/{item_id}/revision",
            {
                "body": data["standardBody"],
                "designation": data["designation"],
                "label": data["edition"],
                "issued_on": data["issuedOn"],
                "title": data["title"],
            },
        )
        standard_id = item_id  # the last one is the current edition

    collections = ensure_collections(api, personal)
    ids = {}
    for doc in (ARTICLE, REPORT):
        data = {"title": doc["title"], **doc["data"]}
        item_id, created = ensure_item(api, personal, doc["item_type"], data)
        pdf, rects = make_pdf(doc["title"], doc["sections"], doc.get("highlight"))
        filename = doc["title"].split(" ")[0].lower() + ".pdf"
        att_id = ensure_pdf(api, item_id, filename, pdf)
        attachments.append(att_id)
        ensure_tags(api, personal, item_id, doc["tags"])
        api.put(
            f"/api/items/{item_id}/collections",
            {"collection_ids": [collections[doc["collection"]]]},
        )
        if created and "note" in doc:
            api.post(
                f"/api/items/{item_id}/notes",
                {"content_html": doc["note"], "visibility": "space"},
            )
        ids[doc["title"]] = (item_id, att_id, rects)

    article_id, article_att, article_rects = ids[ARTICLE["title"]]
    page_no, phrase = ARTICLE["highlight"]
    highlights = api.get(f"/api/attachments/{article_att}/annotations")
    highlight = next((h for h in highlights if h.get("text") == phrase), None)
    if highlight is None:
        highlight = api.post(
            f"/api/attachments/{article_att}/annotations",
            {
                "kind": "highlight",
                "page_number": page_no,
                "rects": article_rects,
                "color": "#ffd400",
                "text": phrase,
            },
        )

    wait_for_extraction(api, attachments)
    api.r.dispose()
    return Demo(
        personal_slug=personal,
        article_id=article_id,
        article_attachment_id=article_att,
        highlight_id=highlight["id"],
        report_id=ids[REPORT["title"]][0],
        standard_id=standard_id,
    )


# ── Stories ──────────────────────────────────────────────────────────────────


@dataclass
class Story:
    name: str
    summary: str
    # Returns None to shoot the viewport, or a clip rect to crop to.
    run: Callable[[Page, Demo], dict | None]


STORIES: dict[str, Story] = {}


def story(name: str, summary: str):
    def register(fn: Callable[[Page, Demo], dict | None]):
        STORIES[name] = Story(name, summary, fn)
        return fn

    return register


def settle(page: Page) -> None:
    """Let requests finish and transitions end before the shutter."""
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(600)


@story("landing", "The landing page, as it greets you after signing in")
def _landing(page: Page, demo: Demo) -> dict:
    page.goto("/home")
    box = page.get_by_placeholder("Search your spaces…")
    box.wait_for()
    settle(page)
    # The page is a small block centred in a lot of empty space; crop to
    # the block (logo above the search box, links below) plus a margin,
    # so the README's opening figure is the page rather than the void.
    b = box.bounding_box()
    width, height = 900, 260
    return {
        "x": b["x"] + b["width"] / 2 - width / 2,
        "y": b["y"] - 150,
        "width": width,
        "height": height,
    }


@story("search", "Search every space at once, with the PDF pages that matched")
def _search(page: Page, demo: Demo) -> None:
    page.goto("/home")
    box = page.get_by_placeholder("Search your spaces…")
    box.fill("load case")
    # Scoped to the results: the space picker is a <select>, whose hidden
    # <option>s answer to the same role.
    page.get_by_role("listbox").get_by_role("option").first.wait_for()
    settle(page)


@story("library", "The library: collections, tags, and an item's details")
def _library(page: Page, demo: Demo) -> None:
    page.goto(f"/library?space={demo.personal_slug}&item={demo.article_id}")
    page.locator(f'tr[data-item-row="{demo.article_id}"]').wait_for()
    settle(page)


@story("standard-revisions", "An engineering standard and its editions")
def _standard(page: Page, demo: Demo) -> None:
    page.goto(f"/library?space={STANDARDS_SLUG}&item={demo.standard_id}&revisions=all")
    page.locator(f'tr[data-item-row="{demo.standard_id}"]').wait_for()
    settle(page)


@story("reader", "The PDF reader, opened at a linked highlight")
def _reader(page: Page, demo: Demo) -> None:
    page.goto(f"/reader/{demo.article_attachment_id}?annotation={demo.highlight_id}")
    page.locator("[data-index] canvas").first.wait_for()
    highlights = page.get_by_role("button", name="Highlights")
    if highlights.count() and highlights.first.get_attribute("aria-pressed") != "true":
        highlights.first.click()
    settle(page)
    page.wait_for_timeout(800)  # the ring on the linked passage animates in


@story("reader-info", "The reader with the document's details open beside it")
def _reader_info(page: Page, demo: Demo) -> None:
    page.goto(f"/reader/{demo.article_attachment_id}?page=1")
    page.locator("[data-index] canvas").first.wait_for()
    page.get_by_role("button", name="Document info").click()
    page.get_by_role("complementary", name="Document info").get_by_role(
        "heading", name=ARTICLE["title"]
    ).wait_for()
    settle(page)


@story("sharing", "Settings → Spaces: members, roles and inheritance")
def _sharing(page: Page, demo: Demo) -> None:
    page.goto("/settings/spaces")
    # The Standards row, expanded to show who it's shared with.
    row = (
        page.locator("div", has=page.get_by_text(STANDARDS_SLUG, exact=False))
        .filter(has=page.get_by_role("button", name="Sharing"))
        .last
    )
    row.get_by_role("button", name="Sharing").click()
    page.get_by_text(MEMBER_NAME).first.wait_for()
    settle(page)


@story("api-tokens", "Settings → API tokens: minting one for the CLI or a script")
def _tokens(page: Page, demo: Demo) -> None:
    page.goto("/settings/tokens")
    page.get_by_role("button", name="New token").click()
    settle(page)


# ── Runner ───────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base-url",
        default="http://localhost:5173",
        help="the SPA, as `pixi run up` prints it (default %(default)s)",
    )
    parser.add_argument(
        "--story",
        action="append",
        choices=sorted(STORIES),
        help="run only this story; repeatable",
    )
    parser.add_argument("--list", action="store_true", help="list the stories and exit")
    parser.add_argument(
        "--theme",
        choices=["dark", "light"],
        help="set the app's theme; writes <story>-<theme>.png "
        "(default: the app's own default, <story>.png)",
    )
    parser.add_argument("--headed", action="store_true", help="show the browser")
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
    suffix = f"-{args.theme}" if args.theme else ""

    with sync_playwright() as pw:
        if not check_stack(pw, args.base_url):
            return 1
        demo = seed(pw, args.base_url)

        browser = pw.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(base_url=args.base_url, viewport=VIEWPORT)
        if args.theme:
            # The SPA's own preference (frontend/src/auth/prefs.ts), not the
            # OS color scheme: the app ignores that unless set to "system".
            ctx.add_init_script(
                f"localStorage.setItem('shelf:pref:theme', '\"{args.theme}\"')"
            )
        dev_login(Api(ctx.request), DEMO_EMAIL, DEMO_NAME)
        page = ctx.new_page()
        failed = []
        for s in selected:
            path = args.out / f"{s.name}{suffix}.png"
            try:
                # A story may return a clip rect to crop to; most shoot
                # the whole viewport.
                clip = s.run(page, demo)
                page.screenshot(path=path, clip=clip)
                print(
                    f"  ✓ {s.name:<20} {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}"
                )
            except Exception as exc:  # keep going; report every broken story
                failed.append(s.name)
                print(f"  ✗ {s.name:<20} {exc}", file=sys.stderr)
        ctx.close()
        browser.close()

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
