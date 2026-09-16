# CHANGELOG



## v0.6.0 (2026-09-16)

### Feature

* feat(spaces): inherit another space, with standards, pinning and private notes

One shared Standards space holding one copy of each standard, read by
every project space and every person who wants them — plus the machinery
that makes that usable. The pieces interlock, so they land together.

Inheritance. A space subscribes to another and reads its items without
holding a copy. Two owners have to agree: the space being read opts in
once (Space.subscribable), and the space doing the reading adds the
subscription. It grants read and nothing else, it does not chain (A
inherits B, B inherits C — A does not see C), and the parent&#39;s owner
keeps a subscriber list they can revoke from. Inherited items, and the
collections that organise them, come across read-only and flagged, with
borrowed collections rendered as their own group per source space rather
than mixed in among the space&#39;s own.

Engineering standards. A new item type plus standard_families /
standard_revisions, so editions of one standard know they are the same
standard: a revision dropdown, and a badge saying whether this is the
current one. &#34;Latest&#34; is relative to the caller — the newest edition they
can actually open — because telling someone their copy is out of date and
then 404-ing the newer one is worse than staying quiet. is_latest_known
reports when the instance holds something newer than they can reach.
Undated and withdrawn editions never claim latest, since labels like
&#34;Rev. 5&#34; and &#34;2020&#34; cannot be compared to each other.

Pinning. A project pins the edition it builds to and its library stops
listing the siblings, with a toggle to reveal them. Owner-level, because
it changes what everyone else in the space sees by default.

Private notes and highlights. Once one document is read by the whole
company, &#34;everyone who can read this PDF&#34; is the wrong audience for a
working note. Both now carry an author and a visibility; on an inherited
document they start private, and sharing one publishes to the space that
owns the document — the other subscribers — not to the personal shelf it
was read from. Only the author can share or retract. Writing a note needs
only read access, which is the case the feature exists for.

Alongside, because each was a gap the above exposed:

  - copy an item to another space (bytes, tags and page text; not notes
    or highlights, which belong to whoever wrote them)
  - rename a space — previously impossible for anyone. Open to its owner,
    and to instance admins for shared spaces, which is a label change
    rather than a way in: an admin who renames a space still cannot list
    one item in it.
  - scope an API token to specific spaces, stored as ids rather than
    slugs since slugs are now renameable
  - set item metadata and file standards over the token API, so an import
    no longer ends with hand-typing in the SPA
  - attachments carry the SHA-256 of the bytes as uploaded, which is what
    lets two instances agree on a document that has no business identity
  - a resizable detail panel, remembered per browser

