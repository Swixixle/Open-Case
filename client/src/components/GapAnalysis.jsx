export default function GapAnalysis({ gaps }) {
  const list = Array.isArray(gaps) ? gaps : [];

  return (
    <section className="oc-section">
      <h2 className="oc-section-title">
        GAP ANALYSIS — DONATION TO VOTE PROXIMITY
      </h2>
      {!list.length ? (
        <p className="oc-empty-note">
          No proximity patterns detected in current dataset. Signal data requires
          FEC ingestion for this official.
        </p>
      ) : (
        list.map((g, i) => {
          const conf = (g.confidence || "medium").toLowerCase();
          const confCls =
            conf === "high"
              ? "oc-gap-confidence--high"
              : conf === "medium"
                ? "oc-gap-confidence--medium"
                : "oc-gap-confidence--low";
          const srcs = Array.isArray(g.sources) ? g.sources : [];
          const fec = srcs.find((u) => String(u).includes("fec.gov"));
          return (
            <div key={i} className="oc-gap-card">
              <p className={`oc-gap-confidence ${confCls}`}>
                {"\u26A1"} {conf.toUpperCase()} CONFIDENCE
              </p>
              <p className="oc-gap-text">{g.sentence}</p>
              <div className="oc-gap-links">
                {fec ? (
                  <a href={fec} target="_blank" rel="noopener noreferrer">
                    FEC Filing →
                  </a>
                ) : (
                  <span style={{ color: "var(--text-dim)" }}>FEC Filing →</span>
                )}
                <span style={{ color: "var(--text-dim)" }}>
                  Senate Vote Record →
                </span>
              </div>
            </div>
          );
        })
      )}
    </section>
  );
}
