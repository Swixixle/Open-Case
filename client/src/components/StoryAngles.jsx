import { useState } from "react";
import { fetchStoryAngles } from "../lib/api.js";

export default function StoryAngles({ dossier }) {
  const [loading, setLoading] = useState(false);
  const [angles, setAngles] = useState([]);
  const [tier, setTier] = useState("");
  const [err, setErr] = useState("");

  const run = async () => {
    const apiKey = import.meta.env.VITE_OPEN_CASE_API_KEY;
    if (!apiKey) {
      setErr(
        "Set VITE_OPEN_CASE_API_KEY for the API, and GEMINI_API_KEY / ANTHROPIC_API_KEY on the server (.env) for routed story angles."
      );
      return;
    }
    setErr("");
    setLoading(true);
    setAngles([]);
    setTier("");
    try {
      const data = await fetchStoryAngles(dossier || {});
      setAngles(Array.isArray(data?.angles) ? data.angles : []);
      if (data?.tier) setTier(String(data.tier));
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="oc-section">
      <h2 className="oc-section-title">STORY ANGLES — AI-ASSISTED LEADS</h2>
      <p className="oc-empty-note">
        Angles use the server&apos;s tiered model router (Gemini for lighter dossiers,
        Claude for dense vote–money patterns); investigation outputs are unchanged.
      </p>
      <button
        type="button"
        className="oc-story-btn"
        onClick={run}
        disabled={loading}
      >
        {loading ? "…" : "[GENERATE STORY ANGLES →]"}
      </button>
      {loading ? (
        <div>
          <div className="oc-loading-dots" aria-hidden>
            <span />
            <span />
            <span />
          </div>
          <p className="oc-empty-note">
            Analyzing public records for story leads...
          </p>
        </div>
      ) : null}
      {tier ? (
        <p className="oc-empty-note">
          Routing tier: <strong>{tier}</strong>
        </p>
      ) : null}
      {err ? <p className="oc-story-err">{err}</p> : null}
      {angles.map((a, i) => (
        <article key={i} className="oc-story-card">
          <p className="oc-story-card-label">STORY ANGLE {i + 1}</p>
          <h3 className="oc-story-headline">{a.headline}</h3>
          <p className="oc-story-copy">{a.angle}</p>
          <p className="oc-story-whynow">
            <strong>WHY NOW:</strong> {a.why_now}
          </p>
          <div className="oc-story-tags">
            {(a.source_types || []).map((t) => (
              <span key={t} className="oc-story-tag">
                {t}
              </span>
            ))}
          </div>
          <button
            type="button"
            className="oc-story-copybtn"
            onClick={() => {
              const blob = `${a.headline}\n\n${a.angle}\n\nWHY NOW: ${a.why_now}`;
              navigator.clipboard?.writeText(blob);
            }}
          >
            Copy {"\u2197"}
          </button>
        </article>
      ))}
    </section>
  );
}
