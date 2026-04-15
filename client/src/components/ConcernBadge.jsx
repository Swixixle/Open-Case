/** Concern / severity pill — maps legacy HIGH to SIGNIFICANT for display. */

const COLORS = {
  critical: "#dc2626",
  significant: "#d97706",
  moderate: "#ca8a04",
  low: "#16a34a",
};

const SIZES = {
  sm: { font: "0.65rem", pad: "0.2rem 0.45rem" },
  md: { font: "0.72rem", pad: "0.28rem 0.55rem" },
  lg: { font: "0.8rem", pad: "0.35rem 0.65rem" },
};

function normalizeLevel(level) {
  const u = (level || "moderate").toString().toUpperCase();
  if (u === "HIGH") return "SIGNIFICANT";
  return u;
}

export default function ConcernBadge({ level, size = "md" }) {
  const norm = normalizeLevel(level);
  const key = norm.toLowerCase();
  const color =
    COLORS[key] ||
    (key === "significant" ? COLORS.significant : COLORS.moderate);
  const sz = SIZES[size] || SIZES.md;
  const label = key === "significant" ? "SIGNIFICANT" : norm || "MODERATE";

  return (
    <span
      className="oc-concern-badge"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "0.35rem",
        fontFamily: "var(--font-mono)",
        fontSize: sz.font,
        fontWeight: 600,
        letterSpacing: "0.06em",
        textTransform: "uppercase",
        padding: sz.pad,
        borderRadius: "999px",
        border: `1px solid ${color}`,
        color,
        background: `${color}14`,
      }}
    >
      <span
        aria-hidden
        style={{
          width: size === "lg" ? 9 : 7,
          height: size === "lg" ? 9 : 7,
          borderRadius: "50%",
          background: color,
        }}
      />
      {label}
    </span>
  );
}
