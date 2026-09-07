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

export type ReaderMode = "single" | "continuous";

export const PREF_READER_MODE: PrefSpec<ReaderMode> = {
  key: "reader_mode",
  default: "continuous",
};

export type ReaderFit = "width" | "page";

export const PREF_READER_FIT: PrefSpec<ReaderFit> = {
  key: "reader_fit",
  // "width" preserves the historical behaviour (page rendered to fill
  // the container width, scroll vertically). "page" fits the full
  // page inside the viewport — necessary for tall pages where the
  // user otherwise can't see top + bottom at once.
  default: "width",
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
