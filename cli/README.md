# shelf-cli

Command-line client for a [shelf](../README.md) instance. Drives the
`/api/v1` REST surface with a token, and pushes **document profiles** —
metadata kept in files — into an instance whenever one is ready.

```sh
pixi global install shelf-cli \
  --git https://github.com/Krande/shelf.git --subdirectory cli --tag v0.4.0
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

```sh
pixi run cli-test
```

The tests fake the client, so they need no server and no database.
