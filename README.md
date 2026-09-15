# shelf

A self-hosted web library for documents: reference metadata, a PDF reader, and an OCR pipeline, in one image.

I wanted a reference manager I could run on my own hardware, that kept the PDFs searchable, so I wrote one. It works for what I use it for. It is not a product, and it has one deployment behind it — expect rough edges, missing conveniences, and an API that will change without much ceremony.

**Status:** early development. The API and data model are not stable.

## What's in it

- Items with type-specific metadata (JSONB, so item types change without a migration), nested collections, tags, notes, creators.
- An in-browser PDF reader with annotations, a generated outline, and pinch-zoom.
- Search over item metadata and the text extracted from each PDF page, using Postgres `tsvector` + GIN with trigram indexes for fuzzy title matching. No separate search service.
- Background OCR and text extraction: Tesseract (via `ocrmypdf`) on CPU, and optionally [olmOCR](https://github.com/allenai/olmocr) on a GPU host for scans Tesseract mangles. Originals are kept; OCR output becomes a new version you can switch between.
- Export to BibTeX, CSL-JSON, and Zotero RDF — the last optionally bundled as a ZIP with the files, in the layout Zotero's own translator produces.
- Bulk select, bulk add-to-collection, and a ZIP download that serves each document's current best version.
- Spaces as the unit of ownership and sharing, so the schema doesn't need reworking if more than one person ever uses an instance.
- OIDC login against any compliant provider, plus scoped API tokens for scripts.
- One Docker image (API + built SPA), and a Helm chart if you're on Kubernetes.

## Quick start (local dev)

Requires [pixi](https://pixi.sh) and Docker.

```bash
pixi install
pixi run up                  # everything, in one terminal
```

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

# Empty disables job publishing; the API still records the queued state on
# the row, so nothing is lost when no worker is running.
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

### Which claim identifies the user

`subject_claim` picks the claim shelf keys identities on. It defaults to `sub`,
which is right for most providers. **Azure AD / Entra needs `"oid"`**: its `sub`
is pairwise — a different value per application registration — so the same
person looks like a different subject to every app, while `oid` is stable across
the tenant.

```
SHELF_OIDC_PROVIDERS='[{"name":"entra","issuer":"https://login.microsoftonline.com/<tenant>/v2.0","client_id":"...","client_secret":"...","subject_claim":"oid"}]'
```

Changing this on a running instance changes what gets matched in `identities`,
so existing users arrive as a new `(idp, subject)` pair. They're re-linked by
email on next login and keep their library, as long as the address still
matches.

Behind a reverse proxy, uvicorn needs `--proxy-headers --forwarded-allow-ips='*'` so redirect URIs are built as `https://…` and match what the IdP has registered. The Dockerfile already does this.

## Roles and accounts

Two *instance* roles, `user` and `admin`. Everyone is a `user`; admins
additionally get an Admin tab in Settings, which lists everyone on the instance
and hands out roles. Admin is a cookie-session thing — no API token scope grants
it, so a leaked script token can't reach those routes.

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

Manage members under Settings → Spaces. People are added by email and must have
signed in at least once, since shelf has no user directory to search (exposing
one to every account holder isn't a trade worth making). Removing someone
revokes their access but leaves the content they created — it belongs to the
space, not to them.

Attachments uploaded from now on are stored under `spaces/{space_id}/…`, so a
bucket policy or lifecycle rule can address one space's objects without going
through the database. Existing objects keep their original keys and are not
rewritten; keys are stored per row, so both layouts coexist.

## Background workers

Jobs go to NATS JetStream and are consumed by `python -m shelf.worker`, running from the same image as the API. Job state also lives on the database row, so a worker being down delays work rather than losing it.

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
