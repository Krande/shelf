import { useEffect, useRef, useState } from "react";
import { Download } from "lucide-react";

/**
 * Small dropdown over the detail panel that triggers a download in
 * the requested format. Each entry navigates to the export endpoint;
 * the server's Content-Disposition: attachment header makes the
 * browser save instead of navigate.
 *
 * Pass either `itemId` for a single-item export, or `slug` for a
 * bulk space export — exactly one is required.
 */
export default function ExportMenu({
  itemId,
  slug,
}: {
  itemId?: string;
  slug?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  const baseUrl = itemId
    ? `/api/items/${encodeURIComponent(itemId)}/export`
    : slug
      ? `/api/spaces/${encodeURIComponent(slug)}/export`
      : null;
  if (!baseUrl) return null;

  function urlFor(format: "bibtex" | "csl-json" | "rdf"): string {
    return `${baseUrl}?format=${format}`;
  }

  // For a single item, RDF is a plain .rdf file (no PDFs). At the
  // space (bulk) level it's a ZIP that bundles the .rdf next to a
  // files/ directory of the attached PDFs — the same shape Zotero
  // produces, so the export can be unzipped and imported directly.
  const isBulk = !itemId && !!slug;
  const formats: {
    value: "bibtex" | "csl-json" | "rdf";
    label: string;
    hint: string;
  }[] = [
    { value: "bibtex", label: "BibTeX", hint: ".bib" },
    { value: "csl-json", label: "CSL-JSON", hint: ".json" },
    {
      value: "rdf",
      label: "Zotero RDF",
      hint: isBulk ? ".zip — RDF + PDFs" : ".rdf — metadata only",
    },
  ];

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Export"
        title="Export"
        className="flex items-center gap-1 rounded px-2 py-1 text-xs hover:opacity-80"
        style={{
          color: open ? "var(--color-accent)" : "var(--color-text-muted)",
        }}
      >
        <Download className="h-3.5 w-3.5" />
        Export
      </button>
      {open && (
        <div
          className="absolute right-0 top-full z-10 mt-1 w-56 overflow-hidden rounded border shadow-lg"
          style={{
            backgroundColor: "var(--color-surface)",
            borderColor: "var(--color-border)",
          }}
        >
          <ul>
            {formats.map((f) => (
              <li key={f.value}>
                <a
                  href={urlFor(f.value)}
                  download
                  onClick={() => setOpen(false)}
                  className="block px-3 py-2 text-sm hover:opacity-80"
                  style={{ color: "var(--color-text)" }}
                >
                  <div className="font-medium">{f.label}</div>
                  <div
                    className="text-[10px]"
                    style={{ color: "var(--color-text-muted)" }}
                  >
                    {f.hint}
                  </div>
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
