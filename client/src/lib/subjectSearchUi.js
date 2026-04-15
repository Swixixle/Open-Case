/** Client-side helpers for ranked subject search (pairs with GET /api/v1/subjects/search). */

export function normalizeForSearch(s) {
  return String(s || "")
    .toLowerCase()
    .replace(/[^\w\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Small boost while typing so prefix matches creep up between debounced API calls. */
export function liveRankBoost(query, displayName) {
  const q = normalizeForSearch(query);
  const n = normalizeForSearch(displayName);
  if (!q || !n) return 0;
  if (n.startsWith(q)) return 0.07;
  const qt = q.split(" ");
  const nt = n.split(" ");
  if (qt.length && nt.length && nt[0].startsWith(qt[0])) return 0.035;
  return 0;
}

export function matchConfidenceFromScore(matchScore) {
  const s = Number(matchScore);
  if (Number.isNaN(s)) return "medium";
  return s >= 0.82 ? "high" : "medium";
}

/**
 * When the query is not a substring of the top hit but fuzzy score is strong,
 * suggest a correction (e.g. "Coton" → Tom Cotton).
 */
export function didYouMeanFromTopHit(query, topHit) {
  if (!topHit?.name || !query || query.trim().length < 3) return null;
  const q = normalizeForSearch(query);
  const n = normalizeForSearch(topHit.name);
  if (!q || !n) return null;
  if (n.includes(q)) return null;
  const ms = Number(topHit.match_score);
  if (Number.isNaN(ms) || ms < 0.68) return null;
  return topHit.name;
}

export function mergeLegacySearchResults(data) {
  if (!data) return [];
  const out = [];
  for (const row of data.database_matches || []) {
    out.push({
      key: `db-${row.case_id}`,
      case_id: row.case_id,
      bioguide_id: row.bioguide_id || "",
      name: row.subject_name,
      subject_type: row.subject_type,
      match_score: row.match_score ?? 0,
      source: "database",
    });
  }
  for (const row of data.candidates || []) {
    const bg = row.bioguide_id || row.bioguideId;
    if (!bg) continue;
    out.push({
      key: `cand-${bg}`,
      case_id: "",
      bioguide_id: bg,
      name: row.name,
      subject_type:
        row.subject_type || (row.office === "house" ? "house_member" : "senator"),
      match_score: row.match_score ?? 0,
      source: row.source || "candidate",
    });
  }
  return out;
}
