/** Informational banner for local-government case reports (fixture + live mix). */

function localPlaceLabel(report) {
  const j = (report?.jurisdiction || "").trim();
  if (j) return j.split(",")[0].trim() || j;
  const name = (report?.subject || report?.title || "").trim();
  if (name.toLowerCase().includes("indianapolis")) return "Indianapolis";
  return name || "Local";
}

export default function DataStatusBanner({ report }) {
  const place = localPlaceLabel(report);

  return (
    <section
      className="oc-data-status-banner"
      aria-label="Data status for this local investigation"
    >
      <h3 className="oc-data-status-banner__title">
        Data Status — {place} Local Investigation
      </h3>
      <div className="oc-data-status-banner__body">
        <p>
          This case runs on a mix of fixture-validated and partially live-ingested
          data. Procurement rows are validated against 14 hand-built contract records
          from Board of Public Works agendas. The IDIS campaign finance adapter is
          gap-documented on live runs pending full scraper completion. Pattern rules
          are deterministic and tested. All outputs are cryptographically signed.
        </p>
        <p>
          Live data sources will replace fixture rows as scrapers stabilize.
        </p>
      </div>
    </section>
  );
}
