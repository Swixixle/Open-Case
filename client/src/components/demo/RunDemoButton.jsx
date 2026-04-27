import { useState } from "react";

/**
 * Triggers POST /api/v1/demo/investigate (public when OPEN_CASE_PUBLIC_DEMO=1 on server).
 */
export default function RunDemoButton({ onBegin, onComplete, onError, customApiKeys, maxFigures }) {
  const [loading, setLoading] = useState(false);

  const handleRun = async () => {
    setLoading(true);
    onBegin?.();
    try {
      const body = {};
      if (customApiKeys && Object.keys(customApiKeys).length > 0) {
        body.custom_api_keys = customApiKeys;
      }
      if (maxFigures != null && maxFigures !== "") {
        const n = Number(maxFigures);
        if (!Number.isNaN(n)) body.max_figures = n;
      }
      const res = await fetch("/api/v1/demo/investigate", {
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
      className="oc-demo-run-btn"
    >
      {loading ? "Running investigations (this may take several minutes)…" : "Run demo: full cohort investigations"}
    </button>
  );
}
