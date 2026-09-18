/**
 * Local user preferences. Lives in localStorage today — easy to swap
 * for a server-backed `/api/me/prefs` endpoint later without changing
 * call sites. Keep keys boolean/scalar; anything richer should move to
 * the backend.
 */

import { useEffect, useState } from "react";

const PREF_PREFIX = "shelf:pref:";

interface PrefSpec<T> {
  key: string;
  default: T;
}

export const PREF_SHOW_LANDING: PrefSpec<boolean> = {
  key: "show_landing",
  default: true,
};

export type Theme = "system" | "light" | "dark";

export const PREF_THEME: PrefSpec<Theme> = {
  key: "theme",
  // Defaults to dark — most folks reading at a screen for hours prefer
  // it, and the design uses high enough contrast that the light theme
  // can stay opt-in. `system` follows prefers-color-scheme.
  default: "dark",
};

export type Palette = "graphite" | "blueprint" | "drafting" | "carbon";
export const ALL_PALETTES: readonly Palette[] = [
  "graphite",
  "blueprint",
  "drafting",
  "carbon",
] as const;

export const PREF_PALETTE: PrefSpec<Palette> = {
  key: "palette",
  default: "graphite",
};

/**
 * How pages are laid out and scrolled, matching pdf.js's scroll modes.
 *
 * - `page`: one page — or one spread — at a time.
 * - `vertical`: the document in a column, scrolled down.
 * - `horizontal`: in a line, scrolled across.
 * - `wrapped`: flowed into as many columns as fit, then wrapped.
 *
 * The key is unchanged from when these were "single" and "continuous",
 * so `readScrollMode` maps those old values rather than resetting
 * anyone's choice.
 */
export type ScrollMode = "page" | "vertical" | "horizontal" | "wrapped";

export const SCROLL_MODE_LABELS: Record<ScrollMode, string> = {
  page: "Page scrolling",
  vertical: "Vertical scrolling",
  horizontal: "Horizontal scrolling",
  wrapped: "Wrapped scrolling",
};

export const PREF_READER_MODE: PrefSpec<ScrollMode> = {
  key: "reader_mode",
  default: "vertical",
};

/** Reads the pref, translating the two values it used to hold. */
export function readScrollMode(raw: string): ScrollMode {
  if (raw === "single") return "page";
  if (raw === "continuous") return "vertical";
  return raw === "horizontal" || raw === "wrapped" || raw === "page"
    ? raw
    : "vertical";
}

export type ReaderFit = "width" | "page";

export const PREF_READER_FIT: PrefSpec<ReaderFit> = {
  key: "reader_fit",
  // "width" preserves the historical behaviour (page rendered to fill
  // the container width, scroll vertically). "page" fits the full
  // page inside the viewport — necessary for tall pages where the
  // user otherwise can't see top + bottom at once.
  default: "width",
};

/**
 * Auto-expand the "In subcollections" section when the open collection
 * holds fewer than this many documents of its own.
 *
 * A folder with a handful of items has room to show what is below it
 * without the nested list burying anything; a full one does not. Zero
 * turns the auto-expansion off and leaves the section always collapsed
 * until clicked.
 */
export const PREF_SUBCOLLECTION_AUTO_EXPAND_BELOW: PrefSpec<number> = {
  key: "subcollection_auto_expand_below",
  default: 5,
};

/**
 * Facing-page layout, matching pdf.js's spread modes.
 *
 * "odd" pairs from page 1 — (1,2), (3,4) — which suits a document whose
 * cover is a left-hand page. "even" leaves page 1 alone and pairs from
 * there — (1), (2,3) — the shape of a bound book.
 */
export type SpreadPref = "none" | "odd" | "even";

export const PREF_READER_SPREAD: PrefSpec<SpreadPref> = {
  key: "reader_spread",
  default: "none",
};

export function getPref<T>(spec: PrefSpec<T>): T {
  if (typeof window === "undefined") return spec.default;
  const raw = window.localStorage.getItem(PREF_PREFIX + spec.key);
  if (raw === null) return spec.default;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return spec.default;
  }
}

export function setPref<T>(spec: PrefSpec<T>, value: T): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(PREF_PREFIX + spec.key, JSON.stringify(value));
  window.dispatchEvent(
    new CustomEvent("shelf-pref-change", { detail: { key: spec.key } }),
  );
}

/** React hook that reads a pref and re-renders when setPref runs. */
export function usePref<T>(spec: PrefSpec<T>): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => getPref(spec));

  useEffect(() => {
    function onChange(e: Event) {
      const detail = (e as CustomEvent).detail as { key: string } | undefined;
      if (!detail || detail.key === spec.key) setValue(getPref(spec));
    }
    function onStorage(e: StorageEvent) {
      if (e.key === PREF_PREFIX + spec.key) setValue(getPref(spec));
    }
    window.addEventListener("shelf-pref-change", onChange);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener("shelf-pref-change", onChange);
      window.removeEventListener("storage", onStorage);
    };
  }, [spec]);

  return [
    value,
    (v: T) => {
      setPref(spec, v);
      setValue(v);
    },
  ];
}
