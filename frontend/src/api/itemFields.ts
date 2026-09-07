/**
 * Item-type schema mirrors Zotero's item-type definitions so existing
 * data imports cleanly. Shelf's backend stores `data` as opaque JSON,
 * so this file is purely a frontend convention — it drives the type
 * picker and the form's per-type field list, nothing more.
 *
 * Types and labels are a trimmed but compatible subset; expand here as
 * you find yourself wanting more fields, no migration required.
 */

export type ItemType =
  | "journalArticle"
  | "book"
  | "bookSection"
  | "conferencePaper"
  | "thesis"
  | "report"
  | "preprint"
  | "webpage"
  | "patent"
  | "document"
  | "computerProgram"
  | "film"
  | "presentation"
  | "letter"
  | "manuscript"
  | "note";

export interface Creator {
  creatorType: string; // "author" | "editor" | "translator" | …
  firstName?: string;
  lastName?: string;
  name?: string; // single-field name for orgs
}

export const ITEM_TYPES: { value: ItemType; label: string }[] = [
  { value: "journalArticle", label: "Journal Article" },
  { value: "book", label: "Book" },
  { value: "bookSection", label: "Book Section" },
  { value: "conferencePaper", label: "Conference Paper" },
  { value: "thesis", label: "Thesis" },
  { value: "report", label: "Report" },
  { value: "preprint", label: "Preprint" },
  { value: "webpage", label: "Web Page" },
  { value: "patent", label: "Patent" },
  { value: "document", label: "Document" },
  { value: "computerProgram", label: "Software" },
  { value: "film", label: "Film" },
  { value: "presentation", label: "Presentation" },
  { value: "letter", label: "Letter" },
  { value: "manuscript", label: "Manuscript" },
  { value: "note", label: "Note" },
];

/** Fields every type gets in addition to title and creators. */
export const COMMON_FIELDS = ["date", "abstractNote", "url", "extra"] as const;

/** Type-specific fields appended after the common ones. */
export const TYPE_FIELDS: Record<string, readonly string[]> = {
  journalArticle: ["publicationTitle", "volume", "issue", "pages", "DOI", "ISSN"],
  book: ["publisher", "place", "ISBN", "pages"],
  bookSection: ["bookTitle", "publisher", "place", "ISBN", "pages"],
  conferencePaper: ["proceedingsTitle", "publisher", "place", "DOI", "pages"],
  thesis: ["university", "place", "thesisType"],
  report: ["institution", "place", "reportNumber"],
  webpage: ["websiteTitle", "accessDate"],
  preprint: ["repository", "DOI"],
  patent: ["country", "assignee", "patentNumber"],
  computerProgram: ["versionNumber", "company", "programmingLanguage"],
  presentation: ["place", "meetingName"],
  film: ["director", "studio", "runningTime"],
  letter: ["recipient", "letterType"],
  manuscript: ["place", "manuscriptType"],
  note: ["note"],
};

export const FIELD_LABELS: Record<string, string> = {
  title: "Title",
  date: "Date",
  abstractNote: "Abstract",
  url: "URL",
  extra: "Extra",
  publicationTitle: "Publication",
  volume: "Volume",
  issue: "Issue",
  pages: "Pages",
  DOI: "DOI",
  ISSN: "ISSN",
  ISBN: "ISBN",
  publisher: "Publisher",
  place: "Place",
  bookTitle: "Book Title",
  proceedingsTitle: "Proceedings Title",
  university: "University",
  thesisType: "Type",
  institution: "Institution",
  reportNumber: "Report Number",
  websiteTitle: "Website Title",
  accessDate: "Accessed",
  repository: "Repository",
  country: "Country",
  assignee: "Assignee",
  patentNumber: "Patent Number",
  versionNumber: "Version",
  company: "Company",
  programmingLanguage: "Language",
  meetingName: "Meeting",
  director: "Director",
  studio: "Studio",
  runningTime: "Running Time",
  recipient: "Recipient",
  letterType: "Letter Type",
  manuscriptType: "Manuscript Type",
  note: "Note",
};

/** Multi-line fields render as a textarea instead of a single-line input. */
export const TEXTAREA_FIELDS = new Set(["abstractNote", "extra", "note"]);

export function fieldsForType(itemType: string): string[] {
  return [
    "title",
    ...(TYPE_FIELDS[itemType] ?? []),
    ...COMMON_FIELDS.filter((f) => !(TYPE_FIELDS[itemType] ?? []).includes(f)),
  ];
}

export function labelFor(field: string): string {
  return FIELD_LABELS[field] ?? field;
}

export function itemTypeLabel(value: string): string {
  return ITEM_TYPES.find((t) => t.value === value)?.label ?? value;
}
