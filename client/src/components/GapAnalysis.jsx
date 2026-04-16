function humanizeGapSentence(sentence) {
  const s = String(sentence || "").trim();
  if (!s) return s;
  let t = s;
  if (/fec ingestion|fec signal|requires\s*fec|ingestion may be required|ingestion required/i.test(t)) {
    t = t.replace(
      /fec ingestion[^.]*\.?/gi,
      "FEC-linked signal data is not yet available for this record; the gap is documented."
    );
    t = t.replace(
      /signal data requires\s*fec ingestion[^.]*\.?/gi,
      "Proximity analysis needs FEC-linked signals for this record; gap documented."
    );
    t = t.replace(
      /no pattern alerts[^.]*fec[^.]*\.?/gi,
      "Pattern analysis requires FEC-linked signals for this record; gap documented."
    );
    t = t.replace(
      /ingestion may be required[^.]*\.?/gi,
      "Additional source linkage for this analysis is documented as a gap."
    );
  }
  return t.trim();
}

export default function GapAnalysis({ gaps }) {
  const list = Array.isArray(gaps) ? gaps : [];

  if (!list.length) return null;

  return (
    <section className="oc-section">
      <h2 className="oc-section-title">
        GAP ANALYSIS — DONATION TO VOTE PROXIMITY
      </h2>
      {list.map((g, i) => {
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
              <p className="oc-gap-text">{humanizeGapSentence(g.sentence)}</p>
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
        })}
    </section>
  );
}
