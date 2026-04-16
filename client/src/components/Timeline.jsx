import { parseSourceDomain } from "../lib/dossierParse.js";
import {
  buildRefUrlMapForApiCategory,
  parseBracketRefIds,
  resolvedClaimSourceUrls,
  urlForRefIndex,
} from "../lib/sourceRefResolve.js";

function claimYear(dateStr) {
  if (!dateStr) return "—";
  const m = String(dateStr).match(/(\d{4})/);
  return m ? m[1] : String(dateStr).slice(0, 4) || "—";
}

export default function Timeline({ claims, refUrlMap, dossier }) {
  if (!Array.isArray(claims) || claims.length < 3) return null;

  const fallbackMap = refUrlMap instanceof Map ? refUrlMap : new Map();

  return (
    <section className="oc-section" aria-label="Timeline of findings">
      <h2 className="oc-section-title">TIMELINE</h2>
      <div className="oc-timeline">
        {claims.map((c, i) => {
          const scoped =
            dossier && c._sourceCategoryKey
              ? buildRefUrlMapForApiCategory(dossier, String(c._sourceCategoryKey))
              : new Map();
          const map = scoped.size > 0 ? scoped : fallbackMap;
          const raw = String(c.source || "");
          const bracketIds = parseBracketRefIds(raw);
          const urls = resolvedClaimSourceUrls(c, map);
          const httpDirect = raw.startsWith("http") ? raw : null;
          const primary = urls[0] || httpDirect;
          const sev =
            (c.allegation_status || "").toLowerCase() === "substantiated"
              ? "HIGH"
              : null;
          const openPrimary = () => {
            if (primary) window.open(primary, "_blank", "noopener,noreferrer");
          };
          const interactive = Boolean(primary);
          return (
            <div
              key={i}
              className={`oc-timeline-item${interactive ? " oc-claim-card--interactive" : ""}`}
              role={interactive ? "button" : undefined}
              tabIndex={interactive ? 0 : undefined}
              onClick={interactive ? openPrimary : undefined}
              onKeyDown={
                interactive
                  ? (e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        openPrimary();
                      }
                    }
                  : undefined
              }
            >
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
                {bracketIds.length ? (
                  <span className="oc-source-ref-badges">
                    {bracketIds.map((n) => {
                      const href = urlForRefIndex(n, map, c.sources);
                      const label = `[${n}]`;
                      if (href) {
                        return (
                          <a
                            key={n}
                            className="oc-source-ref-badge oc-source-ref-badge--link"
                            href={href}
                            target="_blank"
                            rel="noopener noreferrer"
                            onClick={(e) => e.stopPropagation()}
                          >
                            {label}
                          </a>
                        );
                      }
                      return (
                        <span key={n} className="oc-source-ref-badge">
                          {label}
                        </span>
                      );
                    })}
                  </span>
                ) : httpDirect ? (
                  <a
                    href={httpDirect}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {parseSourceDomain(httpDirect) || "link"} →
                  </a>
                ) : (
                  <span>{parseSourceDomain(raw) || raw.trim() || "—"}</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
