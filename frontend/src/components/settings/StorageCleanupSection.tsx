import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Trash2 } from "lucide-react";
import { cleanupOrphanAttachments } from "@/api/attachments";

export default function StorageCleanupSection() {
  const qc = useQueryClient();
  const [result, setResult] = useState<string | null>(null);

  const cleanup = useMutation({
    mutationFn: () => cleanupOrphanAttachments(60),
    onSuccess: ({ deleted }) => {
      setResult(
        deleted === 0
          ? "Nothing to clean up — no pending uploads older than an hour."
          : `Deleted ${deleted} pending attachment${deleted === 1 ? "" : "s"}.`,
      );
      qc.invalidateQueries({ queryKey: ["attachments"] });
    },
    onError: (e: Error) => setResult(`Cleanup failed: ${e.message}`),
  });

  return (
    <section
      className="mb-6 rounded border p-4"
      style={{
        backgroundColor: "var(--color-surface)",
        borderColor: "var(--color-border)",
      }}
    >
      <h2 className="mb-1 text-sm font-medium">Storage cleanup</h2>
      <p className="mb-3 text-xs" style={{ color: "var(--color-text-muted)" }}>
        Pending attachments are rows whose upload never finished — usually a tab
        close mid-PUT. They show up as "(pending)" on the item and can't be
        opened. This sweeps any older than an hour so they're not left as ghosts.
      </p>
      <button
        onClick={() => {
          setResult(null);
          cleanup.mutate();
        }}
        disabled={cleanup.isPending}
        className="flex items-center gap-2 rounded border px-3 py-1.5 text-sm hover:opacity-80 disabled:opacity-50"
        style={{
          borderColor: "var(--color-border)",
          color: "var(--color-text)",
        }}
      >
        {cleanup.isPending ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Trash2 className="h-4 w-4" />
        )}
        {cleanup.isPending ? "Cleaning…" : "Clean up pending uploads"}
      </button>
      {result && (
        <p className="mt-2 text-xs" style={{ color: "var(--color-text-muted)" }}>
          {result}
        </p>
      )}
    </section>
  );
}
