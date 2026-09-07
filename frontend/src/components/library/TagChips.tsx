/**
 * Read-only tag chip row used by the detail panel and the table.
 * Optional `onClick(tag)` makes chips actionable (e.g. click to filter).
 */
export default function TagChips({
  tags,
  onClick,
  max,
  size = "sm",
}: {
  tags: string[];
  onClick?: (tag: string) => void;
  /** Render at most this many chips and append "+N" when truncated. */
  max?: number;
  size?: "xs" | "sm";
}) {
  if (!tags || tags.length === 0) return null;

  const shown = max != null ? tags.slice(0, max) : tags;
  const hidden = tags.length - shown.length;
  const cls =
    size === "xs"
      ? "px-1.5 py-px text-[10px]"
      : "px-2 py-0.5 text-xs";

  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {shown.map((t) => {
        const Tag: keyof React.JSX.IntrinsicElements = onClick ? "button" : "span";
        return (
          <Tag
            key={t}
            type={onClick ? "button" : undefined}
            onClick={
              onClick
                ? (e: React.MouseEvent) => {
                    e.stopPropagation();
                    onClick(t);
                  }
                : undefined
            }
            className={`inline-flex items-center rounded-full ${cls} ${onClick ? "cursor-pointer hover:opacity-80" : ""}`}
            style={{
              backgroundColor:
                "color-mix(in srgb, var(--color-accent) 15%, transparent)",
              color: "var(--color-accent)",
            }}
          >
            {t}
          </Tag>
        );
      })}
      {hidden > 0 && (
        <span
          className={`inline-flex items-center rounded-full ${cls}`}
          style={{
            color: "var(--color-text-muted)",
            backgroundColor: "transparent",
          }}
        >
          +{hidden}
        </span>
      )}
    </span>
  );
}
