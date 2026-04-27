import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { apiUrl } from "../lib/api.js";
import "./DemoPage.css";

export default function DemoFigurePage() {
  const { figureId } = useParams();
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setErr(null);
      setData(null);
      try {
        const res = await fetch(apiUrl(`/api/v1/demo/figure/${encodeURIComponent(figureId)}`), {
          headers: { Accept: "application/json" },
        });
        if (!res.ok) {
          const t = await res.text();
          if (!cancelled) setErr(t.slice(0, 500) || `HTTP ${res.status}`);
          return;
        }
        const json = await res.json();
        if (!cancelled) setData(json);
      } catch (e) {
        if (!cancelled) setErr(e?.message || "Request failed");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [figureId]);

  return (
    <div className="oc-demo-page">
      <header className="oc-demo-header">
        <h1>Demo figure detail</h1>
        <nav className="oc-demo-nav">
          <Link to="/demo">← Demo</Link>
          {" · "}
          <Link to="/">Home</Link>
        </nav>
      </header>
      {err && <p className="oc-demo-error">{err}</p>}
      {data && (
        <>
          <section className="oc-demo-section">
            <h2>
              {data.name}{" "}
              <span className="oc-demo-muted">
                ({data.party}-{data.state})
              </span>
            </h2>
            {data.share_report_url && (
              <p>
                <a href={data.share_report_url} className="oc-demo-text-link">
                  View signed HTML report →
                </a>
              </p>
            )}
            {data.case_id ? (
              <p>
                <Link to={`/official/${data.case_id}`} className="oc-demo-text-link">
                  Investigation profile (case) →
                </Link>
              </p>
            ) : (
              data.bioguide_id && (
                <p>
                  <Link to={`/official/${data.bioguide_id}`} className="oc-demo-text-link">
                    Profile (bioguide) →
                  </Link>
                </p>
              )
            )}
          </section>
          <section className="oc-demo-section">
            <h3>Claims (sample)</h3>
            <ul className="oc-demo-claims">
              {(data.claims || []).slice(0, 12).map((c, i) => (
                <li key={i}>
                  <span className={`oc-demo-epi oc-demo-epi--${(c.label || "").toLowerCase()}`}>
                    [{c.label}]
                  </span>{" "}
                  {c.text}
                </li>
              ))}
            </ul>
          </section>
          <section className="oc-demo-section">
            <h3>Raw JSON</h3>
            <pre className="oc-demo-pre">{JSON.stringify(data, null, 2)}</pre>
          </section>
        </>
      )}
    </div>
  );
}
