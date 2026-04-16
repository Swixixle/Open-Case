import { categoryLabel } from "./constants.js";

const TIER_ORDER = { CRITICAL: 4, SIGNIFICANT: 3, HIGH: 3, MODERATE: 2, LOW: 1 };

export function countTotalFindings(categories) {
  if (!categories || typeof categories !== "object") return 0;
  let n = 0;
  for (const block of Object.values(categories)) {
    const claims = block?.claims;
    if (Array.isArray(claims)) n += claims.length;
  }
  return n;
}

export function categoriesWithClaims(categories) {
  if (!categories || typeof categories !== "object") return [];
  return Object.entries(categories).filter(
    ([, v]) => Array.isArray(v?.claims) && v.claims.length > 0
  );
}

export function concernTierFromDossier(data) {
  const alerts = data?.pattern_alerts;
  if (!Array.isArray(alerts) || !alerts.length) return "MODERATE";
  let best = "LOW";
  for (const a of alerts) {
    const score = Number(
      a?.score ?? a?.proximity_to_vote_score ?? a?.suspicion_score
    );
    let tier = "LOW";
    if (!Number.isNaN(score) && score >= 0.75) tier = "CRITICAL";
    else if (!Number.isNaN(score) && score >= 0.5) tier = "SIGNIFICANT";
    else if (!Number.isNaN(score) && score >= 0.25) tier = "MODERATE";
    else tier = "MODERATE";
    if ((TIER_ORDER[tier] || 0) > (TIER_ORDER[best] || 0)) best = tier;
  }
  if (alerts.length >= 3 && best === "LOW") return "SIGNIFICANT";
  return best;
}

/** Derive concern tier from GET /cases/:id/report payload. */
export function concernTierFromReport(report) {
  const signals = report?.signals;
  if (Array.isArray(signals) && signals.length) {
    let max = 0;
    for (const s of signals) {
      const w = Number(s?.relevance_score ?? s?.weight ?? 0);
      if (!Number.isNaN(w)) max = Math.max(max, w);
    }
    if (max >= 0.75) return "CRITICAL";
    if (max >= 0.5) return "SIGNIFICANT";
    if (max >= 0.25) return "MODERATE";
  }
  const alerts = report?.pattern_alerts;
  if (Array.isArray(alerts) && alerts.length) {
    let best = "LOW";
    for (const a of alerts) {
      const score = Number(a?.score ?? a?.proximity_to_vote_score);
      let tier = "LOW";
      if (!Number.isNaN(score) && score >= 0.75) tier = "CRITICAL";
      else if (!Number.isNaN(score) && score >= 0.5) tier = "SIGNIFICANT";
      else if (!Number.isNaN(score) && score >= 0.25) tier = "MODERATE";
      else tier = "MODERATE";
      if ((TIER_ORDER[tier] || 0) > (TIER_ORDER[best] || 0)) best = tier;
    }
    if (alerts.length >= 3 && best === "LOW") return "SIGNIFICANT";
    if (best !== "LOW") return best;
  }
  if (Array.isArray(alerts) && alerts.length >= 4) return "SIGNIFICANT";
  if (Array.isArray(alerts) && alerts.length >= 1) return "MODERATE";
  return "MODERATE";
}

export function topPatternAlertScore(data) {
  const alerts = data?.pattern_alerts;
  if (!Array.isArray(alerts) || !alerts.length) return null;
  let best = null;
  for (const a of alerts) {
    const s = Number(a?.score ?? a?.proximity_to_vote_score ?? a?.suspicion_score);
    if (!Number.isNaN(s)) best = best == null ? s : Math.max(best, s);
  }
  return best;
}

/** Count epistemic levels on dossier pattern alerts + optional claim hints. */
export function epistemicDistributionFromDossier(data) {
  const dist = { VERIFIED: 0, REPORTED: 0, ALLEGED: 0, DISPUTED: 0, CONTEXTUAL: 0 };
  const alerts = data?.pattern_alerts;
  if (Array.isArray(alerts)) {
    for (const a of alerts) {
      const lev = (a?.epistemic_level || "REPORTED").toUpperCase();
      if (lev in dist) dist[lev] += 1;
      else dist.REPORTED += 1;
    }
  }
  const total = Object.values(dist).reduce((s, n) => s + n, 0);
  if (total > 0) return dist;
  return null;
}

export function firstNarrativeParagraph(categories) {
  if (!categories || typeof categories !== "object") return "";
  const order = Object.keys(categories);
  for (const k of order) {
    const n = (categories[k]?.narrative || "").trim();
    if (n) return n;
  }
  return "";
}

/** Flatten claims with category key and sort by date desc */
export function timelineClaims(categories) {
  if (!categories || typeof categories !== "object") return [];
  const out = [];
  for (const [catKey, block] of Object.entries(categories)) {
    const claims = block?.claims;
    if (!Array.isArray(claims)) continue;
    for (const c of claims) {
      if (!c || typeof c !== "object") continue;
      const text = (c.claim || c.text || "").trim();
      if (!text) continue;
      out.push({
        ...c,
        _categoryKey: catKey,
        _categoryLabel: categoryLabel(catKey),
      });
    }
  }
  out.sort((a, b) => {
    const da = parseClaimYear(a.date) || 0;
    const db = parseClaimYear(b.date) || 0;
    return db - da;
  });
  return out;
}

function parseClaimYear(dateStr) {
  if (!dateStr) return null;
  const s = String(dateStr);
  const m = s.match(/(\d{4})/);
  return m ? parseInt(m[1], 10) : null;
}

export function parseSourceDomain(source) {
  if (!source) return "";
  const t = String(source).trim();
  try {
    if (t.includes("://")) {
      const u = new URL(t);
      return u.hostname.replace(/^www\./, "");
    }
  } catch {
    /* ignore */
  }
  return t.slice(0, 48);
}

export function formatDisplayDate(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(String(iso).includes("T") ? iso : `${iso}T12:00:00Z`);
    if (Number.isNaN(d.getTime())) return String(iso).slice(0, 16);
    return d.toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  } catch {
    return String(iso);
  }
}

/** Government level for senator dossier shell (API subject, dossier root, or directory row). */
export function dossierGovernmentLevel(dossier, dirMeta) {
  const sub = dossier?.subject;
  const fromSub =
    sub && typeof sub === "object" ? String(sub.government_level || "").trim() : "";
  return (
    fromSub ||
    String(dossier?.government_level || "").trim() ||
    String(dirMeta?.government_level || "").trim() ||
    ""
  );
}

/** Minimal “report” shape for DataStatusBanner when rendering from dossier JSON. */
export function dataStatusBannerReportFromDossier(dossier, dirMeta, displayName) {
  const sub = dossier?.subject;
  let jurisdiction = "";
  if (sub && typeof sub === "object") {
    jurisdiction = String(sub.jurisdiction || "").trim();
  }
  if (!jurisdiction) jurisdiction = String(dossier?.jurisdiction || "").trim();
  if (!jurisdiction) jurisdiction = String(dirMeta?.jurisdiction || "").trim();
  if (!jurisdiction && String(dirMeta?.government_level || "").toLowerCase() === "local") {
    jurisdiction = "Indianapolis, IN";
  }
  return {
    jurisdiction,
    subject: displayName,
    title: displayName,
  };
}
