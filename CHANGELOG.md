# CHANGELOG



## v0.1.0 (2026-09-08)

### Feature

* feat: initial public release

Self-hosted web library for documents: FastAPI + SQLAlchemy backend, React
SPA, Postgres full-text search over item metadata and extracted PDF text,
and a NATS-driven OCR pipeline (Tesseract on CPU, olmOCR on GPU).

Licensed AGPL-3.0. CI runs lint, typecheck and the test suite on every PR
as independent status checks; deputy handles release tagging, and a version
tag publishes the image to ghcr.io. ([`278fabe`](https://github.com/Krande/shelf/commit/278fabedd3db55b89e364b7326b4216fdcb73082))

### Fix

* fix: wait for the object store on the host before using it

Switching stores could leave `up` talking to nothing. `docker compose stop`
returns before the host-side port forward is released, so starting the
replacement into that window makes compose reuse the existing container
with its published port silently unestablished. The container runs and
reports healthy -- the healthchecks curl 127.0.0.1 from inside it, so they
cannot see a dead host mapping -- and `--wait` is satisfied.

The bucket bootstrap was the first thing to touch :3900 from the host, so
`up --s3 rustfs` failed there with &#34;connection refused&#34; against a store
that every status command called healthy. With garage, which needs no
bootstrap, nothing checked at all: the stack came up clean and the first
sign of trouble would have been a browser upload failing against a
presigned URL.

Waits for :3900 to be released after stopping the other store, then waits
for it to answer from the host before going further, recreating the
container once if it does not. Both waits use the connect probe, so they
observe the same thing a browser would. ([`0e2f01d`](https://github.com/Krande/shelf/commit/0e2f01d3ff468a404ede59bd0067e5a32a1ff915))

* fix: make the local dev environment work from a fresh checkout

Adds `pixi run up`, a single command that takes a fresh checkout to a
browsable app: starts the compose services and waits on their healthchecks,
creates the object-storage bucket if the backend needs one, migrates,
installs frontend deps when absent, then runs uvicorn and vite together
with prefixed output until Ctrl-C. It is a script rather than a depends-on
chain because pixi runs each dependency to completion, so it cannot hold
two servers up at once, and stopping both from one Ctrl-C needs a
process-tree kill on Windows.

Along the way, three things that made local dev unusable:

The login page was a dead end. It rendered only OIDC provider buttons, so
a checkout with no SHELF_OIDC_PROVIDERS (the default) showed &#34;No identity
providers are configured&#34; and offered no way in. /auth/dev-login existed
and was enabled, but nothing in the SPA reached it. /auth/providers now
reports dev_login, and the page offers the form when it is true.

obstore refused every plaintext S3 endpoint. Its HTTP client rejects
http:// unless allow_http is set, failing with &#34;BadScheme&#34; before the
request goes out -- including the documented dev default of
http://localhost:3900. Presigned URLs masked it, since those are fetched
by the browser or httpx, so the breakage landed on the server-side calls:
object_exists() returned False for objects that existed, and so
ensure_original() silently skipped the OCR original-snapshot. The existing
test used MemoryStore, which never touches HTTP. Now opted into for
http:// endpoints only, so a misconfigured https:// deployment still
fails loudly.

compose.yaml had no object store at all, so uploads could not work
locally. Adds garage and rustfs behind compose profiles, selectable with
`up --s3`. Both publish :3900 with the same bucket, region and
credentials, so SHELF_S3_* is identical either way. Garage&#39;s config is an
inline compose config rather than a bind-mounted file: bind mounts fail
whenever the daemon does not share the client filesystem.

Also: `up` moves to the next free port when :5173 or :8000 is taken and
prints where it landed, and dev-down/dev-logs name the profiles so the
object store is not left holding :3900. ([`2cbbcdd`](https://github.com/Krande/shelf/commit/2cbbcdd5c9012802f4556163fc7d751b0bd4c2f3))

### Unknown

* Merge pull request #1 from Krande/fix/local-dev-env

fix: make the local dev environment work from a fresh checkout ([`7076a63`](https://github.com/Krande/shelf/commit/7076a63ebf35dbc088713c14c81e1cc7025b9e44))
