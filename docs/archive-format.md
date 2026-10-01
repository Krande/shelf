# Shelf archive format (`shelf.archive`)

A Shelf archive is the ZIP that **Download PDFs** produces: from a selection
of documents, a collection, or a whole space. It holds the PDFs and an
`index.json` that makes the archive re-importable. **Import archive** reads
it back into any space, on this instance or another.

- **Current version:** 1.0
- **JSON Schema:** [`schemas/shelf-archive-1.schema.json`](schemas/shelf-archive-1.schema.json),
  generated from the reference models in
  [`backend/src/shelf/archive_format.py`](../backend/src/shelf/archive_format.py)

## ZIP layout

```
alpha.pdf                  ← documents filed in the exported collection itself
Mid/both.pdf               ← one folder per subcollection
Mid/Leaf/beta.pdf
_MISSING_FILES.txt         ← only if some PDF couldn't be read from storage
index.json                 ← always the last entry
```

- **Folders:** for a collection, its own documents sit at the root and each
  subcollection is a folder. For a whole space, each top-level collection is
  a folder, and documents filed in no collection sit at the root. A
  selection of documents is flat.
- **A document in several collections** has its PDFs written once, under the
  shallowest of them. The index still lists every membership.
- **Entries are stored, not deflated.** Each one carries a data descriptor,
  because the archive is streamed as it is built. Any ZIP reader handles
  this. A reader may also accept an archive that was re-compressed.
- **Names are not authoritative.** Folder and file names are sanitised and
  de-duplicated (`report (2).pdf`). A reader locates files by the `path` the
  index gives, never by parsing names.

## `index.json`

```jsonc
{
  "format": "shelf.archive",
  "version": "1.0",
  "created_at": "2026-10-01T12:00:00Z",
  "generator": "shelf 0.17.0",
  "source": { "space": { "id": "…", "slug": "lib", "name": "Library" } },
  "scope": "collection",              // "collection" | "space" | "items"
  "root_collection_id": "…",          // scope "collection" only, else null
  "collections": [
    { "id": "…", "parent_id": null, "name": "Top", "description": null, "folder": "" },
    { "id": "…", "parent_id": "…",  "name": "Mid", "description": null, "folder": "Mid" }
  ],
  "tags": [ { "name": "Physics", "color": "#ff0000" } ],
  "items": [
    {
      "id": "…",
      "origin_id": null,
      "item_type": "report",
      "data": { "title": "Alpha", "creators": [ … ], "date": "2024" },
      "tags": ["Physics"],
      "collection_ids": ["…"],
      "files": [
        {
          "path": "alpha.pdf",
          "filename": "alpha.pdf",
          "content_type": "application/pdf",
          "size": 1024,
          "sha256": "…",
          "version": "original"
        }
      ]
    }
  ],
  "missing": [ { "item_id": "…", "filename": "lost.pdf" } ]
}
```

| Field | Meaning |
|---|---|
| `collections` | The exported tree, parents before children. `id`/`parent_id` are the source's ids and only tie things together *within* the archive. `parent_id` is null for a top-level collection of the archive. `folder` is the ZIP folder of the PDFs filed directly in it. |
| `items[].id` | The document's id where it was exported from. |
| `items[].origin_id` | The document it was first imported from, when it was itself imported. Lets a chain of export → import → export → import recognise a document. |
| `items[].data` | Shelf's metadata, stored as-is: Zotero-style keys (`title`, `creators`, `date`, …) plus per-type fields. Opaque to the format. |
| `items[].collection_ids` | Every archive collection the document is filed in. |
| `items[].files` | Its PDFs in the ZIP. `size` and `sha256` describe the bytes in the ZIP. `version` says which version of the attachment they are: `original`, or the derivation (`ocr`, `outline`) that superseded it. |
| `missing` | PDFs that belonged in the archive but couldn't be read when it was made. |

**Not carried:** notes and annotations, which belong to the people who
wrote them under the visibility they chose. Standard revision links aren't
carried either, because revision families are per instance.

## Importing

The import is idempotent: running it again duplicates nothing and fetches
only what is still missing. That is also how an interrupted import is
finished.

- **Collections** are recreated under the chosen target collection (or at
  the top level). Each is matched by name under the same parent, so a
  re-import merges into the tree it made last time.
- **Documents:**
  - A document counts as *already here* when its `id` or `origin_id`
    matches an item in the target space, by that item's own id or its
    recorded origin. Such a document is not duplicated or edited; it is
    only added to the archive's collections.
  - Otherwise a new item is created, recording the archive document as its
    origin.
  - A document filed in no collection goes under the import target.
- **Tags** are matched by name, case-insensitively, and created when
  missing. Only newly created documents get them.
- **Files** are uploaded unless the document already has an uploaded
  attachment with the same `sha256` or the same `filename`. Imported bytes
  become the attachment's new original.

## Versioning and compatibility

`version` is `"MAJOR.MINOR"`. These rules keep every archive importable for
as long as its major version is supported:

1. **A minor version only adds.** New fields must be optional, at any level.
   Nothing is removed, renamed, retyped or given a different meaning.
2. **A reader ignores fields it does not know**, at every level of the
   document.
3. **A reader accepts any minor version of a major version it supports**,
   newer ones included (rule 2 makes that safe). It **refuses a major
   version it doesn't support**, with a message naming the version, and
   writes nothing.
4. **Anything rule 1 can't express is a new major version.** Shelf keeps
   reading the old major alongside it for as long as reasonably possible.
5. **Every released version has a frozen fixture** under
   `backend/tests/fixtures/archive/<version>/`, with an `index.json` and the
   outcome its import must produce. Fixtures are never edited. A change
   that breaks one breaks the compatibility promise.

Archives from Shelf 0.16.2 predate this format. Their `index.json` is a bare
list of `{path, title, item_id, collection}`, and the importer still reads
it (fixture `v0-legacy`).

### Changing the format

- **Adding an optional field:**
  - Add it to the model in `archive_format.py` with a default.
  - Bump `VERSION`'s minor version.
  - Regenerate the schema:

    ```
    cd backend
    python -c "import json; from shelf.archive_format import ArchiveIndex; print(json.dumps(ArchiveIndex.model_json_schema(), indent=2))" > ../docs/schemas/shelf-archive-1.schema.json
    ```

  - Add a fixture for the new version.
  - Document the field above.
- **Anything else:** a new major version needs a new schema file
  (`shelf-archive-2.schema.json`) and an entry in `SUPPORTED_MAJORS`. Keep
  the old major's reader working.
