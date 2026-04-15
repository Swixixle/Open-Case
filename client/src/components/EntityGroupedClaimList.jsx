import { useEffect, useMemo, useState } from "react";
import { categoryLabel } from "../lib/constants.js";
import {
  groupClaimsByEntity,
  mergeSourcesDeduped,
} from "../lib/claimEntityGroups.js";
import { formatDisplayDate, parseSourceDomain } from "../lib/dossierParse.js";

function AllegBadge({ status }) {
  const s = (status || "unknown").toLowerCase();
  let cls = "oc-badge-alleg--unknown";
  if (s === "substantiated") cls = "oc-badge-alleg--sub";
  else if (s === "filed") cls = "oc-badge-alleg--filed";
  else if (s === "dismissed") cls = "oc-badge-alleg--dismissed";
  return <span className={`oc-badge-alleg ${cls}`}>{s || "unknown"}</span>;
}

function ClaimCard({ claim, categoryKey }) {
  const src = claim.source || "";
  const domain = parseSourceDomain(src);
  const href = String(src).startsWith("http") ? src : null;
  const status =
    claim.allegation_status ||
    (String(claim.type || "").includes("alleg") ? "unknown" : "unknown");

  return (
    <div className="oc-claim-card oc-claim-card--nested">
      <div className="oc-claim-meta">
        <span>{formatDisplayDate(claim.date)}</span>
        <span>[{categoryLabel(categoryKey)}]</span>
        <span>{(claim.type || "fact").toString()}</span>
      </div>
      <p className="oc-claim-text">{claim.claim || claim.text || "—"}</p>
      <AllegBadge status={status} />
      <div className="oc-claim-source">
        Source:{" "}
        {href ? (
          <a href={href} target="_blank" rel="noopener noreferrer">
            {domain} →
          </a>
        ) : (
          <span>{domain || "—"}</span>
        )}
      </div>
    </div>
  );
}

/**
 * @param {object} props
 * @param {Record<string, unknown>[]} props.claims
 * @param {string} props.categoryKey
 */
export default function EntityGroupedClaimList({ claims, categoryKey }) {
  const groups = useMemo(() => groupClaimsByEntity(claims || []), [claims]);

  const initialEntityOpen = useMemo(() => {
    const o = {};
    groups.forEach((g, i) => {
      o[g.entityKey] = i < 2;
    });
    return o;
  }, [groups]);

  const [entityOpen, setEntityOpen] = useState(initialEntityOpen);

  const sig = useMemo(() => groups.map((g) => g.entityKey).join("|"), [groups]);
  useEffect(() => {
    const o = {};
    groups.forEach((g, i) => {
      o[g.entityKey] = i < 2;
    });
    setEntityOpen(o);
  }, [sig]);

  if (!groups.length) return null;

  return (
    <div className="oc-entity-groups">
      {groups.map((g) => {
        const open = entityOpen[g.entityKey] ?? false;
        const mergedSources = mergeSourcesDeduped(g.claims);
        return (
          <div key={g.entityKey} className="oc-entity-block">
            <button
              type="button"
              className="oc-entity-header"
              aria-expanded={open}
              onClick={() =>
                setEntityOpen((prev) => ({
                  ...prev,
                  [g.entityKey]: !open,
                }))
              }
            >
              <span className="oc-entity-name">{g.entityLabel}</span>
              <span className="oc-entity-count">{g.claims.length} facts</span>
              <span className="oc-entity-chevron" aria-hidden>
                {open ? "\u25BC" : "\u25B6"}
              </span>
            </button>
            {open ? (
              <div className="oc-entity-body">
                {mergedSources.length ? (
                  <div className="oc-entity-sources">
                    <span className="oc-entity-sources-label">Sources</span>
                    <ul className="oc-entity-sources-list">
                      {mergedSources.map((u) => {
                        const href = u.startsWith("http") ? u : null;
                        const dom = parseSourceDomain(u);
                        return (
                          <li key={u}>
                            {href ? (
                              <a href={href} target="_blank" rel="noopener noreferrer">
                                {dom} →
                              </a>
                            ) : (
                              <span>{dom}</span>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                ) : null}
                {g.claims.map((c, idx) => (
                  <ClaimCard
                    key={`${g.entityKey}-${idx}-${String(c.claim || c.text || "").slice(0, 24)}`}
                    claim={c}
                    categoryKey={categoryKey}
                  />
                ))}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
