function yearLeft(endDate) {
  if (endDate == null || endDate === "") return "—";
  const s = String(endDate);
  const m = s.match(/(\d{4})/);
  return m ? m[1] : s.slice(0, 4) || "—";
}

export default function StaffNetwork({ staff }) {
  const rows = Array.isArray(staff) ? staff : [];

  return (
    <section className="oc-section">
      <h2 className="oc-section-title">REVOLVING DOOR — STAFF NETWORK</h2>
      {!rows.length ? (
        <p className="oc-empty-note">
          Staff network data unavailable. Congress.gov API key required for
          staff ingestion.
        </p>
      ) : (
        <div className="oc-table-wrap">
          <table className="oc-table">
            <thead>
              <tr>
                <th>NAME</th>
                <th>ROLE</th>
                <th>LEFT</th>
                <th>LOBBIES FOR</th>
                <th>OVERLAP</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td>{r.name || "—"}</td>
                  <td>{r.role_at_office || "—"}</td>
                  <td>{yearLeft(r.end_date)}</td>
                  <td>
                    {(r.lobbying_clients || []).slice(0, 4).join(", ") ||
                      "—"}
                  </td>
                  <td
                    className={
                      r.donor_overlap ? "oc-overlap-warn" : ""
                    }
                  >
                    {r.donor_overlap ? "\u26A0\uFE0F YES" : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
