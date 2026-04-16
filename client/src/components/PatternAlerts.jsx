import PatternAlertCard from "./PatternAlertCard.jsx";

function sortAlerts(list) {
  return [...list].sort((a, b) => {
    const sa = Number(a?.score ?? a?.proximity_to_vote_score);
    const sb = Number(b?.score ?? b?.proximity_to_vote_score);
    const na = Number.isNaN(sa) ? -1 : sa;
    const nb = Number.isNaN(sb) ? -1 : sb;
    if (nb !== na) return nb - na;
    const ra = String(a?.rule_id || "");
    const rb = String(b?.rule_id || "");
    return ra.localeCompare(rb);
  });
}

export default function PatternAlerts({ alerts, newRuleIds }) {
  const list = Array.isArray(alerts) ? alerts : [];
  const sorted = sortAlerts(list);
  const fresh = newRuleIds instanceof Set ? newRuleIds : new Set();

  if (!sorted.length) return null;

  return (
    <section className="oc-section">
      <h2 className="oc-section-title">Pattern alerts</h2>
      {sorted.map((a, i) => (
        <PatternAlertCard
          key={`${a.rule_id || "rule"}-${i}`}
          alert={a}
          isNew={fresh.has(a.rule_id)}
        />
      ))}
    </section>
  );
}
