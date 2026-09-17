import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { fetchFulltextHits } from "@/api/items";

/**
 * Renders the expanded snippet list for one item under the fulltext
 * group. Lazy-fetches via the /fulltext-hits endpoint, keyed on
 * (itemId, query) so unrelated re-renders of the table don't refetch.
 *
 * Each snippet is a button — click navigates to the PDF reader on
 * the matching page with the find toolbar pre-populated. The Reader
 * reads `?find=` on mount; the `?page=` param is its existing
 * persistent-position contract.
 *
 * `onOpen` selects the item in the list first, so the reader's Back
 * button lands on the library with this document's detail panel open
 * rather than on nothing in particular.
 */
export default function FulltextHitsRow({
  itemId,
  query,
  colSpan,
  onOpen,
}: {
  itemId: string;
  query: string;
  colSpan: number;
  onOpen?: (itemId: string) => void;
}) {
  const nav = useNavigate();
  const trimmed = query.trim();
  const hits = useQuery({
    queryKey: ["fulltext-hits", itemId, trimmed],
    queryFn: () => fetchFulltextHits(itemId, trimmed),
    enabled: trimmed.length > 0,
    staleTime: 60_000,
  });

  return (
    <tr
      style={{
        backgroundColor:
          "color-mix(in srgb, var(--color-text-muted) 4%, transparent)",
      }}
    >
      <td colSpan={colSpan} className="px-10 py-2">
        {hits.isLoading && (
          <div
            className="text-xs"
            style={{ color: "var(--color-text-muted)" }}
          >
            Loading hits…
          </div>
        )}
        {hits.isError && (
          <div className="text-xs" style={{ color: "var(--color-danger)" }}>
            Couldn't load hits.
          </div>
        )}
        {hits.data && hits.data.length === 0 && (
          <div
            className="text-xs"
            style={{ color: "var(--color-text-muted)" }}
          >
            No PDF hits for this item.
          </div>
        )}
        {hits.data?.map((att) => (
          <div key={att.attachment_id} className="mb-2 last:mb-0">
            <div
              className="mb-1 text-xs font-medium"
              style={{ color: "var(--color-text-muted)" }}
            >
              {att.filename}
            </div>
            <ul className="flex flex-col gap-1">
              {att.hits.map((h, idx) => {
                const target = `/reader/${encodeURIComponent(att.attachment_id)}?page=${h.page_number}&find=${encodeURIComponent(trimmed)}`;
                return (
                  <li
                    key={`${att.attachment_id}-${h.page_number}-${idx}`}
                  >
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        onOpen?.(itemId);
                        nav(target);
                      }}
                      className="block w-full rounded px-2 py-1 text-left text-sm hover:opacity-80"
                      style={{
                        backgroundColor: "var(--color-surface)",
                        color: "var(--color-text)",
                      }}
                    >
                      <span
                        className="mr-2 text-xs"
                        style={{ color: "var(--color-text-muted)" }}
                      >
                        Page {h.page_number}
                      </span>
                      <span
                        // ts_headline output is escaped by Postgres
                        // before its <mark>/</mark> tags are inserted,
                        // so this is safe to render as HTML.
                        dangerouslySetInnerHTML={{
                          __html: h.snippet_html,
                        }}
                      />
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </td>
    </tr>
  );
}
