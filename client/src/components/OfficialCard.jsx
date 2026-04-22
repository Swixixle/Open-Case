import { Link } from "react-router-dom";
import ConcernBadge from "./ConcernBadge.jsx";
import CongressPortrait from "./CongressPortrait.jsx";
import { subjectTypeLabel } from "../lib/subjectLabels.js";

function formatUpdated(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso.includes("T") ? iso : `${iso}T12:00:00Z`);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  } catch {
    return iso;
  }
}

function officialHref(row) {
  if (row.case_id) return `/official/${row.case_id}`;
  if (row.bioguide_id) return `/official/${row.bioguide_id}`;
  return null;
}

function formatPatternScore(n) {
  if (n == null || Number.isNaN(Number(n))) return "—";
  return Number(n).toFixed(3);
}

export default function OfficialCard({
  name,
  title = "",
  state,
  party,
  bioguide_id,
  case_id,
  subject_type = "public_official",
  concern_tier = "MODERATE",
  finding_count = 0,
  last_updated = "",
  is_building = false,
  pattern_top_score = null,
}) {
  const href = officialHref({ case_id, bioguide_id });
  const typeLabel = subjectTypeLabel(subject_type);
  const meta = [state, party].filter(Boolean).join(" · ");

  const body = (
    <>
      <div className="oc-card-meta">
        {meta || "—"}
        {title ? ` · ${title}` : ""}
      </div>
      <h2 className="oc-card-name">{name.toUpperCase()}</h2>
      <div className="oc-card-rule" aria-hidden />
      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", alignItems: "center", marginBottom: "0.5rem" }}>
        <ConcernBadge level={concern_tier} size="sm" />
        <span
          className="oc-mono"
          style={{
            fontSize: "0.65rem",
            letterSpacing: "0.05em",
            color: "var(--text-muted)",
            border: "1px solid var(--border-bright)",
            padding: "0.15rem 0.4rem",
            borderRadius: 4,
          }}
        >
          {typeLabel}
        </span>
      </div>
      <p className="oc-card-findings">
        {finding_count} documented {finding_count === 1 ? "finding" : "findings"}
        {" · "}
        Top alert {formatPatternScore(pattern_top_score)}
      </p>
      {is_building ? (
        <p className="oc-card-updated">Record build in progress…</p>
      ) : last_updated ? (
        <p className="oc-card-updated">Investigated {formatUpdated(last_updated)}</p>
      ) : null}
    </>
  );

  const inner = (
    <div className="oc-card-inner">
      {bioguide_id ? (
        <CongressPortrait bioguideId={bioguide_id} name={name} variant="card" />
      ) : null}
      <div className="oc-card-body">{body}</div>
    </div>
  );

  if (!href) {
    return (
      <div className="oc-card oc-card--muted" aria-disabled>
        {inner}
        <p className="oc-card-updated" style={{ marginTop: "0.5rem" }}>
          No case link yet — use search to open an investigation.
        </p>
      </div>
    );
  }

  return (
    <Link className="oc-card" to={href}>
      {inner}
    </Link>
  );
}
