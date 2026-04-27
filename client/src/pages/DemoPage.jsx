import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import RunDemoButton from "../components/demo/RunDemoButton.jsx";
import InvestigationProgress from "../components/demo/InvestigationProgress.jsx";
import DemoExportPanel from "../components/demo/DemoExportPanel.jsx";
import "./DemoPage.css";

export default function DemoPage() {
  const [cohort, setCohort] = useState(null);
  const [cohortError, setCohortError] = useState(null);
  const [report, setReport] = useState(null);
  const [runError, setRunError] = useState(null);
  const [stage, setStage] = useState("idle");
  const [fecKey, setFecKey] = useState("");
  const [congressKey, setCongressKey] = useState("");
  const [maxFigures, setMaxFigures] = useState("");

  const loadCohort = useCallback(async () => {
    setCohortError(null);
    try {
      const res = await fetch("/api/v1/demo/cohort", { headers: { Accept: "application/json" } });
      if (!res.ok) {
        setCohortError(
          res.status === 404
            ? "Public demo is not enabled on this server."
            : `Could not load cohort (HTTP ${res.status}).`,
        );
        setCohort(null);
        return;
      }
      const data = await res.json();
      setCohort(data);
    } catch (e) {
      setCohortError(e?.message || "Network error");
      setCohort(null);
    }
  }, []);

  useEffect(() => {
    loadCohort();
  }, [loadCohort]);

  const customApiKeys = useMemo(() => {
    const o = {};
    if (fecKey.trim()) o.fec = fecKey.trim();
    if (congressKey.trim()) o.congress = congressKey.trim();
    return o;
  }, [fecKey, congressKey]);

  const sharePageUrl = typeof window !== "undefined" ? window.location.href : "";

  return (
    <div className="oc-demo-page">
      <header className="oc-demo-header">
        <h1>Open Case — public demo</h1>
        <p className="oc-demo-lead">
          One request runs a full investigation pass per senator in the cohort (FEC, Congress votes, lobbying, spending,
          biographical paths as configured). Outputs are comparative summaries with epistemic labels and signed receipts —
          not verdicts.
        </p>
        <nav className="oc-demo-nav">
          <Link to="/">← Home</Link>
        </nav>
      </header>

      {cohortError && <p className="oc-demo-error">{cohortError}</p>}

      {cohort?.figures && (
        <section className="oc-demo-section">
          <h2>Cohort ({cohort.figures.length})</h2>
          <ul className="oc-demo-cohort">
            {cohort.figures.map((f) => (
              <li key={f.id}>
                <strong>{f.name}</strong> ({f.party}-{f.state}){" "}
                <Link to={`/official/${f.bioguide_id}`}>Profile</Link>
                {" · "}
                <a href={`/api/v1/demo/figure/${encodeURIComponent(f.id)}`}>Demo JSON</a>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="oc-demo-section">
        <h2>Run</h2>
        <p className="oc-demo-muted">
          No API key is required in the browser when the server has adapter keys (e.g. on Render). This can take several
          minutes for all seven figures.
        </p>
        <details className="oc-demo-power">
          <summary>Power user: optional adapter keys</summary>
          <label>
            FEC_API_KEY override
            <input value={fecKey} onChange={(e) => setFecKey(e.target.value)} type="password" autoComplete="off" />
          </label>
          <label>
            CONGRESS_API_KEY override
            <input
              value={congressKey}
              onChange={(e) => setCongressKey(e.target.value)}
              type="password"
              autoComplete="off"
            />
          </label>
        </details>
        <label className="oc-demo-cap">
          Max figures (optional smoke test, 1–7)
          <input value={maxFigures} onChange={(e) => setMaxFigures(e.target.value)} inputMode="numeric" placeholder="7" />
        </label>
        <RunDemoButton
          customApiKeys={customApiKeys}
          maxFigures={maxFigures}
          onBegin={() => {
            setStage("running");
            setRunError(null);
          }}
          onComplete={(data) => {
            setReport(data);
            setRunError(null);
            setStage("complete");
          }}
          onError={(msg) => {
            setRunError(msg);
            setStage("idle");
          }}
        />
        <InvestigationProgress
          stage={stage}
          message={stage === "complete" ? "Report ready below." : runError || ""}
        />
        {runError && <p className="oc-demo-error">{runError}</p>}
      </section>

      {report && (
        <>
          <section className="oc-demo-section">
            <h2>Comparative summary</h2>
            <p className="oc-demo-muted">{report.philosophy_note}</p>
            <pre className="oc-demo-pre">{JSON.stringify(report.cohort_summary, null, 2)}</pre>
          </section>

          <section className="oc-demo-section">
            <h2>Figures</h2>
            <ul className="oc-demo-results">
              {report.figures.map((fig) => (
                <li key={fig.figure_id}>
                  <h3>
                    {fig.name}{" "}
                    <span className="oc-demo-muted">
                      ({fig.party}-{fig.state})
                    </span>
                  </h3>
                  {fig.error && <p className="oc-demo-error">Error: {fig.error}</p>}
                  {fig.share_report_url && (
                    <p>
                      <a href={fig.share_report_url}>Signed HTML report</a>
                      {fig.case_id && (
                        <>
                          {" · "}
                          <Link to={`/official/${fig.bioguide_id}`}>Client profile</Link>
                        </>
                      )}
                    </p>
                  )}
                  <ul>
                    {(fig.claims || []).slice(0, 6).map((c, i) => (
                      <li key={i}>
                        <span className="oc-demo-epi">[{c.label}]</span> {c.text}
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          </section>

          <DemoExportPanel report={report} />

          <section className="oc-demo-section">
            <h2>Shareable page URL</h2>
            <p className="oc-demo-muted">Copy this page after a run; exports are also in the payload above.</p>
            <input className="oc-demo-share-input" readOnly value={sharePageUrl} />
          </section>
        </>
      )}
    </div>
  );
}
