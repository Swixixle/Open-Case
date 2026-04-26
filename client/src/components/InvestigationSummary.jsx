import { useCallback, useEffect, useRef, useState } from "react";
import MarkdownBlock from "./MarkdownBlock.jsx";
import { fetchCaseNarrative, synthesizeCaseNarrative } from "../lib/api.js";

/**
 * Accordion: fetch or generate the signed AI investigation summary for a case.
 */
export default function InvestigationSummary({ caseId }) {
  const [expanded, setExpanded] = useState(false);
  const [loadPhase, setLoadPhase] = useState("loading"); // loading | ready | error
  const [narrative, setNarrative] = useState(null);
  const [meta, setMeta] = useState(null); // { model_used, generated_at }
  const [generating, setGenerating] = useState(false);
  const [loadErr, setLoadErr] = useState("");
  const [actionErr, setActionErr] = useState("");
  const didAutoExpand = useRef(false);

  useEffect(() => {
    didAutoExpand.current = false;
  }, [caseId]);

  const load = useCallback(async () => {
    if (!caseId) return;
    setLoadPhase("loading");
    setLoadErr("");
    const result = await fetchCaseNarrative(caseId);
    if (result._status === "ok") {
      setNarrative(result.narrative || "");
      setMeta({
        model_used: result.model_used,
        generated_at: result.generated_at,
      });
      setLoadPhase("ready");
      return;
    }
    if (result._status === "none") {
      setNarrative(null);
      setMeta(null);
      setLoadPhase("ready");
      return;
    }
    setNarrative(null);
    setMeta(null);
    setLoadPhase("error");
    setLoadErr(
      (result._message && String(result._message).slice(0, 400)) ||
        "Could not load summary."
    );
  }, [caseId]);

  useEffect(() => {
    load();
  }, [load]);

  const hasNarrative = Boolean(narrative && String(narrative).trim());
  useEffect(() => {
    if (hasNarrative && !didAutoExpand.current) {
      setExpanded(true);
      didAutoExpand.current = true;
    }
  }, [hasNarrative]);

  const handleGenerate = async () => {
    setActionErr("");
    if (!import.meta.env.VITE_OPEN_CASE_API_KEY) {
      setActionErr(
        "Set VITE_OPEN_CASE_API_KEY in client/.env.local to generate a summary."
      );
      return;
    }
    setGenerating(true);
    try {
      const data = await synthesizeCaseNarrative(caseId);
      setNarrative(data.narrative || "");
      setMeta({
        model_used: data.model_used,
        generated_at: data.generated_at,
      });
    } catch (e) {
      setActionErr(
        e?.message
          ? String(e.message)
          : "Could not generate summary. Check the API and server model keys."
      );
    } finally {
      setGenerating(false);
    }
  };

  const summaryHint = hasNarrative
    ? "AI summary on file"
    : loadPhase === "error"
      ? "Load failed"
      : "Not generated yet";

  return (
    <section
      className="oc-investigation-summary"
      aria-label="Investigation summary"
    >
      <div className="oc-accordion">
        <button
          type="button"
          className="oc-accordion-header oc-investigation-summary-header"
          aria-expanded={expanded}
          onClick={() => setExpanded((o) => !o)}
        >
          <span className="oc-accordion-dot" aria-hidden />
          <span>INVESTIGATION SUMMARY</span>
          <span className="oc-accordion-count oc-mono">{summaryHint}</span>
        </button>
        {expanded ? (
          <div className="oc-accordion-body oc-investigation-summary-body">
            {loadPhase === "loading" && !generating ? (
              <p className="oc-investigation-summary-line">
                Loading summary status…
              </p>
            ) : null}

            {loadPhase === "error" && loadErr ? (
              <div>
                <p className="oc-investigation-summary-err">{loadErr}</p>
                <button
                  type="button"
                  className="oc-investigation-summary-retry"
                  onClick={load}
                >
                  Retry
                </button>
              </div>
            ) : null}

            {generating ? (
              <div>
                <div className="oc-loading-dots" aria-hidden>
                  <span />
                  <span />
                  <span />
                </div>
                <p className="oc-investigation-summary-line">
                  Generating investigative summary…
                </p>
                <p className="oc-investigation-summary-note">
                  This can take 30–90 seconds when models are cold.
                </p>
              </div>
            ) : null}

            {loadPhase === "ready" && !generating && !hasNarrative ? (
              <div>
                <p className="oc-investigation-summary-line">
                  No summary yet. Generate one from this case’s evidence, signals, and
                  pattern rules (same auth as the API).
                </p>
                <button
                  type="button"
                  className="oc-btn-generate-summary"
                  onClick={handleGenerate}
                  disabled={generating}
                >
                  Generate AI Summary
                </button>
              </div>
            ) : null}

            {loadPhase === "ready" && hasNarrative && !generating ? (
              <div>
                {meta?.model_used || meta?.generated_at ? (
                  <p className="oc-investigation-summary-meta oc-mono">
                    {meta.model_used && <span>Model: {meta.model_used}</span>}
                    {meta.model_used && meta.generated_at ? " · " : null}
                    {meta.generated_at && (
                      <span>
                        {new Date(meta.generated_at).toLocaleString()}
                      </span>
                    )}
                  </p>
                ) : null}
                <MarkdownBlock className="oc-investigation-markdown">
                  {narrative}
                </MarkdownBlock>
                <p className="oc-investigation-disclaimer">
                  AI-generated summary from public records. Not a finding of
                  wrongdoing.
                </p>
              </div>
            ) : null}

            {actionErr ? (
              <p className="oc-investigation-summary-err">{actionErr}</p>
            ) : null}
          </div>
        ) : null}
      </div>
    </section>
  );
}
