import { parseSourceDomain } from "../lib/dossierParse.js";
import {
  patternAlertDescription,
  patternAlertDisplay,
} from "../lib/patternAlertLabels.js";

function extractFirstUrl(text) {
  if (!text || typeof text !== "string") return null;
  const m = text.match(/https?:\/\/[^\s)]+/i);
  return m ? m[0].replace(/[.,;]+$/, "") : null;
}

function alertScore(a) {
  const s = a?.score;
  if (s != null && !Number.isNaN(Number(s))) return Number(s);
  const v = a?.proximity_to_vote_score;
  if (v != null && !Number.isNaN(Number(v))) return Number(v);
  const sus = a?.suspicion_score;
  if (sus != null && !Number.isNaN(Number(sus))) return Number(sus);
  return null;
}

function topSourceFromAlert(a) {
  const fromDisclaimer = extractFirstUrl(a?.disclaimer);
  if (fromDisclaimer) return fromDisclaimer;
  const fromRule = extractFirstUrl(a?.rule_line);
  if (fromRule) return fromRule;
  const refs = a?.evidence_refs;
  if (Array.isArray(refs) && refs.length) {
    return typeof refs[0] === "string" && refs[0].startsWith("http")
      ? refs[0]
      : null;
  }
  return null;
}

function EpistemicMini({ level }) {
  const l = (level || "REPORTED").toUpperCase();
  const colors = {
    VERIFIED: "#16a34a",
    REPORTED: "#2563eb",
    ALLEGED: "#ca8a04",
    DISPUTED: "#ea580c",
    CONTEXTUAL: "#6b7280",
  };
  const c = colors[l] || colors.REPORTED;
  return (
    <span
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: "0.65rem",
        letterSpacing: "0.06em",
        color: c,
        border: `1px solid ${c}`,
        padding: "0.15rem 0.4rem",
        borderRadius: 4,
      }}
    >
      {l}
    </span>
  );
}

export default function PatternAlertCard({ alert, isNew }) {
  const a = alert || {};
  const ruleId = a.rule_id || "";
  const display = patternAlertDisplay(ruleId);
  const desc = patternAlertDescription(ruleId) || a.disclaimer || a.rule_line || "";
  const score = alertScore(a);
  const scorePct =
    score != null ? Math.min(100, Math.max(0, score * 100)) : null;
  const href = topSourceFromAlert(a);
  const domain = href ? parseSourceDomain(href) : "";

  return (
    <div
      className="oc-alert-card oc-pattern-alert-card"
      style={{
        position: "relative",
        border: isNew ? "1px solid var(--amber-gold)" : undefined,
        boxShadow: isNew ? "0 0 0 1px var(--amber-dim)" : undefined,
      }}
    >
      {isNew ? (
        <span          className="oc-mono"
          style={{
            position: "absolute",
            top: 8,
            right: 10,
            fontSize: "0.65rem",
            color: "var(--amber-bright)",
          }}
        >
          New finding
        </span>
      ) : null}
      <div className="oc-alert-head" style={{ alignItems: "flex-start" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>
            {display}
          </span>
          {desc ? (
            <p
              style={{
                margin: "0.35rem 0 0",
                fontSize: "0.82rem",
                color: "var(--text-muted)",
                lineHeight: 1.45,
              }}
            >
              {desc}
            </p>
          ) : null}
        </div>
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-end",
            gap: 6,
            flexShrink: 0,
          }}
        >
          <EpistemicMini level={a.epistemic_level} />
          {score != null ? (
            <span className="oc-mono" style={{ color: "var(--amber-gold)" }}>
              {score.toFixed(3)}
            </span>
          ) : (
            <span className="oc-mono" style={{ color: "var(--text-dim)" }}>
              —
            </span>
          )}
        </div>
      </div>
      {scorePct != null ? (
        <div
          style={{
            height: 6,
            borderRadius: 3,
            background: "var(--border)",
            marginTop: "0.65rem",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              height: "100%",
              width: `${scorePct}%`,
              background: "linear-gradient(90deg, var(--amber-dim), var(--amber-gold))",
            }}
          />
        </div>
      ) : null}
      <div className="oc-alert-meta" style={{ marginTop: "0.65rem" }}>
        {href ? (
          <>
            Top source:{" "}
            <a href={href} target="_blank" rel="noopener noreferrer">
              {domain || "link"} →
            </a>
          </>
        ) : (
          <>Top source: —</>
        )}
      </div>
      {ruleId ? (
        <p
          className="oc-mono"
          style={{
            margin: "0.5rem 0 0",
            fontSize: "0.65rem",
            color: "var(--text-dim)",
          }}
        >
          {ruleId}
        </p>
      ) : null}
    </div>
  );
}
