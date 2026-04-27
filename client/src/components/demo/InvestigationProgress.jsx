import { useEffect, useState } from "react";

/**
 * Full batch is one HTTP request; we show elapsed-time-based queue position as a rough cue.
 */
export default function InvestigationProgress({
  running,
  cohort,
  startedAt,
  complete,
  errorMessage,
}) {
  const [, setTick] = useState(0);

  useEffect(() => {
    if (!running) return undefined;
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [running]);

  if (errorMessage) {
    return (
      <div className="oc-demo-progress-wrap" role="status">
        <p className="oc-demo-progress-error">{errorMessage}</p>
      </div>
    );
  }

  if (complete) {
    return (
      <div className="oc-demo-progress-wrap oc-demo-progress-wrap--done" role="status">
        <p className="oc-demo-progress-done">All investigations complete — results below.</p>
      </div>
    );
  }

  if (!running) return null;

  const list = Array.isArray(cohort) ? cohort : [];
  const n = list.length || 7;
  const elapsedMs = startedAt ? Date.now() - startedAt : 0;
  const step = Math.min(n, Math.max(1, Math.floor(elapsedMs / 48000) + 1));
  const current = list[step - 1];
  const name = current?.name || "…";

  return (
    <div className="oc-demo-progress-wrap" role="status" aria-live="polite">
      <div className="oc-demo-progress-bar-track" aria-hidden="true">
        <div className="oc-demo-progress-bar-indeterminate" />
      </div>
      <p className="oc-demo-progress-main">
        <span className="oc-demo-spinner" aria-hidden="true" />
        Running investigations…
      </p>
      <p className="oc-demo-progress-detail">
        Investigating: <strong>{name}</strong> ({step}/{n})
      </p>
      <p className="oc-demo-progress-hint">
        One server request processes the cohort in order; timing depends on FEC, Congress, and network load.
      </p>
    </div>
  );
}
