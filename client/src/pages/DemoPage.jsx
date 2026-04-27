import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  const [runStartedAt, setRunStartedAt] = useState(null);
  const [fecKey, setFecKey] = useState("");
  const [congressKey, setCongressKey] = useState("");
  const [maxFigures, setMaxFigures] = useState("7");
  const resultsRef = useRef(null);

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

  useEffect(() => {
    if (report && resultsRef.current) {
      resultsRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [report]);

  const customApiKeys = useMemo(() => {
    const o = {};
    if (fecKey.trim()) o.fec = fecKey.trim();
    if (congressKey.trim()) o.congress = congressKey.trim();
    return o;
  }, [fecKey, congressKey]);

  const cohortIds = useMemo(() => {
    if (!cohort?.figures?.length) return null;
    return cohort.figures.map((f) => f.id);
  }, [cohort]);

  const progressCohort = useMemo(() => {
    if (!cohort?.figures?.length) return [];
    const cap = Number(maxFigures);
    const n = !Number.isNaN(cap) && cap >= 1 ? Math.min(cap, cohort.figures.length) : cohort.figures.length;
    return cohort.figures.slice(0, n).map((f) => ({ id: f.id, name: f.name }));
  }, [cohort, maxFigures]);

  const sharePageUrl = typeof window !== "undefined" ? window.location.href : "";

  const summary = report?.cohort_summary;

  return (
    <div className="oc-demo-page">
      <header className="oc-demo-header">
        <h1>Open Case — public demo</h1>
        <p className="oc-demo-lead">
          One click runs a full investigation pass per senator (FEC, Congress votes, spending, lobbying, and related
          adapters as configured). Output is comparative, epistemically labeled, and signed — receipts, not verdicts.
        </p>
        <nav className="oc-demo-nav">
          <Link to="/">← Home</Link>
        </nav>
      </header>

      {cohortError && <p className="oc-demo-error">{cohortError}</p>}

      {cohort?.figures && !cohortError && (
        <section className="oc-demo-hero" aria-labelledby="demo-run-heading">
          <h2 id="demo-run-heading" className="oc-demo-hero-title">
            Run the cohort
          </h2>
          <p className="oc-demo-hero-caption">
            Click to run {progressCohort.length || 7} full investigations across the demo cohort (FEC + Congress + spending
            + lobbying). No browser API keys needed if the server is configured.
          </p>

          <label className="oc-demo-cap">
            Max figures (1–7)
            <input
              value={maxFigures}
              onChange={(e) => setMaxFigures(e.target.value)}
              inputMode="numeric"
              min={1}
              max={7}
              disabled={stage === "running"}
            />
          </label>

          <details className="oc-demo-power">
            <summary>Power user: optional adapter keys</summary>
            <label>
              FEC_API_KEY override
              <input
                value={fecKey}
                onChange={(e) => setFecKey(e.target.value)}
                type="password"
                autoComplete="off"
                disabled={stage === "running"}
              />
            </label>
            <label>
              CONGRESS_API_KEY override
              <input
                value={congressKey}
                onChange={(e) => setCongressKey(e.target.value)}
                type="password"
                autoComplete="off"
                disabled={stage === "running"}
              />
            </label>
          </details>

          <div className="oc-demo-hero-button-wrap">
            <RunDemoButton
              cohortIds={cohortIds}
              customApiKeys={customApiKeys}
              maxFigures={maxFigures}
              onBegin={() => {
                setRunStartedAt(Date.now());
                setRunError(null);
                setStage("running");
              }}
              onComplete={(data) => {
                setReport(data);
                setRunError(null);
                setStage("complete");
              }}
              onError={(msg) => {
                setRunError(msg);
                setStage("idle");
                setRunStartedAt(null);
              }}
            />
          </div>

          <InvestigationProgress
            running={stage === "running"}
            complete={stage === "complete"}
            errorMessage={stage === "idle" && runError ? runError : null}
            cohort={progressCohort}
            startedAt={runStartedAt}
          />
        </section>
      )}

      {cohort?.figures && (
        <section className="oc-demo-section">
          <h2>Cohort ({cohort.figures.length})</h2>
          <ul className="oc-demo-cohort">
            {cohort.figures.map((f) => (
              <li key={f.id}>
                <strong>{f.name}</strong> ({f.party}-{f.state}){" "}
                <Link to={`/official/${f.bioguide_id}`}>Profile</Link>
                {" · "}
                <Link to={`/demo/figure/${encodeURIComponent(f.id)}`}>Demo detail</Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {report && (
        <div ref={resultsRef} id="demo-results" className="oc-demo-results-block">
          <section className="oc-demo-section">
            <h2>Comparative summary</h2>
            <p className="oc-demo-muted">{report.philosophy_note}</p>
            {summary && (
              <div className="oc-demo-summary-cards">
                <div className="oc-demo-summary-card">
                  <span className="oc-demo-summary-label">Cohort size</span>
                  <span className="oc-demo-summary-value">{summary.cohort_size}</span>
                </div>
                <div className="oc-demo-summary-card">
                  <span className="oc-demo-summary-label">Party breakdown</span>
                  <span className="oc-demo-summary-value">
                    {typeof summary.party_breakdown === "object"
                      ? Object.entries(summary.party_breakdown)
                          .map(([k, v]) => `${k}: ${v}`)
                          .join(" · ")
                      : String(summary.party_breakdown)}
                  </span>
                </div>
              </div>
            )}
            {summary?.top_patterns && (
              <div className="oc-demo-top-patterns">
                <h3>Top patterns</h3>
                <ul>
                  {summary.top_patterns.map((p, i) => (
                    <li key={i}>{p}</li>
                  ))}
                </ul>
              </div>
            )}
          </section>

          <section className="oc-demo-section">
            <h2>Figures</h2>
            <ul className="oc-demo-results">
              {report.figures.map((fig) => {
                const claimCount = Array.isArray(fig.claims) ? fig.claims.length : 0;
                return (
                  <li key={fig.figure_id} className="oc-demo-figure-card">
                    <h3>
                      {fig.name}{" "}
                      <span className="oc-demo-muted">
                        ({fig.party}-{fig.state})
                      </span>
                    </h3>
                    <p className="oc-demo-figure-meta">
                      {claimCount} highlight{claimCount === 1 ? "" : "s"} ·{" "}
                      {fig.error ? (
                        <span className="oc-demo-error">Run error</span>
                      ) : (
                        <span className="oc-demo-ok">Receipt available</span>
                      )}
                    </p>
                    {fig.error && <p className="oc-demo-error">Error: {fig.error}</p>}
                    <div className="oc-demo-figure-actions">
                      {fig.share_report_url && (
                        <a href={fig.share_report_url} className="oc-demo-action-btn">
                          View signed report →
                        </a>
                      )}
                      {fig.bioguide_id && (
                        <Link to={`/official/${fig.bioguide_id}`} className="oc-demo-action-btn">
                          View profile →
                        </Link>
                      )}
                      <Link to={`/demo/figure/${encodeURIComponent(fig.figure_id)}`} className="oc-demo-action-btn">
                        Demo detail →
                      </Link>
                    </div>
                    <ul className="oc-demo-claims">
                      {(fig.claims || []).slice(0, 6).map((c, i) => (
                        <li key={i}>
                          <span className={`oc-demo-epi oc-demo-epi--${(c.label || "").toLowerCase()}`}>
                            [{c.label}]
                          </span>{" "}
                          {c.text}
                        </li>
                      ))}
                    </ul>
                  </li>
                );
              })}
            </ul>
          </section>

          <DemoExportPanel report={report} />

          <section className="oc-demo-section">
            <h2>Shareable page URL</h2>
            <p className="oc-demo-muted">Copy this page after a run; exports are in the panel above.</p>
            <input className="oc-demo-share-input" readOnly value={sharePageUrl} />
          </section>
        </div>
      )}
    </div>
  );
}
