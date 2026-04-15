const LEVEL_META = {
  VERIFIED: {
    color: "#16a34a",
    tip: "Verified: drawn from primary government records or filings.",
  },
  REPORTED: {
    color: "#2563eb",
    tip: "Reported: documented in reputable outlets or secondary official sources.",
  },
  ALLEGED: {
    color: "#ca8a04",
    tip: "Alleged: formal claims or complaints; not necessarily adjudicated.",
  },
  DISPUTED: {
    color: "#ea580c",
    tip: "Disputed: competing accounts or active contest in public record.",
  },
  CONTEXTUAL: {
    color: "#6b7280",
    tip: "Contextual: commentary or background; not treated as a verified fact.",
  },
};

const ORDER = ["VERIFIED", "REPORTED", "ALLEGED", "DISPUTED", "CONTEXTUAL"];

export default function EpistemicBar({
  distribution,
  showContextual = false,
}) {
  const d = distribution && typeof distribution === "object" ? distribution : {};
  const entries = ORDER.filter((k) => showContextual || k !== "CONTEXTUAL").map(
    (k) => ({
      key: k,
      count: Number(d[k]) || 0,
      ...LEVEL_META[k],
    })
  );
  const total = entries.reduce((s, e) => s + e.count, 0);
  if (total === 0) {
    return (
      <p className="oc-epistemic-bar-empty" style={{ margin: 0, fontSize: "0.8rem", color: "var(--text-muted)" }}>
        No epistemic breakdown yet for this record.
      </p>
    );
  }

  return (
    <div className="oc-epistemic-bar-wrap" aria-label="Evidence epistemic levels">
      <div        className="oc-epistemic-bar"
        style={{
          display: "flex",
          height: 10,
          borderRadius: 4,
          overflow: "hidden",
          border: "1px solid var(--border)",
        }}
      >
        {entries.map((e) => {
          if (e.count === 0) return null;
          const pct = (e.count / total) * 100;
          return (
            <span
              key={e.key}
              title={`${e.key}: ${e.count} — ${e.tip}`}
              style={{
                width: `${pct}%`,
                background: e.color,
                minWidth: e.count > 0 ? 4 : 0,
              }}
            />
          );
        })}
      </div>
      <div
        className="oc-epistemic-legend"
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "0.65rem 1rem",
          marginTop: "0.5rem",
          fontSize: "0.72rem",
          fontFamily: "var(--font-mono)",
          color: "var(--text-muted)",
        }}
      >
        {entries.map((e) =>
          e.count > 0 ? (
            <span key={e.key} title={e.tip} style={{ cursor: "help" }}>
              <span style={{ color: e.color, fontWeight: 600 }}>{e.key}</span>{" "}
              {e.count}
            </span>
          ) : null
        )}
      </div>
    </div>
  );
}
