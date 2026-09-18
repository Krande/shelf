/**
 * Table column widths the reader can drag, remembered per browser.
 *
 * Unlike `useResizableWidth`, which owns one pane, this owns a set: the
 * handle lives on a column's right edge and drags that column only, so
 * the ones after it shift rather than resize. Widths are stored per
 * column key, and a key with nothing stored falls back to the default
 * the table declares — which is how "the current layout is the default"
 * stays true without the numbers being duplicated here.
 */

import { useCallback, useRef, useState } from "react";

export interface ColumnWidths {
  /** Width to apply to a column, always a number so the table can be
   *  laid out fixed and honour it. */
  width: (key: string) => number;
  /** Bind to a column's drag handle. */
  onPointerDown: (key: string) => (e: React.PointerEvent) => void;
  /** Arrow keys nudge, Home resets the column. */
  onKeyDown: (key: string) => (e: React.KeyboardEvent) => void;
  /** Sum of every column's width — the table's minimum, so a wide
   *  layout scrolls instead of squeezing columns back down. */
  total: (keys: string[]) => number;
  /** Forget every stored width. */
  resetAll: () => void;
  resizing: string | null;
}

const MIN = 48;
const MAX = 1200;
const STEP = 16;

export function useColumnWidths(
  storageKey: string,
  defaults: Record<string, number>,
): ColumnWidths {
  const [stored, setStored] = useState<Record<string, number>>(() =>
    read(storageKey),
  );
  const [resizing, setResizing] = useState<string | null>(null);
  // Read inside pointer listeners without re-subscribing per pixel.
  const latest = useRef(stored);
  latest.current = stored;

  const width = useCallback(
    (key: string) => stored[key] ?? defaults[key] ?? 160,
    [stored, defaults],
  );

  const commit = useCallback(
    (next: Record<string, number>) => {
      setStored(next);
      try {
        if (Object.keys(next).length === 0) {
          localStorage.removeItem(storageKey);
        } else {
          localStorage.setItem(storageKey, JSON.stringify(next));
        }
      } catch {
        // Private windows and blocked site data throw on write. The
        // columns still resize for this session; they just are not
        // remembered, which beats crashing the drag.
      }
    },
    [storageKey],
  );

  const set = useCallback(
    (key: string, value: number) => {
      commit({
        ...latest.current,
        [key]: Math.round(Math.min(Math.max(value, MIN), MAX)),
      });
    },
    [commit],
  );

  const onPointerDown = useCallback(
    (key: string) => (e: React.PointerEvent) => {
      // Stop the header's own click handler — every sortable column is
      // a sort toggle, and grabbing its edge is not a request to sort.
      e.preventDefault();
      e.stopPropagation();
      const handle = e.currentTarget as HTMLElement;
      const startX = e.clientX;
      const startWidth =
        latest.current[key] ??
        handle.closest("th")?.getBoundingClientRect().width ??
        defaults[key] ??
        160;

      setResizing(key);
      handle.setPointerCapture(e.pointerId);

      const move = (ev: PointerEvent) => {
        set(key, startWidth + (ev.clientX - startX));
      };
      const up = (ev: PointerEvent) => {
        setResizing(null);
        handle.releasePointerCapture?.(ev.pointerId);
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", up);
        handle.removeEventListener("pointercancel", up);
      };

      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", up);
      handle.addEventListener("pointercancel", up);
    },
    [set, defaults],
  );

  const onKeyDown = useCallback(
    (key: string) => (e: React.KeyboardEvent) => {
      const current = latest.current[key] ?? defaults[key] ?? 160;
      if (e.key === "ArrowRight") {
        e.preventDefault();
        e.stopPropagation();
        set(key, current + STEP);
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        e.stopPropagation();
        set(key, current - STEP);
      } else if (e.key === "Home") {
        e.preventDefault();
        e.stopPropagation();
        const next = { ...latest.current };
        delete next[key];
        commit(next);
      }
    },
    [set, commit, defaults],
  );

  const total = useCallback(
    (keys: string[]) => keys.reduce((sum, k) => sum + width(k), 0),
    [width],
  );

  return {
    width,
    onPointerDown,
    onKeyDown,
    total,
    resetAll: () => commit({}),
    resizing,
  };
}

function read(key: string): Record<string, number> {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return {};
    const out: Record<string, number> = {};
    for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof v === "number" && Number.isFinite(v) && v > 0) out[k] = v;
    }
    return out;
  } catch {
    // Blocked site data, or a value written by an older shape.
    return {};
  }
}
