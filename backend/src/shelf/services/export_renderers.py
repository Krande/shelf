"""Bibliographic export renderers.

Three formats, all pure-Python with no external deps:

- **BibTeX** — wide compatibility, basic metadata. Loses tags and
  collection membership; fine for citation manager consumption.
- **CSL-JSON** — citeproc/Pandoc lingua franca. Preserves more
  metadata than BibTeX without inventing structure.
- **Zotero RDF** — full-fidelity round-trip into a Zotero library:
  type, creators, metadata, tags, collections, attachments.

Each renderer takes the same `(items, attachments_by_item, tags_by_item,
collections_by_item)` payload and emits text. The endpoint layer
hands it to `Response(content_type=...)`.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable
from html import escape as _html_escape
from typing import Any

from ..models import Attachment, Collection, Item, Tag

# ── Common helpers ─────────────────────────────────────────────────────────


def _creator_parts(c: dict[str, Any]) -> tuple[str, str]:
    """Split a creator dict into ``(family, given)`` parts. ``name``
    (single-field, used for organisations) is returned as the family
    part with an empty given part.
    """
    name = c.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip(), ""
    family = (c.get("lastName") or "").strip()
    given = (c.get("firstName") or "").strip()
    return family, given


def _year_from_date(s: Any) -> str | None:
    if not isinstance(s, str):
        return None
    m = re.search(r"\b(\d{4})\b", s)
    return m.group(1) if m else None


def _slug_from_creator(c: dict[str, Any]) -> str:
    family, _ = _creator_parts(c)
    return re.sub(r"[^A-Za-z0-9]", "", family).lower() or "anon"


def _bibtex_id(item: Item) -> str:
    """Stable per-item BibTeX cite key. Form: ``firstauthorYearTitleslug``.
    Falls back to the UUID hex when nothing usable is in the data."""
    data = item.data or {}
    creators = data.get("creators") or []
    first = creators[0] if creators else {}
    author = _slug_from_creator(first) if first else "anon"
    year = _year_from_date(data.get("date")) or "nd"
    title = (data.get("title") or "").strip()
    title_slug = re.sub(r"[^A-Za-z0-9]", "", title).lower()[:12] or "untitled"
    base = f"{author}{year}{title_slug}"
    if not base or base == "anonnduntitled":
        return f"item{item.id.hex[:8]}"
    return base


# ── BibTeX ─────────────────────────────────────────────────────────────────

# Zotero item type → BibTeX entry type. Anything not listed maps to
# @misc, which Zotero round-trips harmlessly.
_BIBTEX_TYPE: dict[str, str] = {
    "journalArticle": "article",
    "book": "book",
    "bookSection": "incollection",
    "conferencePaper": "inproceedings",
    "thesis": "phdthesis",
    "report": "techreport",
    "preprint": "misc",
    "webpage": "misc",
    "patent": "misc",
    "document": "misc",
    "computerProgram": "misc",
    "film": "misc",
    "presentation": "misc",
    "letter": "misc",
    "manuscript": "unpublished",
    "note": "misc",
}


def _bibtex_escape(value: str) -> str:
    """Wrap inner braces; escape stray characters minimally so the
    output is parseable. We don't try to LaTeX-encode unicode; modern
    BibTeX engines (biber) accept UTF-8 directly."""
    return value.replace("\\", "\\\\").replace("}", "\\}").replace("{", "\\{")


def _bibtex_field(name: str, value: str) -> str:
    return f"  {name} = {{{_bibtex_escape(value)}}}"


def _format_bibtex_creators(creators: list[dict[str, Any]], role: str) -> str:
    """`role` is "author" or "editor". Returns a BibTeX-friendly
    "Lastname, Firstname and …" string."""
    parts: list[str] = []
    for c in creators:
        if c.get("creatorType", "author") != role:
            continue
        family, given = _creator_parts(c)
        if family and given:
            parts.append(f"{family}, {given}")
        elif family:
            parts.append(family)
    return " and ".join(parts)


def render_bibtex(items: Iterable[Item]) -> str:
    out: list[str] = []
    seen: dict[str, int] = {}
    for item in items:
        if item.item_type == "attachment" or item.item_type == "note":
            # BibTeX has no slot for notes/attachments as standalone
            # entries. Skip; users get them via RDF/CSL.
            continue
        data = item.data or {}
        cite = _bibtex_id(item)
        # Disambiguate within the export.
        if cite in seen:
            seen[cite] += 1
            cite = f"{cite}{chr(ord('a') + seen[cite] - 1)}"
        else:
            seen[cite] = 0
        entry_type = _BIBTEX_TYPE.get(item.item_type, "misc")
        fields: list[str] = []
        title = data.get("title")
        if isinstance(title, str) and title.strip():
            fields.append(_bibtex_field("title", title.strip()))
        creators = data.get("creators") or []
        author = _format_bibtex_creators(creators, "author")
        editor = _format_bibtex_creators(creators, "editor")
        if author:
            fields.append(_bibtex_field("author", author))
        if editor:
            fields.append(_bibtex_field("editor", editor))
        year = _year_from_date(data.get("date"))
        if year:
            fields.append(_bibtex_field("year", year))
        if isinstance(data.get("date"), str):
            fields.append(_bibtex_field("date", data["date"]))

        # Type-specific common fields.
        for src, dst in (
            ("publicationTitle", "journal"),
            ("bookTitle", "booktitle"),
            ("proceedingsTitle", "booktitle"),
            ("publisher", "publisher"),
            ("place", "address"),
            ("volume", "volume"),
            ("issue", "number"),
            ("pages", "pages"),
            ("DOI", "doi"),
            ("ISSN", "issn"),
            ("ISBN", "isbn"),
            ("url", "url"),
            ("abstractNote", "abstract"),
            ("university", "school"),
            ("institution", "institution"),
        ):
            v = data.get(src)
            if isinstance(v, str) and v.strip():
                fields.append(_bibtex_field(dst, v.strip()))
        out.append(
            "@" + entry_type + "{" + cite + ",\n" + ",\n".join(fields) + "\n}"
        )
    return "\n\n".join(out) + ("\n" if out else "")


# ── CSL-JSON ───────────────────────────────────────────────────────────────

# Zotero item type → CSL type. Not every type maps cleanly; the
# fallbacks here match Zotero's own export translator.
_CSL_TYPE: dict[str, str] = {
    "journalArticle": "article-journal",
    "book": "book",
    "bookSection": "chapter",
    "conferencePaper": "paper-conference",
    "thesis": "thesis",
    "report": "report",
    "preprint": "article",
    "webpage": "webpage",
    "patent": "patent",
    "document": "document",
    "computerProgram": "software",
    "film": "motion_picture",
    "presentation": "speech",
    "letter": "personal_communication",
    "manuscript": "manuscript",
    "note": "document",
    "attachment": "document",
}


def _csl_creators(
    creators: list[dict[str, Any]], role: str
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in creators:
        if c.get("creatorType", "author") != role:
            continue
        family, given = _creator_parts(c)
        entry: dict[str, Any] = {}
        # Org-style single-field name → CSL "literal".
        if c.get("name") and not (
            c.get("firstName") or c.get("lastName")
        ):
            entry["literal"] = family
        else:
            if family:
                entry["family"] = family
            if given:
                entry["given"] = given
        if entry:
            out.append(entry)
    return out


def _csl_issued(date: Any) -> dict[str, Any] | None:
    if not isinstance(date, str):
        return None
    m = re.match(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", date)
    if not m:
        # Fall back to literal so non-iso strings ("Spring 2024") keep
        # round-tripping.
        return {"literal": date}
    parts = [int(g) for g in m.groups() if g is not None]
    return {"date-parts": [parts]}


def render_csl_json(items: Iterable[Item]) -> str:
    out: list[dict[str, Any]] = []
    for item in items:
        data = item.data or {}
        entry: dict[str, Any] = {
            "id": str(item.id),
            "type": _CSL_TYPE.get(item.item_type, "document"),
        }
        if isinstance(data.get("title"), str):
            entry["title"] = data["title"]
        creators = data.get("creators") or []
        for role, key in (
            ("author", "author"),
            ("editor", "editor"),
            ("translator", "translator"),
        ):
            people = _csl_creators(creators, role)
            if people:
                entry[key] = people
        issued = _csl_issued(data.get("date"))
        if issued:
            entry["issued"] = issued
        for src, dst in (
            ("publicationTitle", "container-title"),
            ("bookTitle", "container-title"),
            ("proceedingsTitle", "container-title"),
            ("publisher", "publisher"),
            ("place", "publisher-place"),
            ("volume", "volume"),
            ("issue", "issue"),
            ("pages", "page"),
            ("DOI", "DOI"),
            ("ISSN", "ISSN"),
            ("ISBN", "ISBN"),
            ("url", "URL"),
            ("abstractNote", "abstract"),
        ):
            v = data.get(src)
            if isinstance(v, str) and v.strip():
                entry[dst] = v.strip()
        out.append(entry)
    return json.dumps(out, indent=2, ensure_ascii=False) + "\n"


# ── Zotero RDF ─────────────────────────────────────────────────────────────

# RDF type → bib: class. Maps against Zotero's own RDF translator.
_RDF_TYPE: dict[str, str] = {
    "journalArticle": "bib:Article",
    "book": "bib:Book",
    "bookSection": "bib:BookSection",
    "conferencePaper": "bib:ConferencePaper",
    "thesis": "bib:Thesis",
    "report": "bib:Report",
    "preprint": "bib:Document",
    "webpage": "bib:Document",
    "patent": "bib:Patent",
    "document": "bib:Document",
    "computerProgram": "bib:Document",
    "film": "bib:Image",
    "presentation": "bib:Document",
    "letter": "bib:Letter",
    "manuscript": "bib:Manuscript",
    "note": "bib:Memo",
    "attachment": "z:Attachment",
}


def _xml_text(s: str) -> str:
    """XML 1.0 text-content escape (used inside element bodies, not
    attribute values)."""
    return _html_escape(s, quote=False)


def _xml_attr(s: str) -> str:
    return _html_escape(s, quote=True)


def attachment_zip_path(att: Attachment) -> str:
    """Where the attachment lives inside the bundled-export ZIP.
    Mirrors Zotero's translator: ``files/<itemID>/<filename>``. We use
    the attachment's hex UUID for the directory so the path is unique
    even across libraries."""
    return f"files/{att.id.hex}/{att.filename}"


def render_zotero_rdf(
    items: list[Item],
    *,
    attachments_by_item: dict[uuid.UUID, list[Attachment]] | None = None,
    tags_by_item: dict[uuid.UUID, list[Tag]] | None = None,
    collections_by_item: dict[uuid.UUID, list[Collection]] | None = None,
    collections_index: dict[uuid.UUID, Collection] | None = None,
    include_file_paths: bool = False,
) -> str:
    """Emit a Zotero-RDF document. Tags / collections / attachments
    only show up if the corresponding map is supplied; passing nothing
    is fine for a metadata-only export.

    ``include_file_paths=True`` adds ``<z:path rdf:resource="files/N/...">``
    to every attachment so a sibling ``files/`` directory in the export
    bundle can be picked up on Zotero re-import."""
    attachments_by_item = attachments_by_item or {}
    tags_by_item = tags_by_item or {}
    collections_by_item = collections_by_item or {}
    collections_index = collections_index or {}

    lines: list[str] = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append("<rdf:RDF")
    lines.append(' xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"')
    lines.append(' xmlns:dc="http://purl.org/dc/elements/1.1/"')
    lines.append(' xmlns:dcterms="http://purl.org/dc/terms/"')
    lines.append(' xmlns:bib="http://purl.org/net/biblio#"')
    lines.append(' xmlns:foaf="http://xmlns.com/foaf/0.1/"')
    lines.append(' xmlns:link="http://purl.org/rss/1.0/modules/link/"')
    lines.append(' xmlns:z="http://www.zotero.org/namespaces/export#">')

    # Collections — write each as a top-level z:Collection. Items
    # belonging to a collection get linked via dcterms:isPartOf.
    written_collections: set[uuid.UUID] = set()

    def _write_collection(c: Collection) -> None:
        if c.id in written_collections:
            return
        written_collections.add(c.id)
        lines.append(
            f' <z:Collection rdf:about="#collection_{c.id.hex}">'
        )
        lines.append(
            f"  <dc:title>{_xml_text(c.name)}</dc:title>"
        )
        if c.parent_id and c.parent_id in collections_index:
            parent = collections_index[c.parent_id]
            lines.append(
                f'  <dcterms:isPartOf rdf:resource="#collection_{parent.id.hex}"/>'
            )
            _write_collection(parent)
        lines.append(" </z:Collection>")

    # Pre-write all collections referenced by exported items.
    for colls in collections_by_item.values():
        for c in colls:
            _write_collection(c)

    # Items.
    for item in items:
        rdf_type = _RDF_TYPE.get(item.item_type, "bib:Document")
        about = f"#item_{item.id.hex}"
        data = item.data or {}
        lines.append(f' <{rdf_type} rdf:about="{_xml_attr(about)}">')
        lines.append(
            f'  <z:itemType>{_xml_text(item.item_type)}</z:itemType>'
        )
        title = data.get("title")
        if isinstance(title, str):
            lines.append(f"  <dc:title>{_xml_text(title)}</dc:title>")
        if isinstance(data.get("date"), str):
            lines.append(
                f'  <dc:date>{_xml_text(data["date"])}</dc:date>'
            )
        if isinstance(data.get("abstractNote"), str):
            lines.append(
                f'  <dcterms:abstract>{_xml_text(data["abstractNote"])}</dcterms:abstract>'
            )
        if isinstance(data.get("url"), str):
            lines.append(
                f'  <dc:identifier>{_xml_text(data["url"])}</dc:identifier>'
            )
        if isinstance(data.get("DOI"), str):
            lines.append(
                f'  <dc:identifier>DOI {_xml_text(data["DOI"])}</dc:identifier>'
            )
        if isinstance(data.get("ISSN"), str):
            lines.append(
                f'  <dc:identifier>ISSN {_xml_text(data["ISSN"])}</dc:identifier>'
            )
        if isinstance(data.get("ISBN"), str):
            lines.append(
                f'  <dc:identifier>ISBN {_xml_text(data["ISBN"])}</dc:identifier>'
            )
        for src, key in (
            ("publicationTitle", "dcterms:isPartOf"),
            ("bookTitle", "dcterms:isPartOf"),
            ("proceedingsTitle", "dcterms:isPartOf"),
        ):
            v = data.get(src)
            if isinstance(v, str) and v.strip():
                lines.append(
                    f'  <{key}><dc:title>{_xml_text(v.strip())}</dc:title></{key}>'
                )
                break
        if isinstance(data.get("publisher"), str):
            lines.append(
                f'  <dc:publisher>{_xml_text(data["publisher"])}</dc:publisher>'
            )
        if isinstance(data.get("volume"), str):
            lines.append(
                f'  <bib:volume>{_xml_text(data["volume"])}</bib:volume>'
            )
        if isinstance(data.get("pages"), str):
            lines.append(
                f'  <bib:pages>{_xml_text(data["pages"])}</bib:pages>'
            )

        # Authors / editors.
        creators = data.get("creators") or []
        if creators:
            authors = [c for c in creators if c.get("creatorType", "author") == "author"]
            editors = [c for c in creators if c.get("creatorType") == "editor"]
            if authors:
                lines.append("  <bib:authors>")
                lines.append('   <rdf:Seq>')
                for a in authors:
                    family, given = _creator_parts(a)
                    lines.append("    <rdf:li>")
                    lines.append("     <foaf:Person>")
                    if family:
                        lines.append(
                            f"      <foaf:surname>{_xml_text(family)}</foaf:surname>"
                        )
                    if given:
                        lines.append(
                            f"      <foaf:givenName>{_xml_text(given)}</foaf:givenName>"
                        )
                    lines.append("     </foaf:Person>")
                    lines.append("    </rdf:li>")
                lines.append("   </rdf:Seq>")
                lines.append("  </bib:authors>")
            if editors:
                lines.append("  <bib:editors>")
                lines.append('   <rdf:Seq>')
                for a in editors:
                    family, given = _creator_parts(a)
                    lines.append("    <rdf:li>")
                    lines.append("     <foaf:Person>")
                    if family:
                        lines.append(
                            f"      <foaf:surname>{_xml_text(family)}</foaf:surname>"
                        )
                    if given:
                        lines.append(
                            f"      <foaf:givenName>{_xml_text(given)}</foaf:givenName>"
                        )
                    lines.append("     </foaf:Person>")
                    lines.append("    </rdf:li>")
                lines.append("   </rdf:Seq>")
                lines.append("  </bib:editors>")

        for tag in tags_by_item.get(item.id, []):
            lines.append(
                f"  <dc:subject>{_xml_text(tag.name)}</dc:subject>"
            )
        for c in collections_by_item.get(item.id, []):
            lines.append(
                f'  <dcterms:isPartOf rdf:resource="#collection_{c.id.hex}"/>'
            )
        for att in attachments_by_item.get(item.id, []):
            lines.append(
                f'  <link:link rdf:resource="#item_{att.id.hex}"/>'
            )
        lines.append(f' </{rdf_type}>')

        # Attachment records as separate resources. Match Zotero's
        # translator: rdf:about="#item_<id>", z:path points at the
        # path inside the export bundle when files are included.
        for att in attachments_by_item.get(item.id, []):
            lines.append(
                f' <z:Attachment rdf:about="#item_{att.id.hex}">'
            )
            lines.append("  <z:itemType>attachment</z:itemType>")
            lines.append(
                f"  <dc:title>{_xml_text(att.filename)}</dc:title>"
            )
            if att.content_type:
                lines.append(
                    f'  <link:type>{_xml_text(att.content_type)}</link:type>'
                )
            if include_file_paths:
                lines.append(
                    f'  <z:path rdf:resource="{_xml_attr(attachment_zip_path(att))}"/>'
                )
            lines.append(" </z:Attachment>")

    lines.append("</rdf:RDF>")
    return "\n".join(lines) + "\n"
