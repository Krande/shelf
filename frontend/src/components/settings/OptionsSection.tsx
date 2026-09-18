import {
  PREF_SUBCOLLECTION_AUTO_EXPAND_BELOW,
  usePref,
} from "@/auth/prefs";

/**
 * Behaviour knobs that aren't about looks.
 *
 * Appearance owns theme and palette; this owns how the library decides
 * what to show without being asked.
 */
export default function OptionsSection() {
  const [expandBelow, setExpandBelow] = usePref(
    PREF_SUBCOLLECTION_AUTO_EXPAND_BELOW,
  );

  return (
    <section
      className="mb-6 rounded border p-4"
      style={{
        backgroundColor: "var(--color-surface)",
        borderColor: "var(--color-border)",
      }}
    >
      <h2 className="mb-1 text-sm font-medium">Library</h2>
      <p className="mb-3 text-xs" style={{ color: "var(--color-text-muted)" }}>
        Opening a collection lists its own documents, and offers whatever is
        filed under its subcollections below them.
      </p>

      <label className="flex flex-wrap items-center gap-2 text-sm">
        <span>Expand subcollections when the collection has fewer than</span>
        <input
          type="number"
          min={0}
          max={999}
          value={expandBelow}
          onChange={(e) => {
            const next = Number(e.target.value);
            // An empty field parses as NaN, and a negative one is the
            // same as off; clamp rather than storing either.
            setExpandBelow(
              Number.isFinite(next) ? Math.min(Math.max(next, 0), 999) : 0,
            );
          }}
          aria-label="Expand subcollections below this many documents"
          className="w-20 rounded border px-2 py-1 text-sm"
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-surface)",
          }}
        />
        <span>documents of its own</span>
      </label>
      <p className="mt-2 text-xs" style={{ color: "var(--color-text-muted)" }}>
        A folder with a handful of documents has room to show what is below
        it; a full one does not. Set to 0 to keep the section collapsed until
        you click it. You can always fold it away again on any collection.
      </p>
    </section>
  );
}
