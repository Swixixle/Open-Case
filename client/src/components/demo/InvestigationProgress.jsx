/**
 * Lightweight status line for the demo runner (batch is one request; no per-figure SSE yet).
 */
export default function InvestigationProgress({ stage, message }) {
  if (stage === "idle") return null;
  return (
    <p className="oc-demo-progress" role="status">
      {stage === "running" && "Running… "}
      {stage === "complete" && "Complete. "}
      {message}
    </p>
  );
}
