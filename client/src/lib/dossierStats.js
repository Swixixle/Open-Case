/** Derive card stats from GET /api/v1/senators/:bioguide_id/dossier payload. */

import { concernTierFromDossier } from "./dossierParse.js";

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

export function statsFromDossier(data) {
  if (!data || typeof data !== "object") return null;

  if (data.status === "building") {
    return {
      concern_tier: "MODERATE",
      finding_count: 0,
      categories_flagged: 0,
      last_updated: "",
      dossier_id: data.dossier_id,
      is_building: true,
    };
  }

  if (data.status !== "completed") return null;

  const dr = data.deep_research || {};
  const categories = dr.categories || {};
  const finding_count = countClaims(categories);
  const categories_flagged = countCategories(categories);
  const concern_tier = concernTierFromDossier(data);
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
    is_building: false,
  };
}
