# CHANGELOG



## v0.10.0 (2026-09-25)

### Feature

* feat(search): search every space you can read, with a session-return and title-alignment fix (#12)

Co-authored-by: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt; ([`f2347d2`](https://github.com/Krande/shelf/commit/f2347d25df4e6bf7b2409e995e00fe5d7cc94389))


## v0.9.1 (2026-09-21)

### Fix

* fix(account): send Settings&#39;s &#34;Switch user&#34; to the provider, not a list (#11)

Co-authored-by: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt; ([`902a573`](https://github.com/Krande/shelf/commit/902a573ede1e0afc0c6b8dfe2155b07d066769b9))


## v0.9.0 (2026-09-18)

### Feature

* feat(perf): make the PDF reader usable on long documents, and adopt pdf.js&#39;s viewer controls (#10)

Co-authored-by: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt; ([`6b06607`](https://github.com/Krande/shelf/commit/6b0660766e9154e871d1b0700349a7e94b5d2cdd))


## v0.8.0 (2026-09-18)

### Feature

* feat(search): open the PDF from a search result, not the record

Picking a result from the landing page opened the item&#39;s detail panel in
the library, so reading the document a search had just found took a
second click every time. A search result is a thing to read.

Enter and a plain click now open the PDF -- and for a full-text passage,
at the page it matched, with the find bar filled in, which is the whole
reason the passage is on screen. Shift reaches the detail panel, and the
Info button on each row already did and still does, so nothing is out of
reach.

The snippet rows lose their coarse-pointer special case: a tap and a
click now mean the same thing, so there is nothing left to special-case.

The shortcuts reference gains a Search section covering these, plus the
arrow keys that unfold a result&#39;s matching pages.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt; ([`0148691`](https://github.com/Krande/shelf/commit/01486910560038fdf5698d6cb76a4278fd3b2be7))

* feat(library): resizable columns, and a shortcuts reference in the header

Column widths drag from a strip on each header&#39;s right edge, remembered
per browser. The strip stops its own events, or grabbing the edge of a
sortable column would re-sort it; arrow keys nudge and Home or a
double-click puts one column back. The table turns table-fixed so the
widths are honoured, with a minimum equal to their sum so a wide layout
scrolls rather than squeezing back down.

An (i) in the header opens what the keyboard and the mouse can do.
Ctrl-click to open a PDF, Backspace out of the reader, drag a row onto a
collection, the modifier that arms a PDF&#39;s own hyperlinks, and opening a
search hit straight at its page are all invisible until someone tries
them, which makes them shortcuts nobody finds.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt; ([`982b6ff`](https://github.com/Krande/shelf/commit/982b6ffc416362f8ef4535d94eee34a1a824e23c))

* feat(library): nest new collections, and label subcollections by path

The + button now creates inside whatever collection is open rather than
always at the root: making a folder while inside another almost always
means making it there. Nothing open, Unfiled, or an inherited collection
-- which the API refuses a child -- still puts it at the root. The
folder menu gains &#34;Add subcollection&#34; for the same thing without having
to open the folder first, and the parent is expanded afterwards so the
new child is not created inside a folded folder.

The subcollection section drops the indented per-level headings for one
line per folder, labelled with its path: &#34;Reports &gt; Drafts (1)&#34;. A
folder holding nothing of its own no longer gets a row -- it would be a
heading over nothing -- and its name reaches the reader through the path
on the folders beneath it instead.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt; ([`913a507`](https://github.com/Krande/shelf/commit/913a507b782cd1b5439545026895338ec08e0aab))

* feat(library): nest the subcollection section, and open it on a sparse folder

The section was one flat list. It now mirrors the rail: a group per
subcollection, in sibling order, depth first, indented by depth. A
document filed in two subcollections shows under both -- the header
count is of distinct documents, the groups are of where they are.

Branches holding nothing at any depth are pruned. A folder that is empty
itself but has a child with documents is kept: dropping it would leave
the child looking like a direct child of the open collection, detached
from the path that explains where it lives. The pruning lives in lib so
those rules are testable without a rendered page.

The section also opens on its own when the folder holds fewer documents
of its own than a new Options tab&#39;s threshold, default 5 -- a folder
with a handful has room to show what is below it, and an empty one that
says &#34;no items&#34; while its subcollections hold the documents is the case
the section exists for. Clicking the header still wins, and the override
is dropped when the rail selection changes.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt; ([`bb75a5c`](https://github.com/Krande/shelf/commit/bb75a5c9a583978e8cc9e14354e7403caf04a985))

* feat(library): drag documents onto a collection to file them

Rows are drag sources carrying a JSON array of item ids, and folders in
the rail accept them. Dragging a row that is part of the checked
selection carries the whole selection; dragging one that is not carries
just that row, so a forgotten selection cannot come along by surprise.

Additive, like the bulk &#34;Add to collection&#34; action: filing into a folder
does not move the document out of the others, which is why the drop
effect is copy rather than move. An inherited folder takes no drop -- it
belongs to another space and the API refuses the write.

The payload rides its own MIME type, so a folder being reordered and a
document being filed stay distinguishable: a document drop targets the
whole row and only ever means &#34;into&#34;, with no before/after to aim at.

Also fixes the subcollection section never appearing for a folder with
no documents of its own -- the usual shape of a parent whose documents
live a level or two down. The table only rendered when the folder&#39;s own
listing had rows, so that case showed &#34;no items match the current
filters&#34; and never reached the section listing them. The table now
renders whenever there is anything to put in it, and the empty-state
message waits until both listings are empty.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt; ([`f684bd4`](https://github.com/Krande/shelf/commit/f684bd41b491f892fdb7ecfbe793800fa5399925))

* feat(library): show a collection&#39;s subcollection documents below its own

Opening a folder that has subcollections now lists its own documents
first and offers everything filed below it -- to any depth -- in a
collapsible section underneath, collapsed by default.

Two listings rather than one widened filter, so neither loses its
identity: the listing endpoint takes collection_scope=subcollections,
which walks the tree with a recursive CTE and excludes the parent&#39;s own
members, keeping the two sets disjoint. EXISTS rather than a join, or an
item filed in two subcollections would come back once per subcollection.

The section&#39;s rows are ordinary rows, so selection, ctrl+click, Enter
and the arrow keys reach them without knowing where they came from. Id
lookups go through every loaded item for the same reason. &#34;Select all&#34;
and the bulk toolbar act on what is on screen: a collapsed section is
not, so its items are not swept into an action whose scope nobody can
see.

Nothing is fetched for a folder without children.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt; ([`79e0e58`](https://github.com/Krande/shelf/commit/79e0e5889cbce1150d6e366a2db80e6eb4fcac50))

* feat(library): copy a selection to another space, and choose where it lands

&#34;Copy to space&#34; sits next to &#34;Add to collection&#34; on the bulk toolbar and
copies every checked item. Copies run one at a time so a refusal names
the item it belongs to, and one refusal -- an item already in the target
is the common case -- no longer strands the rest of the selection.

Both the bulk action and the detail panel&#39;s copy button now ask which
collection in the target the copy should land in. Copying still does not
translate the source&#39;s folders, since they mean nothing on the other
side; this is a choice about where it arrives, made while the copy is
being set up rather than by hunting for it afterwards. Leaving it unset
keeps the old behaviour and the copy lands unfiled.

Only the target&#39;s own collections are offered: an inherited one belongs
to another space and is read-only there. A collection that is not the
target&#39;s is a 404, and the copy is abandoned rather than quietly landing
unfiled.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt; ([`8df0085`](https://github.com/Krande/shelf/commit/8df0085ec6554592259e6dbf2cb5989227475886))

* feat(library): download a collection&#39;s PDFs as one zip

&#34;Download PDFs&#34; on a collection&#39;s menu, using the archive endpoint the
bulk-select action already uses -- same assembly, same current-best
version resolution, same _MISSING_FILES.txt and X-Shelf-Skipped when a
blob cannot be fetched.

The endpoint takes `collection=` rather than the client expanding the
folder into one `item=` per document, which would build a query string
long enough to be refused on a folder of a few hundred files. The two
selectors are mutually exclusive.

Direct members only, no descendants: the library lists a collection the
same way, so the archive is what is on screen. Trashed members are left
out. A collection belonging to another space is a 404, not an empty
archive.

Not offered on inherited collections -- their menu is hidden because
every other entry is a write the API refuses, and the endpoint bundles
only the space&#39;s own items.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt; ([`9429b84`](https://github.com/Krande/shelf/commit/9429b84d504afe005f365f9b0400a822fe6c69f8))

### Fix

* fix(items): annotate the descendant-collection query&#39;s return type

mypy runs over the backend in its own CI job and wants every function
annotated; the recursive CTE helper went in without one.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt; ([`3a27c4f`](https://github.com/Krande/shelf/commit/3a27c4f1a81f351c9bd8c76508f58eb12c23df68))

* fix(reader): stop the find highlight re-scrolling on every re-render

Escaping out of a search scrolled the page, and so did zooming. Same
cause, and not the one the previous commit fixed.

The effect that draws the find highlights ends by scrolling the current
match into view, and it re-runs whenever the text layer is rebuilt --
which happens on any change of render scale. Closing the find bar
resizes the scroll container, so every page re-rendered and the view
snapped back to the match just left behind. Zooming does it too, with a
delay that made it look unrelated: oversample only changes once the
gesture settles, and that rebuild fires the same scroll.

It now scrolls only when the target match actually moved, tracked per
page. A query cleared to nothing resets that, so searching the same word
again still takes you to it.

Escape also clears the query now, as the X button already did. A query
left behind keeps its highlights on the page with no visible control
left to clear them.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt; ([`e801d5f`](https://github.com/Krande/shelf/commit/e801d5fabf51ddf379cd8d8a50f97853a206ea23))

* fix(reader): stop a stale ?page= reclaiming the view, and tame wheel zoom

Closing the find bar threw the reader back to the page the deep link
named. The ?page= writeback uses replaceState, which react-router never
sees, so its copy of the param stays frozen at whatever opened the
document -- while the effect that honours it also depends on layout
state that changes much later, since opening or closing the find bar
resizes the scroll container and flips heightsReady. Follow a hyperlink
to page 40, close the find bar, and it re-applied page 5. It is now
honoured once per navigation, keyed on the location key so arriving at
the same page from two different search hits still scrolls.

Wheel zoom moved in enormous steps. The exponential was tuned for a
trackpad pinch&#39;s small deltas; a mouse notch arrives as 100px in one
event, which through exp(-dy * 0.01) is a 172% jump. Normalising the
delta modes and clamping puts a notch at about 10% while leaving a
pinch&#39;s small deltas fine-grained, and the factor is now a pure
function with tests, since &#34;how big is one notch&#34; is the whole
complaint.

Zooming also walked the page sideways: the horizontal anchor followed
the cursor, so a few notches pushed the page off the viewport. There is
only one page across, so there is nothing to the side worth anchoring
on -- it now stays centred horizontally while the point under the
cursor still holds vertically, which is what zooming into a figure
needs.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt; ([`0069791`](https://github.com/Krande/shelf/commit/0069791ee64d7cd536ddc25c17f461e9a3047d19))

* fix(library): make column widths real, and keep the scope filter in place

Three things the last commit got wrong, and one older one.

Columns did not clip. A fixed column constrains the box, not the text
inside it, so a title simply spilled past the divider and narrowing a
column changed nothing visible. Cells truncate now, with the full value
on the title attribute.

Dragging one divider moved every column. Under table-layout: fixed a
table wider than the sum of its columns spreads the surplus across every
sized column in proportion, so each drag nudged all of them -- including
the header label, which is why &#34;Title&#34; appeared to float. An unsized
trailing column swallows the surplus instead, and each declared width is
now the width you get.

The shortcuts reference was only in the app header, which the landing
page does not use -- it has its own nav. It sits there too now.

And the landing page&#39;s scope filter navigated away instead of opening:
its trigger had no type, so inside that page&#39;s search form it defaulted
to submit and ran the search. One attribute, with a test, because the
next button added to a form will make the same mistake.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt; ([`58dbc7e`](https://github.com/Krande/shelf/commit/58dbc7e701dd67549f4fc89bfc6f3ef76706d3d3))

* fix(tests): run the suite against its own database

conftest&#39;s client fixture TRUNCATEs every core table, and the default
SHELF_DATABASE_URL names the database `pixi run up` serves. Running the
suite against a live dev stack therefore wiped its data and logged out
its sessions, with nothing in the output to say what had happened.

The URL is now rewritten to a sibling ending in `_test` before shelf.db
builds its engine, so the app under test and the TRUNCATE address the
same throwaway database and no environment variable can aim them at a
real one. It is created and migrated on first use, so a clean checkout
needs no setup step and a new migration needs no separate command.

test-db-migrate and the CI migrate step go with it: both brought up the
database the suite no longer uses.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt; ([`098613a`](https://github.com/Krande/shelf/commit/098613a556a6dd3394d573cc0e224361b6babc02))

* fix(library): drop the collection filter when switching space

Collection ids are per-space, but the library kept ?collection= in the
URL across a space switch. The new space&#39;s listing was then filtered by
a folder it does not have, which matched nothing and returned 200 with
an empty list -- indistinguishable from an empty space, and invisible in
the server log.

It showed up after copying an item between spaces: copy does not carry
collections across (the target&#39;s folder tree is its own), so the copy
landed unfiled and the stale filter hid it. The copy itself was fine.

Switching space now drops `collection` and `item` in one write, both
being scoped to the space being left.

The listing also stops accepting a collection it cannot see: not found
is a 404 rather than an empty result. The check is against the space
plus everything it inherits, since an inherited item brings its own
space&#39;s collections with it and filtering by one of those is legitimate.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt; ([`5851566`](https://github.com/Krande/shelf/commit/58515662a883d71fe2731843f18e715ed389191a))

### Unknown

* Merge pull request #9 from Krande/fix/collection-scope-and-download

feat: collection tooling, search-to-PDF, reader fixes, and test isolation ([`baa7dde`](https://github.com/Krande/shelf/commit/baa7dde37cb984dc90bd02192b4e440b52f50391))


## v0.7.0 (2026-09-17)

### Feature

* feat(reader): follow the hyperlinks inside a PDF

Link annotations are extracted per page and drawn as an overlay. They
stay pointer-events: none until Ctrl/Cmd is held, so a clickable
rectangle over the text never swallows the drag that starts a selection.
Held down, links tint and become clickable: internal destinations scroll
the reader, external URLs open in a new tab.

Backspace leaves the reader, the counterpart to Enter in the library.

destToPage moves out of OutlinePanel into lib/pdfLinks alongside the new
extractor -- a link annotation&#39;s dest and an outline node&#39;s dest are the
same thing -- which also puts the fiddly parts under test: named versus
explicit destinations, rects given as either pair of opposite corners,
and links that resolve to nothing followable.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt; ([`e4b2713`](https://github.com/Krande/shelf/commit/e4b2713b1d932a2f58f0510f0098671f9921f4c6))

* feat(library): multi-PDF upload, and open a document from the list

Upload takes a whole selection and each file becomes its own document.
Uploads run sequentially so a failure names the file it belongs to, and
one bad file no longer strands the batch: its own item is rolled back
and the failures are reported together at the end.

Ctrl/Cmd-click a row, or press Enter on it, to open its PDF directly.
Up/Down move the selection. The rows are walked in the order they are
painted, so the table body is now built as data rather than inline in
the JSX -- a second construction of that order would drift from what is
on screen.

The selected item moves from component state into ?item=, reusing the
param the landing page already deep-links to. That is what makes Back
work from the reader: the history pop restores the panel along with the
collection, search and sort that led there.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt; ([`8c0d6f2`](https://github.com/Krande/shelf/commit/8c0d6f2650c02ffeff8fb5a448b9c160bf967c92))

* feat(admin): edit a user display name

PATCH /api/admin/users/{id} becomes a partial update: role and
display_name are both optional, so the role select and the name field
each send only what changed. A blank name is a 400, an empty body is a
400, and a refused last-admin demotion aborts the rename alongside it.

Nothing syncs names from the identity provider, so a correction sticks.

Renames the settings tab from &#34;Admin&#34; to &#34;Users&#34;: it renders only the
user list, and every other tab is named for its content rather than the
role needed to see it. The route segment is unchanged.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt; ([`9d0aadd`](https://github.com/Krande/shelf/commit/9d0aadd194459bf634a5818143b51f9faf5a690e))

### Fix

* fix(release): bump the CLI version with the rest

cli/pyproject.toml and cli/src/shelf_cli/__init__.py were not release
targets, so the CLI sat at 0.4.0 while the project reached 0.6.0. It
lives in this repo to stay versioned with the API it talks to, and
`pixi global install shelf-cli --tag v&lt;x&gt;` only picks the right client
if the two agree. Adds both to deputy.toml and syncs them to 0.6.0 so
the next bump starts from the right number.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt; ([`dc382ed`](https://github.com/Krande/shelf/commit/dc382eddeb33d7d1fbe3cc8cf4e199303e497c30))

* fix(ui): keep the header and space rows inside a phone viewport

The account trigger shows the user icon instead of the display name
below sm -- the name was the widest thing in the header. The trigger
keeps title={email}, so the identity is still one hover away.

Space rows wrap instead of overflowing, and their panel buttons drop
their text labels below sm: three shrink-0 buttons alongside the space
name pushed the row past its container. aria-label carries the name.

Co-Authored-By: Claude Opus 5 (1M context) &lt;noreply@anthropic.com&gt; ([`2fa594e`](https://github.com/Krande/shelf/commit/2fa594eb59e6362636ae3a03b8fc63195c06da26))

### Unknown

* Merge pull request #8 from Krande/feat/admin-names-bulk-upload-reader-nav

feat: display-name editing, multi-PDF upload, and reader navigation ([`4e40f7d`](https://github.com/Krande/shelf/commit/4e40f7d88f603a5840f107f3497daf9cc6b04764))


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
