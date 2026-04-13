import { Link } from "react-router-dom";

const tierClass = {
  CRITICAL: "oc-tier--critical",
  HIGH: "oc-tier--high",
  MODERATE: "oc-tier--moderate",
  LOW: "oc-tier--low",
};

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

export default function SenatorCard({
  name,
  state,
  party,
  bioguide_id,
  concern_tier = "MODERATE",
  finding_count = 0,
  last_updated = "",
}) {
  const tier = (concern_tier || "MODERATE").toUpperCase();
  const tc = tierClass[tier] || tierClass.MODERATE;

  return (
    <Link className="oc-card" to={`/senator/${bioguide_id}`}>
      <div className="oc-card-meta">
        {state} · {party}
      </div>
      <h2 className="oc-card-name">{name.toUpperCase()}</h2>
      <div className="oc-card-rule" aria-hidden />
      <div className={`oc-tier ${tc}`}>
        <span className="oc-tier-dot" aria-hidden />
        {tier}
      </div>
      <p className="oc-card-findings">
        {finding_count} documented {finding_count === 1 ? "finding" : "findings"}
      </p>
      <p className="oc-card-updated">Updated {formatUpdated(last_updated)}</p>
    </Link>
  );
}
