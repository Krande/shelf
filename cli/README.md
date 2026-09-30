# shelf-cli

Command-line client for a [shelf](../README.md) instance. Drives the
`/api/v1` REST surface with a token, and pushes **document profiles** —
metadata kept in files — into an instance whenever one is ready.

```sh
pixi global install shelf-cli \
  --git https://github.com/Krande/shelf.git --subdirectory cli --tag v0.13.0
```

It lives inside the shelf repo rather than beside it so it's versioned
with the API it talks to; `--subdirectory` is what makes that installable
on its own.

## Configuring it

Three layers, most-specific first:

```
CLI flag / env var   ->   shelf.toml   ->   built-in default
```

```toml
# shelf.toml, next to your profiles
[instance]
base_url = "https://shelf.example.com"
space = "standards"          # default target for pushes
```

The **token is never read from `shelf.toml`** — config files get
committed by accident. It comes from `SHELF_API_TOKEN` or `--token`.

```sh
export SHELF_API_TOKEN=shelf_…
shelf whoami
```

## Searching and browsing

`shelf search` finds what the landing page finds — titles, creators,
abstracts and **PDF body text**, title hits first:

```sh
shelf search "load case"                        # JSON, one row per document
shelf search "load case" --scope fulltext --hits 5   # + each row's first 5 matching pages
```

`shelf browse` is the same search as a terminal UI. Type to search; each
document is listed with the PDF pages it matched underneath.

| key     | does                                                    |
|---------|---------------------------------------------------------|
| `↓` `↑` | move between the search box and the results            |
| `enter` | open the highlighted page in the shelf reader          |
| `o`     | open the PDF locally at that page (`O` re-downloads)   |
| `space` | show every matching page of a document, or fold it up  |
| `c`     | spaces & collections sidebar (shown by default when wide) |
| `→` `←` | in the sidebar: open / close a branch, step in / out   |
| `backspace` | from the results: up one level (collection → space → all) |
| `/`     | back to the search box · `esc` clears it · `q` quits   |

Picking a space or collection in the sidebar makes it where searches
look. With an empty search box a collection lists what is filed directly
in it, like opening a folder; type a query and it searches everything
nested below it too. The same narrowing is on the plain command:

```sh
shelf search "load case" --in-space standards
shelf search "load case" --collection <id> --subtree
```

Opening a web hit needs the `search` scope; opening locally needs
`download` too. `shelf open <attachment-id> --page N [--web]` does either
from a script.

### Opening a PDF at a page

No operating system has a general way to open a file *at a page* —
every viewer spells it differently. What several agree on is the
`#page=N` fragment on a URL, which Chrome, Edge, Firefox and their
relatives honour on a local file. So by default `o` downloads the PDF
once into a cache (`%LOCALAPPDATA%\shelf\pdf`, or `~/.cache/shelf/pdf`)
and opens `file:///…#page=N` in your **default browser**.

To use a dedicated viewer instead, give a command template in
`shelf.toml` (or `SHELF_PDF_VIEWER`):

```toml
[viewer]
pdf = 'SumatraPDF.exe -page {page} "{path}"'   # Windows: quote {path} yourself
# pdf = "okular -p {page} {path}"              # Linux: each word is one argument
# pdf = "evince -i {page} {path}"
# pdf = "zathura -P {page} {path}"
```

On Linux, a browser installed as a snap or flatpak may not be allowed to
read hidden directories like `~/.cache`; point `SHELF_CACHE_DIR` at a
visible one (e.g. `~/Documents/shelf-cache`) if the PDF doesn't load.
macOS has no browser lookup yet, so there it opens in the default PDF
app and tells you the page.

## Setting fields on a document

```sh
shelf items set <item-id> \
  --set standardBody="NX Standards" \
  --set designation="NX-ACME 1234" \
  --set numberOfPages=74          # parses as a number; anything else stays a string
shelf items set <item-id> --unset supersedes
```

Fields are **merged** by default, so setting one doesn't drop the rest of
a document's metadata. `--replace` swaps the whole blob instead.

## Document profiles

A profile is one JSON file describing one item. Keep them wherever the
real metadata is allowed to live — a share, a private repo, beside the
PDFs — and push when an instance exists:

```json
{
  "space": "standards",
  "item_type": "standard",
  "data": {
    "title": "Guidance on the design of widgets — Part 2",
    "standardBody": "NX Standards",
    "designation": "NX-ACME 1234",
    "edition": "2015+A2:2020+NA:2020",
    "nationalAnnex": "NA:2020 (Ruritania)"
  },
  "revision": {
    "body": "NX Standards",
    "designation": "NX-ACME 1234",
    "label": "2015+A2:2020+NA:2020",
    "issued_on": "2020-10-01"
  },
  "attachments": [{ "path": "./widgets-part-2.pdf" }]
}
```

```sh
shelf profiles push ./profiles/ --dry-run   # say what would happen
shelf profiles push ./profiles/
shelf profiles pull <item-id> -o widgets.json   # capture what's already there
```

Pushing twice updates rather than duplicates. Attachment paths are
relative to the profile file, so a directory of profiles and PDFs can be
moved or copied whole.

### How a profile finds its document

Item ids can't do it — they differ per instance, which is the whole point
of keeping profiles in files — so matching walks three rules, most
specific first:

1. **`match.item_id`**, when the profile names one. Pins to a single
   instance; useful for a one-off, useless for portability.
2. **`revision`'s (body, designation, label)** — the *document's* own
   identity. Survives the file being replaced: a re-download, an OCR
   pass, a corrected printing.
3. **the SHA-256 of the first attachment** — *content* identity.

Rule 3 is what makes this work for everything that isn't a standard. A
report or a drawing has no designation to be known by, and then the bytes
are the only thing two instances can agree on: push the same file to two
of them and both compute the same hash. It's second to rule 2 rather than
first because bytes can change while the document doesn't — publishers
stamp per-download watermarks, and shelf's own OCR rewrites blobs — so
where a business identity exists it's the more durable of the two.

A profile with none of the three is created on every push. That's
deliberate rather than hidden: guessing identity from a title is how
importers produce silent duplicates.

### Attachments

Files already on the item are skipped, matched on SHA-256 — so re-running
a push uploads nothing, and a file whose *contents* changed is re-sent
even though its name didn't. Declare `"sha256"` on an attachment and the
profile can be pushed without the file beside it, as long as the instance
already has those bytes. `"force": true` re-uploads regardless.

## Developing

See [DEVELOPERS.md](../DEVELOPERS.md#cli) — `pixi run cli-test`, which
needs no server and no database.
