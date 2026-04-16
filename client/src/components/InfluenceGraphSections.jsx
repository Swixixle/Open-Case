function fmtMoney(n) {
  if (n == null || Number.isNaN(Number(n))) return "—";
  const x = Number(n);
  if (x >= 1e6) return `$${(x / 1e6).toFixed(2)}M`;
  if (x >= 1e3) return `$${(x / 1e3).toFixed(1)}K`;
  return `$${x.toFixed(0)}`;
}

export default function InfluenceGraphSections({ dossier }) {
  const dark = Array.isArray(dossier?.dark_money) ? dossier.dark_money : [];
  const travel = Array.isArray(dossier?.ethics_travel) ? dossier.ethics_travel : [];
  const witnesses = Array.isArray(dossier?.committee_witnesses)
    ? dossier.committee_witnesses
    : [];

  if (!dark.length && !travel.length && !witnesses.length) return null;

  return (
    <>
      {dark.length ? (
        <section className="oc-section" id="dark-money">
          <h2 className="oc-section-title">DARK MONEY — NONPROFIT CONNECTIONS</h2>
          <div className="oc-table-wrap">
            <table className="oc-table">
              <thead>
                <tr>
                  <th>ORG</th>
                  <th>TYPE</th>
                  <th>REVENUE</th>
                  <th>POLITICAL SPEND</th>
                  <th>CONNECTED DONOR</th>
                  <th>PASS-THROUGH</th>
                </tr>
              </thead>
              <tbody>
                {dark.map((r, i) => (
                  <tr key={i}>
                    <td>{r.org_name || "—"}</td>
                    <td>{r.org_type || "—"}</td>
                    <td>{fmtMoney(r.total_revenue)}</td>
                    <td>{fmtMoney(r.political_expenditures)}</td>
                    <td>{r.fec_donor_name || "—"}</td>
                    <td
                      className={
                        (r.pass_through_entities || []).length
                          ? "oc-overlap-warn"
                          : ""
                      }
                    >
                      {(r.pass_through_entities || []).length
                        ? `\u26A0\uFE0F ${(r.pass_through_entities || []).join(", ")}`
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      {travel.length ? (
        <section className="oc-section" id="ethics-travel">
          <h2 className="oc-section-title">GIFTS &amp; SPONSORED TRAVEL</h2>
          <div className="oc-table-wrap">
            <table className="oc-table">
              <thead>
                <tr>
                  <th>SPONSOR</th>
                  <th>TYPE</th>
                  <th>VALUE</th>
                  <th>DESTINATION</th>
                  <th>DATE</th>
                  <th>DONOR MATCH</th>
                </tr>
              </thead>
              <tbody>
                {travel.map((r, i) => (
                  <tr key={i}>
                    <td>{r.sponsor_name || "—"}</td>
                    <td>{r.sponsor_type || "—"}</td>
                    <td>{fmtMoney(r.value)}</td>
                    <td>{r.destination || "—"}</td>
                    <td>{r.date || "—"}</td>
                    <td
                      className={
                        r.fec_donor_match || r.lda_match ? "oc-overlap-warn" : ""
                      }
                    >
                      {r.fec_donor_match || r.lda_match
                        ? `\u26A0\uFE0F FEC:${r.fec_donor_match ? "Y" : "N"} LDA:${r.lda_match ? "Y" : "N"}`
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      {witnesses.length ? (
        <section className="oc-section" id="committee-witnesses">
          <h2 className="oc-section-title">COMMITTEE WITNESSES — DONOR OVERLAP</h2>
          <div className="oc-table-wrap">
            <table className="oc-table">
              <thead>
                <tr>
                  <th>HEARING</th>
                  <th>WITNESS</th>
                  <th>AFFILIATION</th>
                  <th>DONOR MATCH</th>
                  <th>AMOUNT</th>
                </tr>
              </thead>
              <tbody>
                {witnesses.map((r, i) => (
                  <tr key={i}>
                    <td>{r.hearing_title || "—"}</td>
                    <td>{r.witness_name || "—"}</td>
                    <td>{r.witness_affiliation || "—"}</td>
                    <td
                      className={
                        r.fec_donor_match || r.lda_match ? "oc-overlap-warn" : ""
                      }
                    >
                      {r.fec_donor_match || r.lda_match
                        ? `\u26A0\uFE0F FEC:${r.fec_donor_match ? "Y" : "N"} LDA:${r.lda_match ? "Y" : "N"}`
                        : "—"}
                    </td>
                    <td>{fmtMoney(r.donation_amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </>
  );
}
