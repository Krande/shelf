import { Layers } from "lucide-react";

export interface LayerGroup {
  id: string;
  name: string;
  visible: boolean;
}

/**
 * The PDF's optional content groups — its layers.
 *
 * Drawing packages use these heavily: a sheet will carry dimensions,
 * revisions, grid and notes as separate groups, and turning one off is
 * often the only way to read what is underneath. Toggling one re-renders
 * the pages, which is why the reader owns the config rather than this
 * panel: the render path has to see the change.
 */
export function LayersPanel({
  layers,
  onToggle,
}: {
  layers: LayerGroup[] | undefined;
  onToggle: (id: string, visible: boolean) => void;
}) {
  if (layers === undefined) {
    return <Empty>Loading…</Empty>;
  }
  if (layers.length === 0) {
    return <Empty>This PDF has no layers.</Empty>;
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto py-1">
      <ul>
        {layers.map((layer) => (
          <li key={layer.id}>
            <label
              className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-xs hover:opacity-80"
              style={{ color: "var(--color-text)" }}
            >
              <input
                type="checkbox"
                checked={layer.visible}
                onChange={(e) => onToggle(layer.id, e.target.checked)}
                className="cursor-pointer"
              />
              <Layers
                className="h-3.5 w-3.5 shrink-0"
                style={{ color: "var(--color-text-muted)" }}
              />
              <span className="min-w-0 flex-1 truncate">{layer.name}</span>
            </label>
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
