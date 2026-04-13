import { parseSourceDomain } from "../lib/dossierParse.js";

function claimYear(dateStr) {
  if (!dateStr) return "—";
  const m = String(dateStr).match(/(\d{4})/);
  return m ? m[1] : String(dateStr).slice(0, 4) || "—";
}

export default function Timeline({ claims }) {
  if (!Array.isArray(claims) || claims.length < 3) return null;

  return (
    <section className="oc-section" aria-label="Timeline of findings">
      <h2 className="oc-section-title">TIMELINE</h2>
      <div className="oc-timeline">
        {claims.map((c, i) => {
          const domain = parseSourceDomain(c.source);
          const url = String(c.source || "").startsWith("http")
            ? c.source
            : null;
          const sev =
            (c.allegation_status || "").toLowerCase() === "substantiated"
              ? "HIGH"
              : null;
          return (
            <div key={i} className="oc-timeline-item">
              <div className="oc-timeline-head">
                <span className="oc-timeline-year">{claimYear(c.date)}</span>
                <span className="oc-tag-cat">
                  [{c._categoryLabel || c._categoryKey}]
                </span>
                {sev ? <span className="oc-tag-sev">● {sev}</span> : null}
              </div>
              <p className="oc-timeline-body">
                {(c.claim || c.text || "").slice(0, 280)}
                {(c.claim || "").length > 280 ? "…" : ""}
              </p>
              <div className="oc-timeline-source">
                Source:{" "}
                {url ? (
                  <a href={url} target="_blank" rel="noopener noreferrer">
                    {domain || "link"} →
                  </a>
                ) : (
                  <span>{domain || "—"}</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
