# CHANGELOG



## v0.3.0 (2026-09-14)

### Feature

* feat: verify dependencies at startup and add a real readiness probe

The app started regardless of whether its dependencies were usable, and
/health returned {&#34;status&#34;: &#34;ok&#34;} unconditionally. A misconfigured
deployment therefore reported itself healthy and failed later in a
browser, far from the cause: a 500 on the login redirect when an issuer
was not a resolvable URL, an upload blocked by CORS when the bucket
carried no rule, a presigned URL the browser refused as mixed content.
None of those are visible to a server-side request, and none of them
need to wait for a user to find.

Add a startup preflight covering exactly those failures:

  database        reachable, and alembic_version present - an unmigrated
                  schema only produces confusing errors further in
  object-store    bucket exists and the credentials can address it
  browser-upload  a real CORS preflight for public_base_url, plus a
                  refusal to serve an http:// store endpoint to an
                  https:// page
  oidc            every issuer resolves a discovery document carrying an
                  authorization_endpoint
  session-secret  not the documented dev value outside dev

The browser-upload check issues an actual OPTIONS request rather than
reading bucket metadata. A preflight is unauthenticated, so it needs no
signing, and it tests the thing that matters: whether a browser will be
allowed to use the URL the API hands it.

Failing fast is only safe if it distinguishes &#34;wrong&#34; from &#34;not up yet&#34;,
so transient faults - connect errors, timeouts, 5xx - are retried before
giving up, while configuration faults fail on the first attempt.
_is_transient matches ConnectionError rather than OSError precisely
because PermissionError is an OSError too, and retrying a denial only
delays the same failure.

Add /readyz, which re-runs the cheap subset per request, and point the
chart&#39;s readinessProbe at it. /health stays a plain liveness check that
touches nothing, so a dependency outage takes an instance out of
rotation instead of restarting it.

Preflight is on by default and disabled with SHELF_PREFLIGHT_ENABLED=false
for deployments that start without their dependencies on purpose.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt;
Claude-Session: https://claude.ai/code/session_01MgNYH1A9cGSt3nU71SJKyc ([`25077f5`](https://github.com/Krande/shelf/commit/25077f57677664f763aa15b76abc2bc6efe9789d))

### Unknown

* Merge pull request #4 from Krande/feat/startup-preflight-checks

feat: verify dependencies at startup and add a real readiness probe ([`461419d`](https://github.com/Krande/shelf/commit/461419d01764902dfd0e0d33906df98a0c3a77fa))


## v0.2.0 (2026-09-14)

### Chore

* chore: sync the frontend lockfile to the released version

package.json carries 0.1.0 and package-lock.json still said 0.0.0, so the
`npm install` that `up` runs on a fresh checkout rewrites the file and
hands the developer a dirty tree before they have changed anything.

deputy writes package.json but deliberately leaves the lockfile alone --
its version pattern matches once per dependency and would rewrite the
whole tree -- so the two drift apart at every release and this will need
doing again at 0.2.0. Worth the one line now; the alternative is every
fresh checkout starting dirty.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt;
Claude-Session: https://claude.ai/code/session_01DQRLj2HQmjeioEN3Gz4R4p ([`7a56236`](https://github.com/Krande/shelf/commit/7a56236a9f0315a091e58f41a2411feedba21400))

* chore: keep vite&#39;s output from killing the pump thread

vite announces itself with a U+279C arrow. Windows picks cp1252 for a
redirected stdout, which raises UnicodeEncodeError on it -- inside the
thread that echoes a server&#39;s output, so the traceback lands in the middle
of the log, that server&#39;s echo stops for good, and it goes on running with
nothing to show for it. The &#34;Open http://localhost:...&#34; line the reader is
waiting for is printed from the same thread and never arrives.

Reconfigures stdout and stderr to UTF-8 with errors=&#34;replace&#34;, so an
encoding a console cannot render costs a replacement character rather than
the rest of the session&#39;s output.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt;
Claude-Session: https://claude.ai/code/session_01DQRLj2HQmjeioEN3Gz4R4p ([`b9889e8`](https://github.com/Krande/shelf/commit/b9889e84e2631476a52c2cb5e7bf26936d9afdf1))

* chore: let the browser upload straight to the dev bucket

Dropping a PDF into the SPA failed with &#34;Upload failed: Failed to fetch&#34;,
and the console explained why:

    Access to fetch at &#39;http://localhost:3901/shelf/items/.../attachments/...&#39;
    from origin &#39;http://localhost:5174&#39; has been blocked by CORS policy:
    Response to preflight request doesn&#39;t pass access control check

(Bumped ports either side, from the commit before this one -- the failure
predates them and happens just the same on :3900 and :5173.)

Uploads and downloads go from the browser straight to a presigned URL, so
they are cross-origin by construction -- vite and the store are different
ports, and nothing about the stack can make them the same one. Garage
starts with no CORS rules on the bucket at all and answers the preflight
OPTIONS with 403 &#34;This CORS request is not allowed&#34;, which reaches the SPA
as a bare TypeError with nothing in it to point at a bucket.

`up` now PUTs a rule after the bucket step, for whichever store is running
-- not gated on the bootstrap above it, since garage makes its own bucket
and needs the rule just the same. The signer grew query-string and payload
support to sign ?cors; it was empty-payload, no-query before, which was
all CreateBucket needed.

Advisory, not fatal: a store that answers preflights permissively on its
own, or does not implement PutBucketCors, is not a reason to refuse to
start. It warns instead, so an upload that does fail later has something
to point at.

Verified against garage on a bumped port by driving the SPA&#39;s own path --
register, preflight, PUT, complete, download -- with the bytes coming back
identical. Preflight answers 200 where it was 403.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt;
Claude-Session: https://claude.ai/code/session_01DQRLj2HQmjeioEN3Gz4R4p ([`407f11a`](https://github.com/Krande/shelf/commit/407f11a7d0e3db58c98d74067af83705916fab48))

* chore: bump dev host ports that are already taken

`up` scanned for a free port for vite and uvicorn but published the
container ports as fixed numbers, so anything else on the machine holding
one took the whole stack down:

    Error response from daemon: driver failed programming external
    connectivity on endpoint shelf-garage-1: Bind for 0.0.0.0:3900
    failed: port is already allocated

Every one of them is a number other things want: :5432, :6379, :3000 and
:3900 are all defaults for what they run, so a second compose stack
elsewhere on the machine is enough. compose gives up on the first failed
bind, leaving the services that did start running and the developer with a
message that names a port but not what to do about it.

Each published port is now a ${VAR:-default} in compose.yaml, so a plain
`docker compose up` is unchanged, and `up` resolves them before it starts
anything: probe, and move to the next free number when something answers.
The choice is threaded through to everything that has to agree with it --
compose itself, ./.env so the compose commands `up` does not run address
the same containers, and SHELF_DATABASE_URL / SHELF_REDIS_URL /
SHELF_GOTENBERG_URL / SHELF_S3_ENDPOINT for uvicorn and alembic, set only
for the ports that actually moved.

A port one of our own containers already publishes is kept rather than
re-picked, which needs `docker compose ps` rather than the probe: the
probe cannot tell our garage from a stranger&#39;s, so every run would find
itself on :3900, bump to :3901, then find :3900 free next time and bump
back, recreating a working container each way.

One thing this does not reach: `pixi run test` reads backend/.env
directly, so a bumped Postgres needs SHELF_DATABASE_URL set there. `up`
says so when it happens rather than leaving the suite to fail on connect.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt;
Claude-Session: https://claude.ai/code/session_01DQRLj2HQmjeioEN3Gz4R4p ([`627cac4`](https://github.com/Krande/shelf/commit/627cac461b146002c75231edacfc86526a9e0327))

### Feature

* feat(storage): allow presigning against a separate public S3 endpoint

Presigned upload and download URLs are handed to a browser, but they were
signed against s3_endpoint - the address the *server* uses to reach the
bucket. When the browser and the API are on different networks those are
not the same host, and the signed URL is unusable: the browser cannot
resolve an internal service name, and an http:// endpoint is refused
outright on an https:// page as mixed content. Uploads fail with no
server-side error, since nothing reaches the server.

Add s3_endpoint_public, used only when signing URLs meant for a browser.
Empty (the default) means &#34;same as s3_endpoint&#34;, so single-network
deployments and dev are unchanged and keep a single store.

_build_store now takes the endpoint as an argument instead of reading it
from settings, so both stores share one construction - and, importantly,
one allow_http rule, which is derived from the endpoint being built rather
than from s3_endpoint. Without that, an http:// server endpoint would have
leaked the plaintext opt-in into an https:// browser store.

Split the two presign consumers that are not browser-facing back onto the
server-side endpoint: read_object (bulk-export ZIP assembly) and
stream_attachment (proxy download) both fetch the URL from this process,
so there is no reason to send that traffic out to a public host and back.
They now call presign_download_internal.

Tests cover the fallback cases (unset / identical / distinct) and that the
allow_http opt-in follows each store&#39;s own scheme.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt;
Claude-Session: https://claude.ai/code/session_01MgNYH1A9cGSt3nU71SJKyc ([`0d025ee`](https://github.com/Krande/shelf/commit/0d025ee59202b6ef9353b66e0a83a6a5d19821d6))

### Unknown

* Merge pull request #3 from Krande/feat/s3-public-presign-endpoint

feat(storage): allow presigning against a separate public S3 endpoint ([`59afbb4`](https://github.com/Krande/shelf/commit/59afbb400d285252e6c27f67eb2da13ff98ab1b1))

* Merge pull request #2 from Krande/chore/dev-stack-port-conflicts

chore: bump dev ports that are taken, and let the browser upload ([`d62b623`](https://github.com/Krande/shelf/commit/d62b6230dba61d477aea0cbefcb62ee9ab239eb3))


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
