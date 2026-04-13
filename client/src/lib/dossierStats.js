/** Derive card stats from GET /api/v1/senators/:bioguide_id/dossier payload. */

const TIER_ORDER = { CRITICAL: 4, HIGH: 3, MODERATE: 2, LOW: 1 };

function countClaims(categories) {
  if (!categories || typeof categories !== "object") return 0;
  let n = 0;
  for (const cat of Object.values(categories)) {
    const claims = cat?.claims;
    if (Array.isArray(claims)) n += claims.length;
  }
  return n;
}

function countCategories(categories) {
  if (!categories || typeof categories !== "object") return 0;
  return Object.keys(categories).filter((k) => {
    const claims = categories[k]?.claims;
    return Array.isArray(claims) && claims.length > 0;
  }).length;
}

function maxPatternTier(patternAlerts) {
  if (!Array.isArray(patternAlerts) || !patternAlerts.length) return "MODERATE";
  let best = "LOW";
  for (const a of patternAlerts) {
    const sev = (a?.severity || a?.level || "").toString().toUpperCase();
    const mapped =
      sev.includes("CRIT") || sev === "CRITICAL"
        ? "CRITICAL"
        : sev.includes("HIGH")
          ? "HIGH"
          : sev.includes("MOD") || sev === "MODERATE"
            ? "MODERATE"
            : "LOW";
    if ((TIER_ORDER[mapped] || 0) > (TIER_ORDER[best] || 0)) best = mapped;
  }
  return best;
}

export function statsFromDossier(data) {
  if (!data || data.status !== "completed") return null;
  const dr = data.deep_research || {};
  const categories = dr.categories || {};
  const finding_count = countClaims(categories);
  const categories_flagged = countCategories(categories);
  const concern_tier = maxPatternTier(data.pattern_alerts);
  const rawDate = data.completed_at || data.generated_at || "";
  let last_updated = "";
  try {
    if (rawDate) {
      const d = new Date(rawDate);
      if (!Number.isNaN(d.getTime())) {
        last_updated = d.toISOString().slice(0, 10);
      }
    }
  } catch {
    /* ignore */
  }
  return {
    concern_tier,
    finding_count,
    categories_flagged,
    last_updated,
    dossier_id: data.dossier_id,
  };
}
