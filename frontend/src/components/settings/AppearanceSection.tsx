import { Monitor, Moon, Sun } from "lucide-react";
import {
  PREF_PALETTE,
  PREF_SHOW_LANDING,
  PREF_THEME,
  usePref,
  type Palette,
  type Theme,
} from "@/auth/prefs";

const THEME_OPTIONS: { value: Theme; label: string; Icon: typeof Sun }[] = [
  { value: "system", label: "System", Icon: Monitor },
  { value: "light", label: "Light", Icon: Sun },
  { value: "dark", label: "Dark", Icon: Moon },
];

// Each swatch shows: [page background, surface, accent]. The triplet
// is enough to recognise the palette without staring at long hex
// values — the eye reads it as "warm/cool" + "muted/loud accent".
// Hex values mirror the light-mode tokens in index.css.
const PALETTE_OPTIONS: {
  value: Palette;
  label: string;
  blurb: string;
  swatch: { bg: string; surface: string; accent: string };
}[] = [
  {
    value: "graphite",
    label: "Graphite",
    blurb: "Neutral cool paper with signal-yellow accents.",
    swatch: { bg: "#f4f4f3", surface: "#fbfbfa", accent: "#c79400" },
  },
  {
    value: "blueprint",
    label: "Blueprint",
    blurb: "Chalk paper, navy ink, engineering cyan.",
    swatch: { bg: "#eef1f5", surface: "#f7f9fc", accent: "#0571c5" },
  },
  {
    value: "drafting",
    label: "Drafting",
    blurb: "Warm drafting paper with red correction ink.",
    swatch: { bg: "#f4eed8", surface: "#faf6e6", accent: "#b32424" },
  },
  {
    value: "carbon",
    label: "Carbon",
    blurb: "Pure greyscale — phosphor-green in dark mode.",
    swatch: { bg: "#fafafa", surface: "#ffffff", accent: "#137a2a" },
  },
];

export default function AppearanceSection() {
  const [showLanding, setShowLanding] = usePref(PREF_SHOW_LANDING);
  const [theme, setTheme] = usePref(PREF_THEME);
  const [palette, setPalette] = usePref(PREF_PALETTE);

  return (
    <section
      className="mb-6 rounded border p-4"
      style={{
        backgroundColor: "var(--color-surface)",
        borderColor: "var(--color-border)",
      }}
    >
      <h2 className="mb-3 text-sm font-medium">Appearance</h2>

      <div className="mb-5">
        <p className="mb-2 text-xs" style={{ color: "var(--color-text-muted)" }}>
          Palette
        </p>
        <div
          className="grid grid-cols-2 gap-2 sm:grid-cols-4"
          role="radiogroup"
          aria-label="Palette"
        >
          {PALETTE_OPTIONS.map(({ value, label, blurb, swatch }) => {
            const active = palette === value;
            return (
              <button
                key={value}
                role="radio"
                aria-checked={active}
                onClick={() => setPalette(value)}
                title={blurb}
                className="group flex flex-col gap-2 rounded-lg border p-2 text-left transition-shadow hover:shadow-sm"
                style={{
                  borderColor: active
                    ? "var(--color-accent)"
                    : "var(--color-border)",
                  // Subtle accent-tinted background on the active tile so
                  // the choice reads at a glance without overwhelming the
                  // swatch.
                  backgroundColor: active
                    ? "color-mix(in srgb, var(--color-accent) 8%, transparent)"
                    : "transparent",
                  boxShadow: active ? "0 0 0 1px var(--color-accent)" : undefined,
                }}
              >
                {/* Swatch strip: bg | surface | accent. A miniature preview
                    of what the chrome will look like once selected. */}
                <div
                  className="flex h-10 overflow-hidden rounded border"
                  style={{ borderColor: "var(--color-border)" }}
                  aria-hidden="true"
                >
                  <div className="flex-1" style={{ backgroundColor: swatch.bg }} />
                  <div
                    className="flex-1"
                    style={{ backgroundColor: swatch.surface }}
                  />
                  <div className="w-3" style={{ backgroundColor: swatch.accent }} />
                </div>
                <div className="flex items-center justify-between gap-1">
                  <span className="text-sm font-medium">{label}</span>
                  {active && (
                    <span
                      className="font-mono text-[10px] uppercase tracking-wider"
                      style={{ color: "var(--color-accent)" }}
                    >
                      Active
                    </span>
                  )}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      <div className="mb-4">
        <p className="mb-2 text-xs" style={{ color: "var(--color-text-muted)" }}>
          Theme
        </p>
        <div
          className="inline-flex overflow-hidden rounded border text-xs"
          style={{ borderColor: "var(--color-border)" }}
          role="radiogroup"
          aria-label="Theme"
        >
          {THEME_OPTIONS.map(({ value, label, Icon }) => {
            const active = theme === value;
            return (
              <button
                key={value}
                role="radio"
                aria-checked={active}
                onClick={() => setTheme(value)}
                className="flex items-center gap-1.5 px-3 py-1.5"
                style={{
                  backgroundColor: active
                    ? "var(--color-accent)"
                    : "var(--color-surface)",
                  color: active ? "#fff" : "var(--color-text-muted)",
                }}
              >
                <Icon className="h-3.5 w-3.5" />
                {label}
              </button>
            );
          })}
        </div>
      </div>

      <label className="flex cursor-pointer items-start gap-3">
        <input
          type="checkbox"
          checked={showLanding}
          onChange={(e) => setShowLanding(e.target.checked)}
          className="mt-0.5 h-4 w-4 cursor-pointer"
        />
        <span className="text-sm">
          Show search landing page on sign-in
          <span
            className="block text-xs"
            style={{ color: "var(--color-text-muted)" }}
          >
            When off, sign-in lands directly on the library list.
          </span>
        </span>
      </label>
    </section>
  );
}
