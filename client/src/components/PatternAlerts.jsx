export default function PatternAlerts({ alerts }) {
  const list = Array.isArray(alerts) ? alerts : [];

  return (
    <section className="oc-section">
      <h2 className="oc-section-title">PATTERN ENGINE ALERTS</h2>
      {!list.length ? (
        <p className="oc-empty-note">
          No pattern alerts. FEC signal ingestion required.
        </p>
      ) : (
        list.map((a, i) => {
          const score =
            a.proximity_to_vote_score != null
              ? Number(a.proximity_to_vote_score).toFixed(3)
              : "—";
          return (
            <div key={i} className="oc-alert-card">
              <div className="oc-alert-head">
                <span>{a.rule_id || "RULE"}</span>
                <span>score: {score}</span>
              </div>
              <p className="oc-alert-body">{a.disclaimer || "—"}</p>
              <p className="oc-alert-meta">
                Donor: {a.donor_entity || "—"}
                <br />
                Window: {a.window_days != null ? `${a.window_days} days` : "—"}
              </p>
            </div>
          );
        })
      )}
    </section>
  );
}
