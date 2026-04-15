/**
 * Group dossier category claims by a primary named entity inferred from claim text.
 * Heuristic only (display + ingest hints); not a substitute for NER.
 */

const BANNED_FIRST = new Set([
  "The",
  "This",
  "According",
  "Senator",
  "Representative",
  "Congress",
  "Senate",
  "House",
  "United",
  "American",
  "National",
  "Federal",
  "State",
  "Department",
  "Committee",
  "Staff",
  "Former",
  "Chief",
  "During",
  "After",
  "Before",
  "While",
  "When",
  "Following",
  "Public",
  "Records",
  "Report",
  "Reports",
  "OpenSecrets",
  "FEC",
]);

/** @param {string} claimText */
export function extractPrimaryEntity(claimText) {
  const t = (claimText || "").trim();
  if (!t) return "Other details";

  const qm = t.match(/^"([^"]{3,120})"/);
  if (qm) return qm[1].trim().slice(0, 120);

  const re = /\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b/g;
  let best = null;
  let bestScore = -1;
  let m;
  while ((m = re.exec(t)) !== null) {
    const phrase = m[1];
    const parts = phrase.split(/\s+/);
    if (BANNED_FIRST.has(parts[0])) continue;
    if (parts.some((p) => p.length <= 1)) continue;
    const score = 500 - m.index + phrase.length * 3;
    if (score > bestScore) {
      bestScore = score;
      best = phrase;
    }
  }

  return best || "Other details";
}

/** @param {Record<string, unknown>} claim */
export function collectClaimSources(claim) {
  const out = [];
  const s = claim?.source;
  if (s) out.push(String(s).trim());
  const arr = claim?.sources;
  if (Array.isArray(arr)) {
    for (const u of arr) {
      if (u) out.push(String(u).trim());
    }
  }
  return out.filter(Boolean);
}

/** @param {Record<string, unknown>[]} claims */
export function mergeSourcesDeduped(claims) {
  const seen = new Set();
  const urls = [];
  for (const c of claims) {
    for (const u of collectClaimSources(c)) {
      if (!seen.has(u)) {
        seen.add(u);
        urls.push(u);
      }
    }
  }
  return urls;
}

/** @param {string|undefined|null} dateStr */
function claimDateSortValue(dateStr) {
  if (!dateStr) return 0;
  const s = String(dateStr);
  const iso = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (iso) {
    const t = Date.UTC(
      parseInt(iso[1], 10),
      parseInt(iso[2], 10) - 1,
      parseInt(iso[3], 10)
    );
    return t;
  }
  const y = s.match(/(\d{4})/);
  return y ? parseInt(y[1], 10) * 10000 : 0;
}

/**
 * @param {Record<string, unknown>[]} claims
 * @returns {{ entityLabel: string, entityKey: string, claims: Record<string, unknown>[] }[]}
 */
export function groupClaimsByEntity(claims) {
  if (!Array.isArray(claims) || !claims.length) return [];

  const map = new Map();
  const order = [];

  for (const c of claims) {
    if (!c || typeof c !== "object") continue;
    const text = String(c.claim || c.text || "").trim();
    if (!text) continue;
    const label = extractPrimaryEntity(text);
    const key = label.toLowerCase();
    if (!map.has(key)) {
      map.set(key, { entityLabel: label, entityKey: key, claims: [] });
      order.push(key);
    }
    map.get(key).claims.push(c);
  }

  const groups = order.map((k) => map.get(k));

  for (const g of groups) {
    g.claims.sort(
      (a, b) =>
        claimDateSortValue(a?.date) - claimDateSortValue(b?.date) ||
        String(a?.claim || "").localeCompare(String(b?.claim || ""))
    );
  }

  groups.sort((a, b) => {
    const maxA = Math.max(0, ...a.claims.map((x) => claimDateSortValue(x?.date)));
    const maxB = Math.max(0, ...b.claims.map((x) => claimDateSortValue(x?.date)));
    if (maxB !== maxA) return maxB - maxA;
    return a.entityLabel.localeCompare(b.entityLabel);
  });

  return groups;
}
