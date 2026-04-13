import { useState } from "react";

function stripJsonFences(text) {
  let t = (text || "").trim();
  t = t.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/i, "");
  return t.trim();
}

export default function StoryAngles({ dossier }) {
  const [loading, setLoading] = useState(false);
  const [angles, setAngles] = useState([]);
  const [err, setErr] = useState("");

  const run = async () => {
    const apiKey = import.meta.env.VITE_ANTHROPIC_API_KEY;
    if (!apiKey) {
      setErr("Add VITE_ANTHROPIC_API_KEY to client .env to generate angles.");
      return;
    }
    setErr("");
    setLoading(true);
    setAngles([]);
    try {
      const sub = dossier?.subject || {};
      const name = sub.name || "Senator";
      const state = sub.state || "";
      const cats = dossier?.deep_research?.categories || {};
      const gaps = dossier?.gap_analysis || [];
      const alerts = dossier?.pattern_alerts || [];
      const darkMoney = dossier?.dark_money || [];
      const ethicsTravel = dossier?.ethics_travel || [];
      const committeeWitnesses = dossier?.committee_witnesses || [];

      const prompt = `You are an investigative journalism assistant.

Given this senator dossier data, generate 3-5 specific newsworthy story angles.

Senator: ${name} (${state})

Deep research findings:
${JSON.stringify(cats, null, 2).slice(0, 3000)}

Gap analysis:
${JSON.stringify(gaps, null, 2).slice(0, 1000)}

Pattern alerts:
${JSON.stringify(alerts, null, 2).slice(0, 1000)}

Dark money connections:
${JSON.stringify(darkMoney, null, 2).slice(0, 500)}

Ethics and travel:
${JSON.stringify(ethicsTravel, null, 2).slice(0, 500)}

Committee witness overlaps:
${JSON.stringify(committeeWitnesses, null, 2).slice(0, 500)}

Return ONLY a JSON array, no prose, no markdown:
[{
  "headline": "Short punchy headline",
  "angle": "2-3 sentence story description with specific facts from the dossier",
  "why_now": "One sentence on timeliness",
  "source_types": ["FEC", "LDA", "Ethics filing"]
}]

Rules:
- Use only facts present in the dossier data
- No causal language — say "coincides with" not "because of"
- No accusations — document patterns only
- If data is sparse, say so and suggest what reporting would reveal
- Always note findings require independent verification`;

      const res = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-api-key": apiKey,
          "anthropic-version": "2023-06-01",
        },
        body: JSON.stringify({
          model: "claude-sonnet-4-20250514",
          max_tokens: 1200,
          messages: [{ role: "user", content: prompt }],
        }),
      });

      if (!res.ok) {
        const t = await res.text();
        throw new Error(t.slice(0, 200) || res.statusText);
      }

      const data = await res.json();
      const text = data?.content?.[0]?.text || "";
      const cleaned = stripJsonFences(text);
      const parsed = JSON.parse(cleaned);
      if (!Array.isArray(parsed)) throw new Error("Model did not return an array");
      setAngles(parsed);
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="oc-section">
      <h2 className="oc-section-title">STORY ANGLES — AI-ASSISTED LEADS</h2>
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
