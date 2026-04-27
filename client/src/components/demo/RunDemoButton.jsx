import { useState } from "react";
import { apiUrl } from "../../lib/api.js";

/**
 * POST /api/v1/demo/investigate — public when server sets OPEN_CASE_PUBLIC_DEMO=1.
 * Styling matches app primary actions (oc-btn-sidebar: amber on dark card).
 */
export default function RunDemoButton({
  onBegin,
  onComplete,
  onError,
  customApiKeys,
  maxFigures,
  cohortIds,
}) {
  const [loading, setLoading] = useState(false);

  const handleRun = async () => {
    setLoading(true);
    onBegin?.();
    try {
      const body = {};
      if (Array.isArray(cohortIds) && cohortIds.length > 0) {
        body.cohort = cohortIds;
      }
      if (customApiKeys && Object.keys(customApiKeys).length > 0) {
        body.custom_api_keys = customApiKeys;
      }
      const n = Number(maxFigures);
      if (!Number.isNaN(n) && n >= 1 && n <= 7) {
        body.max_figures = n;
      }
      const res = await fetch(apiUrl("/api/v1/demo/investigate"), {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(body),
      });
      if (res.status === 404) {
        throw new Error("Public demo is not enabled on this server (OPEN_CASE_PUBLIC_DEMO).");
      }
      if (!res.ok) {
        const t = await res.text();
        throw new Error(t.slice(0, 400) || `HTTP ${res.status}`);
      }
      const data = await res.json();
      try {
        localStorage.setItem("openCaseDemoReport", JSON.stringify(data));
      } catch {
        /* ignore quota */
      }
      onComplete?.(data);
    } catch (e) {
      onError?.(e?.message || "Unknown error");
    } finally {
      setLoading(false);
    }
  };

  return (
    <button
      type="button"
      onClick={handleRun}
      disabled={loading}
      className={`oc-btn-sidebar oc-demo-run-hero${loading ? " oc-demo-run-hero--loading" : ""}`}
    >
      {loading
        ? "RUNNING INVESTIGATIONS…"
        : "RUN DEMO INVESTIGATION →"}
    </button>
  );
}
