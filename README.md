# shelf

A self-hosted web library for documents: reference metadata, a PDF reader, and an OCR pipeline, in one image.

I wanted a reference manager I could run on my own hardware, that kept the PDFs searchable, so I wrote one. It works for what I use it for. It is not a product, and it has one deployment behind it — expect rough edges, missing conveniences, and an API that will change without much ceremony.

**Status:** early development. The API and data model are not stable.

## What's in it

- Items with type-specific metadata (JSONB, so item types change without a migration), nested collections, tags, notes, creators.
- An in-browser PDF reader with annotations, a generated outline, and pinch-zoom. Highlights are linkable — **Copy link** on one gives a URL that opens the document at that passage and rings it.
- Search over item metadata and the text extracted from each PDF page, using Postgres `tsvector` + GIN with trigram indexes for fuzzy title matching. No separate search service. The landing page searches every space you can read at once — your own shelf, spaces shared with you, and the ones those subscribe to — listing each document once no matter how many of those reach it, with a filter for taking a noisy library back out.
- Background OCR and text extraction: Tesseract (via `ocrmypdf`) on CPU, and optionally [olmOCR](https://github.com/allenai/olmocr) on a GPU host for scans Tesseract mangles. Originals are kept; OCR output becomes a new version you can switch between.
- Export to BibTeX, CSL-JSON, and Zotero RDF — the last optionally bundled as a ZIP with the files, in the layout Zotero's own translator produces.
- Bulk select, bulk add-to-collection, and a ZIP download that serves each document's current best version.
- Spaces as the unit of ownership and sharing, so the schema doesn't need reworking if more than one person ever uses an instance.
- OIDC login against any compliant provider, plus scoped API tokens for scripts.
- One Docker image (API + built SPA), and a Helm chart if you're on Kubernetes. The image installs the locked pixi environment, so what CI tested is what runs.

## Quick start (local dev)

Requires [pixi](https://pixi.sh) and Docker.

```bash
pixi install
pixi run up                  # everything, in one terminal
```

### Dependencies

`pixi.toml` and `pixi.lock` decide every package version — locally, in CI, and
in the container images, which install the locked `prod` (or `gpu`) environment
rather than resolving anything of their own. Packages come from conda-forge
unless they aren't published there; each `[pypi-dependencies]` entry in
`pixi.toml` says which case it is. Neither `pyproject.toml` lists runtime
dependencies: they hold build metadata only, and a `pip install .` of this
repo deliberately installs no dependencies.

To change a dependency: edit `pixi.toml`, run `pixi lock`, commit the lockfile
with it. CI installs with `--locked` and fails on a stale lock, which is the
point — a dependency release cannot reach production without a diff someone
reviewed. It once did: SQLAlchemy 2.1.0 moved greenlet behind an extra, the
images resolved it from PyPI at build time, and every pod died on startup with
CI green on the lockfile's 2.0.49 throughout.

`up` starts Postgres + Redis + Gotenberg + an object store and waits on their healthchecks, applies migrations, installs the frontend's `node_modules` if they're missing, then runs the backend (`:8000`) and the vite dev server (`:5173`) side by side with prefixed output. Ctrl-C stops both servers; the containers stay up so the next `up` is quick. `pixi run dev-down` stops those when you're done.

### Object store

Two S3 implementations are wired up as compose profiles and are interchangeable — shelf talks to both through the same presigned-URL path:

```bash
pixi run up --s3 garage      # default — https://garagehq.deuxfleurs.fr
pixi run up --s3 rustfs      # https://github.com/rustfs/rustfs
pixi run up --s3 none        # skip it; metadata works, uploads don't
```

Both publish the S3 API on `:3900` with the same bucket, region and credentials, so `SHELF_S3_*` doesn't change when you switch and the running one is the only difference. `up` stops the other before starting the one you asked for, since they'd otherwise collide on the port. It also puts a permissive CORS rule on the bucket: uploads go from the browser straight to a presigned URL, which is cross-origin, and Garage refuses the preflight until told otherwise. Garage creates its bucket and access key on first boot from `GARAGE_DEFAULT_*`; RustFS starts empty, so `up` creates the bucket itself. RustFS also serves a web console on `:9001`.

The dev credentials are committed in `compose.yaml` on purpose so a fresh checkout needs no setup. They're throwaways — anything exported in your shell wins, so point `SHELF_S3_*` at your own store and `up` leaves it alone.

### Ports

Every port the stack publishes is a common one — `:5173` for vite, `:8000` for the backend, `:5432`, `:6379`, `:3000`, `:3900` for the containers — so any of them may already be taken by another checkout, another project's compose stack, or a service you run anyway. `up` probes each one before it starts anything and moves to the next free number, printing where it landed:

```
    5173 is in use — running the frontend on 5174
    3900 is in use — publishing the S3 API on 3901
==> Open http://localhost:5174 — ...
```

Nothing else needs adjusting when one moves. The SPA reaches the API through vite's proxy, so its calls are same-origin regardless of port, and OIDC redirect URIs come from the backend's own `SHELF_PUBLIC_BASE_URL`. A moved container port is passed to compose and written to `./.env` — which compose reads on its own, so `dev-down`, `dev-logs` and `test-db-up` address the same containers — and the matching `SHELF_DATABASE_URL` / `SHELF_REDIS_URL` / `SHELF_GOTENBERG_URL` / `SHELF_S3_ENDPOINT` is set for the backend and the migrations. Ports one of your own containers already publishes are kept rather than re-picked, so re-running `up` doesn't recreate a working container.

Two things are outside that: `--web-port` / `--api-port` pin the two servers, in which case the port is used as given rather than scanned for; and `pixi run test` reads `backend/.env` directly, so a bumped Postgres needs `SHELF_DATABASE_URL` set there before the suite will connect — `up` says so when it happens.

The individual tasks are still there if you'd rather drive one piece at a time:

```bash
pixi run dev-up              # Postgres + Redis + Gotenberg via compose.yaml
pixi run alembic-up          # apply migrations
pixi run dev-api             # backend on http://localhost:8000

# In another terminal, for the SPA:
pixi run frontend-install
pixi run frontend-dev        # http://localhost:5173 (proxies /api and /auth to the backend)
```

Open http://localhost:5173. `SHELF_DEV_LOGIN_ENABLED` defaults to true, so the login page offers a dev-login form — enter any address and you're in, no identity provider required. It's the same thing as `POST /auth/dev-login`, and `/auth/providers` reports whether it's available so the SPA knows to show it. Turn it off anywhere others can reach it: it mints a session for any email presented.

## Running the tests

The suite needs a live Postgres — the fixtures truncate real tables between tests rather than mocking the database. `test-all` handles that for you:

```bash
pixi run test-all       # starts Postgres, waits for it, migrates, runs pytest
```

It only starts the `postgres` service, waits on its healthcheck rather than sleeping, and is a no-op if the container is already up — so it behaves the same on Windows and Linux, and re-running it in a loop stays quick. `pixi run test` is the bare pytest if you already have a database up and migrated.

`pixi run lint` and `pixi run typecheck` need no services. `pixi run dev-down` stops the stack when you're done.

### Frontend

The SPA has its own suite — vitest and Testing Library on jsdom — which needs
no services at all: components under test talk to a stubbed `fetch`, never a
real API.

```bash
pixi run frontend-test            # once
pixi run frontend-test-watch      # on change
pixi run frontend-test-coverage   # with a v8 coverage report
```

Helpers live in `frontend/src/test/utils.tsx`: `renderWithProviders` wraps a
component in a router and a throwaway QueryClient, `mockFetch` stubs responses
by path prefix, and `makeMe` / `makeMeWithTwoAccounts` build the `/api/me`
shapes. An unstubbed request throws rather than hanging, so a test that reaches
for the network says so.

Full-page navigations go through `frontend/src/lib/navigation.ts` rather than
calling `window.location.assign` inline — partly to keep the "reload, don't
router-navigate" decision documented in one place, partly because jsdom won't
let a test intercept it otherwise.

CI runs five checks on every pull request, each as its own status check: lint,
typecheck, and the backend suite; plus the frontend's typecheck and unit tests.

## Configuration

Settings are environment variables under the `SHELF_` prefix, read via pydantic-settings; `backend/.env.example` lists the common ones. The defaults target local development and are not safe to deploy as-is.

```
SHELF_DATABASE_URL=postgresql+asyncpg://shelf:shelf@localhost:5432/shelf
SHELF_REDIS_URL=redis://localhost:6379/0

# Any S3-compatible store. Uploads and downloads are presigned, so file
# bytes go browser <-> store without passing through the API.
SHELF_S3_ENDPOINT=http://localhost:3900
# Set this when the browser cannot reach SHELF_S3_ENDPOINT - the API and the
# browser are on different networks, so the address the server uses to reach
# the bucket is not one a browser can resolve or load. Presigned URLs are
# signed for this host instead; server-side calls keep using SHELF_S3_ENDPOINT.
# Leave unset when both sides share a network, as they do in dev.
SHELF_S3_ENDPOINT_PUBLIC=
SHELF_S3_REGION=us-east-1
SHELF_S3_BUCKET=shelf
SHELF_S3_ACCESS_KEY_ID=
SHELF_S3_SECRET_ACCESS_KEY=

SHELF_GOTENBERG_URL=http://localhost:3000
SHELF_CORS_ORIGINS=["http://localhost:5173"]

SHELF_SESSION_SECRET_KEY=<openssl rand -hex 32>
SHELF_SESSION_COOKIE_SECURE=true
SHELF_DEV_LOGIN_ENABLED=false
SHELF_PUBLIC_BASE_URL=https://shelf.example.com

# Emails promoted to admin on login. See "Roles and accounts" below.
SHELF_ADMIN_EMAILS=["you@example.com"]

# The API checks its dependencies once at startup and refuses to serve if
# one is misconfigured - see "Startup checks" below. Set to false only to
# start the app without them on purpose.
SHELF_PREFLIGHT_ENABLED=true

# NATS JetStream, for the background workers. Empty disables job publishing;
# the API still records the queued state on the row, so nothing is lost when
# no worker is running — the PDF just never gets its text until something
# re-queues it. `pixi run up` sets this for you.
SHELF_NATS_URL=nats://nats:4222
```

## Startup checks

The API verifies its dependencies once at startup and refuses to serve if
one is misconfigured, so a broken deployment fails at boot with the reason
in its logs rather than reporting itself healthy and failing later in a
browser. It checks that the database is reachable and migrated, that the
bucket exists and the credentials can address it, that the bucket answers
a CORS preflight for `SHELF_PUBLIC_BASE_URL` (uploads go browser-to-store,
so a missing rule breaks them and nothing reaches the server), that the
browser-facing store endpoint is not plaintext when the app is served over
HTTPS, and that every OIDC issuer resolves a discovery document.

Failures that look transient - connection refused, timeout, 5xx - are
retried before giving up, so a dependency that is slow to start does not
become a crash loop. Configuration faults are not retried.

`/readyz` re-runs the cheap subset per request and is the readiness probe;
`/health` stays a plain liveness check that never touches a dependency, so
an outage takes an instance out of rotation rather than restarting it.

Set `SHELF_PREFLIGHT_ENABLED=false` to start without them.

## OIDC / SSO

Providers are a JSON list and none is special-cased — Authentik, Keycloak, Entra ID, Google, or anything else OIDC-compliant. Endpoints come from `{issuer}/.well-known/openid-configuration`.

```
SHELF_OIDC_PROVIDERS='[{"name":"authentik","issuer":"https://authentik.example.com/application/o/shelf/","client_id":"...","client_secret":"..."}]'
```

Register the redirect URI as `{SHELF_PUBLIC_BASE_URL}/auth/callback/{name}`, where `name` is the provider's label in the JSON above — so the example needs `https://shelf.example.com/auth/callback/authentik`. On first login shelf creates the user, the identity record, and a personal space; later logins match on `(idp, subject)`, so a user keeps their library if their email changes.

### Per-provider tuning

Two optional fields exist for providers that deviate from the common case. Both
default to what a standards-compliant provider expects, so Authentik, Keycloak,
Google and most others need neither.

| Field | Default | Set it when |
|---|---|---|
| `subject_claim` | `sub` | the provider's `sub` isn't stable for a user across applications |
| `link_prompt` | `select_account` | the provider doesn't implement that `prompt` value |

**`subject_claim`** picks the claim shelf keys identities on. Azure AD / Entra
needs `"oid"`: its `sub` is pairwise — a different value per application
registration — so the same person looks like a different subject to every app,
while `oid` is stable across the tenant.

Changing this on a running instance changes what gets matched in `identities`,
so existing users arrive as a new `(idp, subject)` pair. They're re-linked by
email on next login and keep their library, as long as the address still
matches.

**`link_prompt`** is the `prompt` sent when adding a second account. The default
`select_account` is standard OIDC and makes the provider show its account
picker; without it, a provider that keeps you signed in silently returns the
same account and linking a second one is impossible. A provider that doesn't
implement it returns `account_selection_required` — shelf reports that with the
fix in the message. Set `"login"` there instead (universally supported; forces
re-authentication so a different account can be entered), or `""` to send no
prompt.

```
# A provider on the defaults needs nothing extra:
SHELF_OIDC_PROVIDERS='[{"name":"authentik","issuer":"https://authentik.example.com/application/o/shelf/","client_id":"...","client_secret":"..."}]'

# Entra wants both:
SHELF_OIDC_PROVIDERS='[{"name":"entra","issuer":"https://login.microsoftonline.com/<tenant>/v2.0","client_id":"...","client_secret":"...","subject_claim":"oid"}]'
```

Several providers can be configured at once; the login page lists each, and the
account switcher sends "switch user" to whichever one the active account signed
in with.

Behind a reverse proxy, uvicorn needs `--proxy-headers --forwarded-allow-ips='*'` so redirect URIs are built as `https://…` and match what the IdP has registered. The Dockerfile already does this.

## Roles and accounts

Two *instance* roles, `user` and `admin`. Everyone is a `user`; admins
additionally get an Admin tab in Settings, which lists everyone on the instance,
hands out roles, and can add an account from an email address before its owner
has ever signed in (see "Sharing a space"). Admin is a cookie-session thing — no
API token scope grants it, so a leaked script token can't reach those routes.

Being an admin does **not** grant access to anyone's spaces. Handing out roles
and reading everybody's library are different powers, and keeping them apart
makes the admin role far less dangerous to hold. An admin who needs a space asks
its owner, like anyone else.

A fresh instance has no admin. Name yourself in `SHELF_ADMIN_EMAILS` and log in;
the role is granted on login and then lives in the database, so removing the
address later doesn't take it away. `pixi run grant-admin <email>` does the same
to an existing user without a restart, and `--revoke` reverses it. The last
remaining admin can't be demoted through the UI, so an instance can't lock
itself out by accident.

**In local development** you get an admin without any of that: `pixi run up`
sets `SHELF_DEV_LOGIN_ROLE=admin` for the backend it launches, so accounts made
through the dev-login form are admins and the Admin tab is there to look at. The
setting defaults to `user` everywhere else, and deliberately — dev login mints a
session for any address presented, so defaulting it to `admin` would turn
"forgot to switch dev login off" into "anyone who can reach this is an admin".
Set it explicitly in `backend/.env` or the shell to override what `up` picks.

Like `SHELF_ADMIN_EMAILS` it only ever grants. Env can hand out a role; only the
admin UI or `pixi run grant-admin --revoke` takes one back. A knob that demoted
would quietly strip, at the next sign-in, a role you'd set on purpose.

Roles are read from the database on every request, so a change takes effect on
the next one rather than whenever the session happens to expire.

### Switching between accounts

If you have more than one identity — two work accounts at different tenants, say
— you can attach them to the same browser session and flip between them without
logging out, from the menu in the header or **Switch user** under Settings →
Account. "Add account" runs the normal code flow against
`/auth/link/{provider}`, asking the provider for its account picker so you
actually get a choice of which identity to sign in as. Each account keeps its
own spaces and library; switching changes who you are and nothing else.

The linked set lives in the session cookie, so it only ever contains accounts
that completed a login in this browser, and it lasts as long as the session.
Signing out clears all of them at once; unlink one from Settings to drop just
that one.

## Linking into a document

The reader reads three query params:

| Param | Points at |
|---|---|
| `?page=N` | a page. Written back as you scroll, so reload and back/forward restore your position |
| `?annotation=<id>` | a highlight — its page, plus a ring on the passage itself |
| `?find=term` | opens the find toolbar pre-filled. A search, not an address: it lands on the first textual match, or nowhere if OCR mangled the word |

`?annotation=` is the precise one. Annotations carry rects in PDF
user-space, which survive zoom, re-render and DPI differences, so the link
resolves to the same passage for everyone who can open the space. Get one from
**Copy link** in the highlights panel.

Nothing smaller than that is addressable: shelf never extracts tables, figures
or equations as objects, so there's no identifier to put in a URL for them.
`?page=N&find=Table%203.1` is the honest workaround and it is a guess, not an
anchor.

## Sharing a space

Spaces are the unit of sharing. Each one has a creator, who is always its owner,
plus any number of members at one of two levels:

| Role | Can |
|---|---|
| `viewer` | read items, attachments, notes and tags; search; export |
| `editor` | all of the above, plus create, edit and delete content |
| `owner` | all of the above, plus manage who has access |

Owner belongs to the creator and isn't assignable — there's no second owner, and
no membership row to delete that would lock the creator out of their own space.
An editor can fill a space but can't widen access to it, so "who else can see
this" stays the owner's decision.

Everyone gets a personal space at first login. **Admins can create additional
shared spaces** from Settings → Spaces — the creator owns it and picks who else
is in it. Creation is admin-gated because spaces are cheap to make and awkward
to clean up; making a space still grants nothing over spaces other people own.

**Rename** in the same panel changes a space's name and slug. The owner can do
that to their own space, and an instance admin can do it to any *shared* space —
a deliberate, narrow exception to "admins get nothing here", because a label is
not a way in. An admin who renames a space still can't list a single item in it,
see its members, or add to it. Nobody renames somebody else's personal shelf,
and a personal space's slug is fixed either way: `is_personal` is derived from
the `u-` prefix, so moving it would quietly reclassify the space.

Changing a slug changes the space's URL and there's no redirect from the old
one, so links already shared will 404. Nothing stored points at a slug — API
tokens carry scope labels and collection ids — so no access breaks.

Manage members under Settings → Spaces. You pick people from a dropdown of
everyone with an account. Removing someone revokes their access but leaves the
content they created — it belongs to the space, not to them.

People appear in that dropdown when they first sign in — nothing is synced from
the identity provider ahead of that, so assigning someone the app in Entra (or
Authentik, or anywhere else) doesn't create a shelf account until they actually
log in. To add someone to a space before then, **admins can pre-provision an
account by email** from Settings → Admin. It creates the user row and their
personal space with no identity attached; their first sign-in links onto that
row by email rather than making a second account. Use the same address the
provider sends, or they'll get that second account.

That dropdown is backed by `GET /api/users`, which **any signed-in user can
read**: it lists every account's display name and email address. Everyone owns
their personal space and so may need to share it, which is why it isn't
admin-only. Nothing else is exposed — no roles, no identities, nothing about
anyone's library — but if you hand out accounts to people who shouldn't see each
other's addresses, this is the endpoint to put behind something narrower.

Attachments uploaded from now on are stored under `spaces/{space_id}/…`, so a
bucket policy or lifecycle rule can address one space's objects without going
through the database. Existing objects keep their original keys and are not
rewritten; keys are stored per row, so both layouts coexist.

## Inheriting a space

A space can subscribe to another and read its items without holding a copy.
The shape it's for: one shared **Standards** space holds one copy of each
standard, and every project space — and every person who wants them in their own
library — subscribes to it. Corrections happen once and reach everyone.

Two sides have to agree, and the two panels live under Settings → Spaces →
**Inheritance**:

- The space **being read** opts in: its owner ticks *Let other spaces inherit
  this one*. That's the consent, because everyone who can read a subscribing
  space will be able to read this one's items.
- The space **doing the reading** subscribes: its owner picks from the list of
  spaces that have opted in.

Inherited items are read-only wherever they're borrowed — the API refuses the
writes, not just the UI — and are marked as inherited in the detail panel. Three
properties keep the grant honest:

- **It grants read and nothing else.** An editor on a project space is still a
  viewer on the standards it inherits, and is not a member of that space.
- **It does not chain.** A inherits B, B inherits C — A does not see C. One hop
  is what the model promises, so an owner can answer "who can see my items" from
  one table.
- **The owner keeps control.** *Inherited by* lists every subscriber with a
  Revoke button. Turning the flag back off stops new subscriptions and leaves
  existing ones alone — access silently evaporating across every project is a
  worse surprise than a stale subscription.

Personal spaces can subscribe to a shared space but can't be subscribed *to*.

### Notes and highlights on someone else's document

Once one PDF is read by the whole company, "everyone who can read this document"
is the wrong audience for a working note. So notes and highlights carry a
visibility:

| | Who sees it |
|---|---|
| `private` | the author, and nobody else — not even the owner of the space the document lives in |
| `space` | everyone who can read the space that owns the **item** |

On an inherited document a new note or highlight starts **private**. Share it and
it reaches the space that owns the document — the other subscribers to Standards,
not your own shelf where nobody is. Only the author can share one or take it
back; an owner can't publish your notes for you, and can't retract them either.

Writing a note needs only read access — annotating a document you can only read
is the whole point. In a space you're actually a member of, notes keep the
behaviour they always had and start shared with that space.

## Engineering standards

Standards get republished, and which edition applies is a decision a project
makes deliberately. Two things the plain item model can't express:

**Revisions know about each other.** Pick the *Engineering Standard* item type,
fill in the issuing body, designation and edition, then file it under a standard
from the item's detail panel. Editions matched on body + designation (ignoring
case) share one history, so the detail panel gets a dropdown of every edition
you can open and a badge saying whether this is the current one.

"Latest" means the newest edition *you can read*, not the newest row in the
database. When the instance holds something newer that you can't reach, the panel
says so instead of presenting a stale edition as current. An edition with no
issue date sorts last and is never latest — "Rev. 5" and "2020" can't be compared
to each other, so only dates are trusted.

**A space pins the edition it uses.** A project space inheriting Standards can
pin "we build to the 2018 edition"; its library then lists that one and hides the
other four. *Show all revisions* in the library toolbar reveals them, and the
Standards space itself always stays the complete record. Pinning takes owner, not
editor — it changes what everyone else in the space sees by default. Pins survive
dropping and re-adding a subscription.

## Copying an item to another space

**Copy to another space** in the item detail panel makes a real copy: a new item,
new attachment rows, new objects in the bucket. Metadata, files, extracted page
text and tags come across; tags are matched by name into the target space's own
tag table. Collections don't — folders are the target's own structure. Neither do
notes and highlights: they belong to whoever wrote them, under the visibility
they chose, and republishing them into a space those people may not be in is a
disclosure rather than a copy.

If the other space only needs to *read* the document, inherit instead. One copy
of the bytes, one place to fix a mistake, and everyone sees the fix.

## API tokens

Mint them under Settings → API tokens. The plaintext is shown exactly once and
there is no other path to it. A token carries coarse scopes — `upload`,
`search`, `download` — and acts as the user who minted it, so by default it
reaches every space that user can: their own, any shared with them, and any
those inherit.

Two optional allow-lists narrow it further:

- **Spaces** — the coarse cut, and usually the one you want. A token for an
  import script that should only touch one project, or a read-only token that
  should see the shared Standards space and nothing of your own.
- **Collections** — finer, within a space, optionally including everything
  nested below the ones you pick (resolved at request time, so subcollections
  added later are covered).

Neither can widen access. Both are intersected with what the user can read on
every request, so a token outlives neither a revoked membership nor a dropped
subscription — and a space allow-list is stored as ids, not slugs, so renaming
a space doesn't quietly break it.

Admin is deliberately unreachable by token: no scope grants it, so a leaked
script token can't reach the admin routes.

### The `shelf` CLI

[`cli/`](cli/README.md) is a command-line client for the REST API, installable
on its own:

```sh
pixi global install shelf-cli \
  --git https://github.com/Krande/shelf.git --subdirectory cli --tag v0.4.0
```

It does two things worth having: sets metadata fields without curl, and pushes
**document profiles** — metadata kept in JSON files, somewhere other than this
repo — into an instance whenever one is ready.

```sh
shelf items set <id> --set designation="NX-ACME 1234" --set numberOfPages=74
shelf profiles push ./profiles/ --dry-run
```

Pushing twice updates rather than duplicates, matching on the document's own
identity where it has one and on its file's SHA-256 where it doesn't — see the
CLI README for why it's that way round.

### What a token can do

`/api/v1/*` covers a whole import without touching the SPA:

| | |
|---|---|
| `POST /api/v1/items` | create an item with its metadata |
| `PATCH /api/v1/items/{id}` | set type and metadata fields |
| `GET /api/v1/items/{id}` | read one back |
| `PUT /api/v1/items/{id}/revision` | file it as one edition of a standard |
| `POST /api/v1/upload`, `/uploads/register`, `/uploads/{id}/complete` | attach files |
| `GET /api/v1/search`, `/collections` | find things |

`data` is the same opaque JSON the SPA writes, so whatever that item type's
form would capture goes straight in:

```bash
curl -X POST https://shelf.example.com/api/v1/items \
  -H "Authorization: Bearer $SHELF_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"item_type": "standard", "space_slug": "standards", "data": {
        "title": "Guidance on the design of widgets — Part 2: Plated widgets",
        "standardBody": "NX Standards",
        "designation": "NX-ACME 1234",
        "edition": "2015+A2:2020+NA:2020",
        "nationalAnnex": "NA:2020 (Ruritania)",
        "amendments": "AC:2017, A1:2018, A2:2020",
        "issuedOn": "2020-10-01"
      }}'
```

`PATCH` replaces `data` wholesale, matching the SPA's own PATCH. Pass
`"merge": true` to set individual keys and leave the rest standing — the mode a
script enriching existing records wants — where a key set to `null` is removed.

Filing a revision uses the same family lookup the SPA does, matched
case-insensitively on issuing body + designation, so editions loaded by an
importer and editions filed by hand land in one revision history.

## Background workers

Jobs go to NATS JetStream and are consumed by `python -m shelf.worker`, running from the same image as the API. Job state also lives on the database row, so a worker being down delays work rather than losing it.

`pixi run up` starts NATS and a worker alongside the API and the SPA, so a local
stack processes uploads end-to-end: upload a PDF, and its text is extracted and
searchable a few seconds later. Which consumers run depends on what the machine
has —

| Consumer | Does | Needs |
|---|---|---|
| `extract` | body text + per-page text, quality assessment | pypdf (always available) |
| `outline` | heading detection, generated table of contents | PyMuPDF (always available) |
| `ocr` | Tesseract pass over scanned PDFs | `tesseract` + `gs` on PATH |

`ocr` is skipped with a note when those binaries are missing, which is the
normal case on Windows and macOS — pixi only installs them on linux-64. Override
with `pixi run up --consumers extract,ocr,outline`, or run without a worker at
all via `pixi run up --no-worker`.

Anything uploaded while no queue was running has a row but no message, so
nothing ever told the worker about it. `pixi run up` re-publishes those on every
start (idempotent — WorkQueue retention drops the duplicate once acked), and
`pixi run backfill-extract` does the same by hand. A PDF stuck at
`extraction_status = 'pending'` with no worker running is exactly this case.

- **CPU tier** — text extraction, PDF quality scoring, Tesseract OCR, outline generation (a PyMuPDF font heuristic). Runs anywhere.
- **GPU tier** — olmOCR for documents the CPU tier scores as poor. Built separately from `Dockerfile.gpu` (`pixi run gpu-image-build`) because it pulls a multi-GB Torch/CUDA stack.

A GPU host outside the cluster should use `SHELF_WORKER_BACKEND=api`, which routes database writes through `/api/v1/worker/*` with a scoped token (`pixi run mint-worker-token <label>`) so that host never holds database credentials. Use `sql` only for in-cluster pods on a trusted network.

## Deployment

Images are published to the GitHub Container Registry on each release tag:

```bash
docker run -p 8000:8000 ghcr.io/krande/shelf:latest
```

The image bundles the built SPA and serves it from the same origin as the API. A Helm chart is in [`deploy/helm/shelf/`](./deploy/helm/shelf/), with [`deploy/examples/values-example.yaml`](./deploy/examples/values-example.yaml) as a starting point. The chart expects a Kubernetes Secret holding at least `SHELF_DATABASE_URL` and `SHELF_SESSION_SECRET_KEY`.

## Contributing

Issues and PRs are welcome, though I make no promises about response time. PRs use conventional commit titles and a `release-*` label; [deputy](https://github.com/Krande/deputy) checks both, and merging a labelled PR cuts the tag that publishes the image. Configuration is in [`deputy.toml`](./deputy.toml).

## License

[GNU AGPL v3](./LICENSE). If you run a modified shelf as a network service, your modifications have to be available to its users.