Two token-facing bugs fixed in passing: /api/v1/search and
/api/v1/collections bounded themselves with readable_space_ids, so an
inherited space was plainly visible in the SPA and invisible to scripts;
and copying an item dropped the attachment hash, making the copy look
like a different file to anything matching on content. ([`441a999`](https://github.com/Krande/shelf/commit/441a9991f54d7ab83d16262d647177c8dee59754))

* feat(admin): pre-provision a user from an email address

Nothing is synced from the identity provider, so a colleague assigned the
app in Entra still does not exist here until they have signed in once —
and until then they cannot be picked as a space member. Admins can now
create the account ahead of that from Settings -&gt; Admin.

The row is created with no Identity attached. On first sign-in
upsert_user_from_claims finds it by email (users.email is CITEXT, so case
does not matter) and links the provider identity onto it rather than
minting a second account. That match is on the address alone, so a
mistyped address leaves a stray empty account behind; nothing breaks, but
the form says so.

Extracts create_user_with_personal_space, which was the third copy of
&#34;add a user row and the personal space everything else assumes exists&#34;.
Keeping it in one place means a fourth caller cannot forget the space and
leave an account that can hold nothing. ([`71b3585`](https://github.com/Krande/shelf/commit/71b3585a2009a010f9d1d6e70ac7cb85259c177b))

* feat(cli): add a command-line client for the REST API

Modelled on deputy: src layout, hatchling, argparse, and a pixi-build
manifest so it installs in one command without a checkout —

    pixi global install shelf-cli --git &lt;repo&gt; --subdirectory cli --tag vX.Y.Z

It lives inside this repo rather than beside it so it is versioned with
the API it talks to; --subdirectory is what makes that installable on its
own. Config layers the way deputy&#39;s does, except the token is
deliberately not read from shelf.toml, because config files get committed
by accident.

Two things it does. `shelf items set` changes metadata fields without
curl, merging by default so setting one field doesn&#39;t drop the rest of a
document&#39;s record. And `shelf profiles push` sends **document profiles** —
metadata kept in JSON files, outside this repo — into an instance
whenever one is ready.

Pushing twice has to update rather than duplicate, and item ids can&#39;t be
the link because they differ per instance, which is the whole situation
profiles exist for. So matching walks three rules, most specific first:
an explicit item id, the document&#39;s own identity (body, designation,
edition) for a standard, then the SHA-256 of its first attachment. The
last is what makes this work for a report or a drawing, which have no
designation to be known by — there the bytes are the only thing two
instances can agree on. It sits below the business identity rather than
above because bytes can change while the document does not: publishers
stamp per-download watermarks, and shelf&#39;s own OCR rewrites blobs.

CI covered neither lint nor tests for a new package, so both are wired up
here. ([`228bec6`](https://github.com/Krande/shelf/commit/228bec609d9b72fb058bf84332fbfb0f84a99679))

* feat(dev): run NATS and a background worker in the local stack

`pixi run up` started Postgres, an object store and the two servers, but
no queue — so every uploaded PDF sat at extraction_status=&#39;pending&#39; with
nobody to tell, and full-text search never saw it. Add a JetStream NATS
service and a supervised worker process, and re-publish orphaned
attachments on each start so a stack that gains a worker catches up on
its backlog.

Consumers are chosen by what the machine can run: extract and outline are
pure Python, while ocr shells out to tesseract and ghostscript, which
pixi only installs on linux-64. It is skipped with a printed reason
rather than failing on the first scanned PDF someone tries.

Also takes over file watching from uvicorn&#39;s own reloader. That watcher
thread stops on Windows while the server keeps serving, so the stack
looks healthy and quietly answers with stale code — the worst shape this
can take, since nothing appears wrong until a route you just wrote
returns 404. Owning the watch costs a full process restart per edit and
buys a reload that either announces itself or has visibly failed.

Two supervisor bugs fixed alongside: a watcher-initiated stop was counted
as a crash and restarted a second time, exhausting the restart budget in
a handful of saves; and Server.start() could overwrite a live process
handle, orphaning a server that went on holding the port and pushing the
next run onto :8001. ([`e631814`](https://github.com/Krande/shelf/commit/e6318145563da4f7785492c5992283e75828bfd0))

### Fix

* fix(worker): start on Windows

`loop.add_signal_handler` is implemented on Unix only; asyncio raises
NotImplementedError from it on Windows&#39; ProactorEventLoop rather than
degrading. The worker therefore could not start at all on Windows —
it died during consumer setup, before fetching a single job, which is
awkward given that is where it gets developed against.

Fall back to `signal.signal`, hopping back onto the loop via
`call_soon_threadsafe` since that handler runs on the main thread
between bytecodes. Shutdown is then delayed until the current fetch
times out, which is FETCH_TIMEOUT at worst. ([`01986cf`](https://github.com/Krande/shelf/commit/01986cfbdf20810eb16b1961554b8a0b49f1a4c7))

### Unknown

* Merge pull request #7 from Krande/feat/space-roles-and-scoped-storage

feat: inherit shared spaces, with engineering standards, private notes and a CLI ([`e211663`](https://github.com/Krande/shelf/commit/e211663f314736198ae93e4864e11e6a67f2d48e))

* Merge branch &#39;main&#39; into feat/space-roles-and-scoped-storage ([`3e724cc`](https://github.com/Krande/shelf/commit/3e724cc6a812c1a1562434a6ccfb584b90777640))


## v0.5.0 (2026-09-15)

### Feature

* feat(reader): deep-link an annotation with ?annotation=&lt;id&gt;

Page was the finest thing a shelf URL could address. ?find= looks like an
anchor but is a search: it lands on the first textual match, or nowhere
at all if OCR mangled the word, and nothing in the URL says which
occurrence was meant.

Annotations are the one sub-page object shelf already identifies. They
carry a stable id and rects in PDF user-space, chosen so they survive
zoom and DPI differences, so they resolve to the same passage for
everyone who can open the space.

  * ?annotation=&lt;id&gt; opens the document at that highlight and rings it,
    gated on heightsReady for the same reason ?page= is - scrolling
    before the virtualizer can measure lands on the wrong offset.
  * Copy link in the highlights panel produces the URL, falling back to
    a prompt where the clipboard API is unavailable (it needs a secure
    context, which a plain-http instance has not got).
  * The ring is drawn with outline, not border, so it costs no layout
    and cannot nudge the rect out of alignment with the glyphs beneath.
    prefers-reduced-motion keeps the ring and drops the pulse.

Tables, figures and equations stay unaddressable: nothing extracts them
as objects, so there is no id to put in a URL. ([`87041cd`](https://github.com/Krande/shelf/commit/87041cd46421769629b1b70b65557aad278d259c))

* feat(spaces): pick members from a directory of registered users ([`2a304ab`](https://github.com/Krande/shelf/commit/2a304aba1f5852c8dc018f74959c250ba412a3c3))

* feat(spaces): let admins create shared spaces ([`ab724ef`](https://github.com/Krande/shelf/commit/ab724ef46bdae1c60f444b1f5b08a381031f1976))

* feat(auth): add SHELF_DEV_LOGIN_ROLE so local dev has an admin ([`4a8857e`](https://github.com/Krande/shelf/commit/4a8857e47c57a2ba2120f35edab520cb8b5db27a))

* feat: add per-space roles, space-scoped storage keys and a configurable subject claim

Spaces have been &#34;the unit of ownership and sharing&#34; in name only: the
schema carried a space_memberships table since 0001 that nothing read or
wrote, and every permission check in the app was the same line repeated
in nine routers - does the caller own the space that owns this row?

Per-space roles

  viewer reads, editor writes, owner also manages membership. The
  repeated ownership check becomes auth/spaces.py, and each endpoint
  declares the role it needs, so a read path and a write path in the
  same router no longer share one answer.

  Space.owner_id stays authoritative for the creator: always owner, no
  membership row, nothing to delete that would lock them out. Owner is
  therefore not assignable through the API.

  A caller with no role gets 404 - a space you cannot see should not be
  confirmed to exist. A caller whose role is merely too low gets 403,
  naming the role required, since they already know it is there.

  Instance admins get nothing here. Handing out roles and reading
  everybody&#39;s library are different powers, and conflating them would
  make the admin role far more dangerous than it looks.

  Bulk operations take writable_space_ids rather than readable: a rescan
  or an orphan sweep that quietly included read-only spaces would be a
  privilege escalation even with every individual endpoint correct.

Space-scoped storage keys

  New attachments are written to spaces/{space_id}/items/{item_id}/... so
  a bucket policy, lifecycle rule or per-space sweep can address one
  space&#39;s objects without consulting the database.

  New registrations only. Keys are stored per row, so anything written
  under the older flat items/{id}/... layout keeps working untouched -
  no rewrite, no dual-read path. Derivation and .original keys extend
  the parent key, so they follow whichever layout their attachment has.

Configurable subject claim

  OIDCProvider gains subject_claim, defaulting to &#34;sub&#34;. Azure AD needs
  &#34;oid&#34;: its sub is pairwise, a different value per application
  registration, so the same person looks like a different subject to
  every app. Falls back to sub when the configured claim is absent
  rather than locking those users out.

Also: membership management under Settings -&gt; Spaces, /api/me/spaces now
reports the caller&#39;s role per space and includes spaces they were added
to, and the library hides write controls in a read-only space. ([`d18e37c`](https://github.com/Krande/shelf/commit/d18e37cdacaac9ba31b5bda32735efe97dacaf6d))

### Fix

* fix(auth): make the link prompt per-provider and handle provider errors ([`d0f22ec`](https://github.com/Krande/shelf/commit/d0f22ec85a4dbe10e06746c27e42f578ec31143d))

### Unknown

* Merge pull request #6 from Krande/feat/space-roles-and-scoped-storage

feat: add per-space roles, space-scoped storage keys and a configurable subject claim ([`9560c28`](https://github.com/Krande/shelf/commit/9560c283741b5bedf34cc0e1bbcb681dce969b9c))

* refactor(auth): move session JWTs off the deprecated authlib.jose

authlib warns that authlib.jose will not survive its 2.0, and joserfc is
already a declared dependency. Moving session.py across also lets two
things be tightened that the old call could not express:

  * the algorithm is pinned at verify time rather than read from the
    token&#39;s own header, so a forgery cannot choose how it is checked;
  * exp is now an essential claim, so a token issued without one is
    rejected instead of never expiring.

Note this does NOT silence the startup notice: authlib emits it from its
own internals (_joserfc_helpers imports authlib.jose), so it stays until
authlib 2.0 lands. What it does mean is that shelf itself no longer
depends on the removed API. ([`94fa7fe`](https://github.com/Krande/shelf/commit/94fa7fe4c54b719642bcb2a5f02202c8110c1e25))


## v0.4.0 (2026-09-15)

### Feature

* feat: add admin/user roles and multi-account switching

Shelf had no global role: every authorization check was &#34;does the caller
own the space that owns this row?&#34;, and api/extraction.py said so out
loud. It also carried exactly one identity per session, so someone with
two OIDC accounts had to sign out and back in to move between them.

Roles

  users.role (&#39;admin&#39; | &#39;user&#39;), TEXT with a CHECK rather than a boolean
  so a third role needs no migration. Named role, not scopes:
  api_tokens.scopes already owns that word for bearer permissions.

  SHELF_ADMIN_EMAILS promotes on login and never demotes, so in-app
  changes stick and editing the env cannot strip anyone. `pixi run
  grant-admin &lt;email&gt;` is the escape hatch when a restart is not
  practical. Admin is cookie-session only; no token scope grants it.

  The role is re-read from the database per request rather than baked
  into the session, so a demotion lands on the next request instead of
  at session expiry.

Account switching

  The session JWT gains an `accts` claim listing every identity linked
  to this browser session. It is inside the signature, and only a
  completed OIDC callback can add to it, so a cookie cannot be edited
  into reaching an account its owner never authenticated as.

  /auth/link/{provider} sends prompt=select_account, without which a
  provider with an active session signs the same account straight back
  in and linking a second one is unreachable. Switching re-uses the
  original expiry: sessions are stateless and unrevocable, so sliding it
  on every switch would make one immortal.

  /api/me reports each account&#39;s idps, letting the SPA send &#34;switch
  user&#34; to the right provider&#39;s picker instead of asking which.

Settings

  Rebuilt as tabs - Account, Appearance, Documents, API tokens, and
  Admin for admins. The tab is a route segment, so it is linkable and
  survives the reload the switcher performs. /admin redirects to
  /settings/admin.

Frontend testing

  The SPA had no tests and no CI at all. Adds vitest + Testing Library
  on jsdom (62 tests) and a workflow running typecheck and the suite.
  Hard navigations move into lib/navigation.ts, both to document the
  &#34;reload, do not router-navigate&#34; decision in one place and because
  jsdom cannot otherwise intercept them. ([`41f5f5d`](https://github.com/Krande/shelf/commit/41f5f5d6adc824fc25a482798c36c5ba2b1de16d))

### Unknown

* Merge pull request #5 from Krande/feat/user-roles-and-account-switching

feat: add admin/user roles and multi-account switching ([`c702c88`](https://github.com/Krande/shelf/commit/c702c8836d895e14cef6388112cc5ea113dee0f1))


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
