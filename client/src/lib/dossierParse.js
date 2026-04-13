import { categoryLabel } from "./constants.js";

const TIER_ORDER = { CRITICAL: 4, HIGH: 3, MODERATE: 2, LOW: 1 };

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
    const score = Number(a?.proximity_to_vote_score);
    let tier = "LOW";
    if (!Number.isNaN(score) && score >= 0.75) tier = "CRITICAL";
    else if (!Number.isNaN(score) && score >= 0.5) tier = "HIGH";
    else if (!Number.isNaN(score) && score >= 0.25) tier = "MODERATE";
    else tier = "MODERATE";
    if ((TIER_ORDER[tier] || 0) > (TIER_ORDER[best] || 0)) best = tier;
  }
  if (alerts.length >= 3 && best === "LOW") return "HIGH";
  return best;
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
