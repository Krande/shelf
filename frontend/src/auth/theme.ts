/**
 * Applies the user's chosen theme + palette to documentElement.
 *
 * - `.dark` class toggles dark mode within the active palette.
 * - `data-palette="…"` selects which color family is in play; the
 *   default ("ink") sets `:root` directly so no attribute is needed.
 *
 * Both contracts are read by `index.css`: `.dark` (and
 * `[data-palette].dark` for non-default palettes) override the same
 * `--color-*` tokens consumed everywhere in the app.
 *
 * `system` follows prefers-color-scheme and re-evaluates when the OS
 * setting changes. `light`/`dark` are sticky overrides.
 */

import { useEffect } from "react";
import {
  PREF_PALETTE,
  PREF_THEME,
  usePref,
  type Palette,
  type Theme,
} from "./prefs";

function resolve(theme: Theme): "light" | "dark" {
  if (theme === "system") {
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }
  return theme;
}

export function applyTheme(theme: Theme, palette: Palette): void {
  const resolved = resolve(theme);
  const root = document.documentElement;
  root.classList.toggle("dark", resolved === "dark");
  root.style.colorScheme = resolved;
  // "graphite" is the :root default — leave the attribute off so the
  // selectors stay simple and DevTools shows a clean <html>.
  if (palette === "graphite") {
    root.removeAttribute("data-palette");
  } else {
    root.setAttribute("data-palette", palette);
  }
}

/** Mount once at app root; keeps documentElement in sync with the
 *  user's theme + palette prefs and follows OS changes when set to
 *  "system". */
export function useThemeSync(): { theme: Theme; palette: Palette } {
  const [theme] = usePref(PREF_THEME);
  const [palette] = usePref(PREF_PALETTE);

  useEffect(() => {
    applyTheme(theme, palette);
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => applyTheme(theme, palette);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, [theme, palette]);

  return { theme, palette };
}
