import { useState } from "react";
import { categoryLabel } from "../lib/constants.js";
import EntityGroupedClaimList from "./EntityGroupedClaimList.jsx";
import { JUDICIAL_SUBJECT_TYPES, subjectTypeLabel } from "../lib/subjectLabels.js";

const TABS = [
  { id: "identity", label: "Identity" },
  { id: "money", label: "Money" },
  { id: "politics", label: "Politics" },
  { id: "conduct", label: "Conduct" },
  { id: "bench_record", label: "Bench record" },
  { id: "signals", label: "Signals" },
];

function EvidenceList({ rows }) {
  if (!Array.isArray(rows) || !rows.length) {
    return (
      <p className="oc-empty-note" style={{ margin: 0 }}>
        Nothing in this section yet.
      </p>
    );
  }
  return (
    <ul className="oc-six-tab-list" style={{ margin: 0, padding: 0, listStyle: "none" }}>
      {rows.slice(0, 40).map((e) => (
        <li
          key={e.id || `${e.title}-${e.entered_at}`}
          style={{
            borderBottom: "1px solid var(--border)",
            padding: "0.65rem 0",
          }}
        >
          <div style={{ fontWeight: 600, fontSize: "0.9rem" }}>{e.title || "Entry"}</div>
          {e.body ? (
            <p style={{ margin: "0.35rem 0 0", fontSize: "0.82rem", color: "var(--text-muted)" }}>
              {e.body}
            </p>
          ) : null}
          <div className="oc-mono" style={{ fontSize: "0.68rem", marginTop: 6, color: "var(--text-dim)" }}>
            {e.epistemic_level ? `${e.epistemic_level} · ` : ""}
            {e.source_name || ""}
            {e.source_url ? (
              <>
                {" "}
                <a href={e.source_url} target="_blank" rel="noopener noreferrer">
                  source →
                </a>
              </>
            ) : null}
          </div>
        </li>
      ))}
    </ul>
  );
}

function IdentityPanelReport({ sections }) {
  const block = sections?.identity?.[0];
  if (!block) {
    return <p className="oc-empty-note">No identity block returned.</p>;
  }
  const prof = block.profile;
  return (
    <div className="oc-six-tab-identity">
      {prof ? (
        <dl className="oc-receipt-row" style={{ display: "grid", gap: "0.35rem" }}>
          <div>
            <dt style={{ color: "var(--text-dim)", fontSize: "0.75rem" }}>Name</dt>
            <dd style={{ margin: 0 }}>{prof.subject_name}</dd>
          </div>
          <div>
            <dt style={{ color: "var(--text-dim)", fontSize: "0.75rem" }}>Role</dt>
            <dd style={{ margin: 0 }}>{subjectTypeLabel(prof.subject_type)}</dd>
          </div>
          <div>
            <dt style={{ color: "var(--text-dim)", fontSize: "0.75rem" }}>Level / branch</dt>
            <dd style={{ margin: 0 }}>
              {prof.government_level || "—"} · {prof.branch || "—"}
            </dd>
          </div>
        </dl>
      ) : null}
      {block.summary ? (
        <p style={{ marginTop: "1rem", lineHeight: 1.55 }}>{block.summary}</p>
      ) : null}
    </div>
  );
}

