import { useEffect, useState } from "react";
import { Download, Paperclip } from "lucide-react";
import type { PDFDocumentProxy } from "pdfjs-dist";

interface EmbeddedFile {
  filename: string;
  content: Uint8Array;
}

/**
 * Files carried inside the PDF.
 *
 * Distinct from shelf's own attachments, which hang off the item: these
 * are embedded in the document itself, and most PDFs have none. A
 * specification that ships its worked examples as spreadsheets is the
 * usual case for one that does.
 */
export function AttachmentsPanel({ doc }: { doc: PDFDocumentProxy | null }) {
  const [files, setFiles] = useState<EmbeddedFile[] | undefined>(undefined);

  useEffect(() => {
    if (!doc) return;
    let cancelled = false;
    doc
      .getAttachments()
      .then((found: Record<string, EmbeddedFile> | null) => {
        if (cancelled) return;
        setFiles(found ? Object.values(found) : []);
      })
      .catch(() => {
        if (!cancelled) setFiles([]);
      });
    return () => {
      cancelled = true;
    };
  }, [doc]);

  function save(file: EmbeddedFile) {
    // Copied into a fresh buffer: the view pdfjs hands back can be
    // backed by memory it reuses.
    const blob = new Blob([new Uint8Array(file.content)]);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = file.filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  if (files === undefined) {
    return <Empty>Loading…</Empty>;
  }
  if (files.length === 0) {
    return <Empty>Nothing is embedded in this PDF.</Empty>;
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto py-1">
      <ul>
        {files.map((file) => (
          <li key={file.filename}>
            <button
              type="button"
              onClick={() => save(file)}
              title={`Save ${file.filename}`}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:opacity-80"
              style={{ color: "var(--color-text)" }}
            >
              <Paperclip className="h-3.5 w-3.5 shrink-0" />
              <span className="min-w-0 flex-1 truncate">{file.filename}</span>
              <Download
                className="h-3.5 w-3.5 shrink-0"
                style={{ color: "var(--color-text-muted)" }}
              />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="px-3 py-6 text-center text-xs"
      style={{ color: "var(--color-text-muted)" }}
    >
      {children}
    </div>
  );
}
