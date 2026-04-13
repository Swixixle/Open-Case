import { useEffect, useMemo, useState } from "react";
import { categoryLabel } from "../lib/constants.js";
import { formatDisplayDate, parseSourceDomain } from "../lib/dossierParse.js";

function AllegBadge({ status }) {
  const s = (status || "unknown").toLowerCase();
  let cls = "oc-badge-alleg--unknown";
  if (s === "substantiated") cls = "oc-badge-alleg--sub";
  else if (s === "filed") cls = "oc-badge-alleg--filed";
  else if (s === "dismissed") cls = "oc-badge-alleg--dismissed";
  return (
    <span className={`oc-badge-alleg ${cls}`}>{s || "unknown"}</span>
  );
}

export default function ClaimsAccordion({ categories }) {
  const keys = useMemo(() => {
    return Object.keys(categories || {}).filter((k) => {
      const claims = categories[k]?.claims;
      return Array.isArray(claims) && claims.length > 0;
    });
  }, [categories]);

  const initialOpen = useMemo(() => {
    const o = {};
    keys.forEach((k, i) => {
      o[k] = i < 2;
    });
    return o;
  }, [keys]);

  const [open, setOpen] = useState(initialOpen);

  const keySig = keys.join("|");
  useEffect(() => {
    const o = {};
    keys.forEach((k, i) => {
      o[k] = i < 2;
    });
    setOpen(o);
  }, [keySig]);

  const toggle = (k) => {
    setOpen((prev) => ({ ...prev, [k]: !prev[k] }));
  };

  if (!keys.length) return null;

  return (
    <section className="oc-section" aria-label="Findings by category">
      <h2 className="oc-section-title">DOCUMENTED FINDINGS</h2>
      {keys.map((key) => {
        const block = categories[key];
        const claims = block.claims || [];
        const isOpen = open[key];
        return (
          <div key={key} id={`cat-${key}`} className="oc-accordion">
            <button
              type="button"
              className="oc-accordion-header"
              aria-expanded={isOpen}
              onClick={() => toggle(key)}
            >
              <span className="oc-accordion-dot" aria-hidden />
              <span>{categoryLabel(key).toUpperCase()}</span>
              <span className="oc-accordion-count">({claims.length})</span>
            </button>
            {isOpen ? (
              <div className="oc-accordion-body">
                {claims.map((c, idx) => {
                  const src = c.source || "";
                  const domain = parseSourceDomain(src);
                  const href = String(src).startsWith("http") ? src : null;
                  const status =
                    c.allegation_status ||
                    (String(c.type || "").includes("alleg")
                      ? "unknown"
                      : "unknown");
                  return (
                    <div key={idx} className="oc-claim-card">
                      <div className="oc-claim-meta">
                        <span>{formatDisplayDate(c.date)}</span>
                        <span>[{categoryLabel(key)}]</span>
                        <span>{(c.type || "fact").toString()}</span>
                      </div>
                      <p className="oc-claim-text">{c.claim || c.text || "—"}</p>
                      <AllegBadge status={status} />
                      <div className="oc-claim-source">
                        Source:{" "}
                        {href ? (
                          <a
                            href={href}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            {domain} →
                          </a>
                        ) : (
                          <span>{domain || "—"}</span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : null}
          </div>
        );
      })}
    </section>
  );
}