function IdentityPanelDossier({ subject }) {
  const s = subject || {};
  return (
    <div>
      <p style={{ margin: 0, lineHeight: 1.55 }}>
        <strong>{s.name || "Subject"}</strong>
        {s.state ? ` — ${s.state}` : ""}
        {s.party ? ` (${s.party})` : ""}
      </p>
      {Array.isArray(s.committees) && s.committees.length ? (
        <div style={{ marginTop: "0.75rem" }}>
          <div className="oc-sidebar-section-title" style={{ marginBottom: 6 }}>
            COMMITTEES
          </div>
          <ul style={{ margin: 0, paddingLeft: "1.1rem", color: "var(--text-muted)" }}>
            {s.committees.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function DossierCategoryPanel({ categories, keys }) {
  const cats = categories || {};
  const sections = [];
  for (const k of keys) {
    const claims = cats[k]?.claims;
    if (!Array.isArray(claims) || !claims.length) continue;
    sections.push(
      <div key={k} style={{ marginBottom: "1.25rem" }}>
        <div className="oc-sidebar-section-title" style={{ marginBottom: "0.5rem" }}>
          {categoryLabel(k).toUpperCase()}
        </div>
        <EntityGroupedClaimList claims={claims} categoryKey={k} />
      </div>
    );
  }
  if (!sections.length) {
    return (
      <p className="oc-empty-note" style={{ margin: 0 }}>
        Nothing in this section yet.
      </p>
    );
  }
  return <div>{sections}</div>;
}

function SignalsPanelReport({ sections }) {
  const sig = sections?.signals?.[0];
  if (!sig) return <p className="oc-empty-note">No packaged signals section.</p>;
  const inner = sig.signals || [];
  if (!inner.length) {
    return <p className="oc-empty-note">No scored signals on file.</p>;
  }
  return (
    <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
      {inner.slice(0, 20).map((s) => (
        <li key={s.id || s.description} style={{ padding: "0.5rem 0", borderBottom: "1px solid var(--border)" }}>
          <div className="oc-mono" style={{ fontSize: "0.72rem", color: "var(--amber-gold)" }}>
            weight {(s.weight != null ? Number(s.weight) : 0).toFixed(2)}
            {s.relevance_score != null
              ? ` · relevance ${Number(s.relevance_score).toFixed(2)}`
              : ""}
          </div>
          <div style={{ fontSize: "0.88rem" }}>
            {s.chronology_line || s.description || s.headline || "Signal"}
          </div>
        </li>
      ))}
    </ul>
  );
}

/**
 * @param {"report" | "dossier"} mode
 * @param {object} [report] full GET /cases/:id/report
 * @param {object} [dossier] senator dossier shape
 */
export default function SixTabProfile({ mode = "report", report, dossier, subjectType }) {
  const [active, setActive] = useState("identity");

  const judicial =
    subjectType && JUDICIAL_SUBJECT_TYPES.has(subjectType);

  const sections = report?.sections;
  const categories = dossier?.deep_research?.categories;
  const subject = dossier?.subject;

  return (
    <section className="oc-section oc-six-tab-profile">
      <h2 className="oc-section-title">PROFILE</h2>
      <div className="oc-six-tab-bar" role="tablist" aria-label="Profile sections">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={active === t.id}
            className={`oc-six-tab ${active === t.id ? "is-active" : ""}`}
            onClick={() => setActive(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="oc-six-tab-panel" role="tabpanel">
        {active === "identity" ? (
          mode === "report" ? (
            <IdentityPanelReport sections={sections} />
          ) : (
            <IdentityPanelDossier subject={subject} />
          )
        ) : active === "money" ? (
          mode === "report" ? (
            <EvidenceList rows={sections?.money} />
          ) : (
            <DossierCategoryPanel
              categories={categories}
              keys={["financial_disclosures"]}
            />
          )
        ) : active === "politics" ? (
          mode === "report" ? (
            <EvidenceList rows={sections?.politics} />
          ) : (
            <DossierCategoryPanel
              categories={categories}
              keys={["donor_vs_vote_record", "public_statements_vs_votes"]}
            />
          )
        ) : active === "conduct" ? (
          mode === "report" ? (
            <EvidenceList rows={sections?.conduct} />
          ) : (
            <DossierCategoryPanel
              categories={categories}
              keys={["ethics_and_investigations", "revolving_door"]}
            />
          )
        ) : active === "bench_record" ? (
          judicial ? (
            mode === "report" ? (
              <EvidenceList rows={sections?.bench_record} />
            ) : (
              <DossierCategoryPanel categories={categories} keys={["recent_news"]} />
            )
          ) : (
            <p className="oc-empty-note" style={{ margin: 0 }}>
              Bench record applies to judicial subject types. This official is not in a
              judicial role in this record.
            </p>
          )
        ) : active === "signals" ? (
          mode === "report" ? (
            <SignalsPanelReport sections={sections} />
          ) : (
            <p className="oc-empty-note" style={{ margin: 0 }}>
              See pattern alerts above for quantitative signals. Dossier signals may also
              appear in story angles and gap analysis below.
            </p>
          )
        ) : null}
      </div>
    </section>
  );
}
