/**
 * Resolve dossier claim source strings like "[3][18]" or "Source: [1]" to URLs using
 * optional reference tables on the dossier / category blocks, or parallel claim.sources URLs.
 */

/**
 * @param {unknown} list
 * @param {Map<number, string>} map
 */
function ingestReferencesList(list, map) {
  if (!Array.isArray(list) || !(map instanceof Map)) return;

  function add(n, url) {
    const u = String(url || "").trim();
    if (!u.startsWith("http")) return;
    const num = Number(n);
    if (!Number.isInteger(num) || num < 1) return;
    if (!map.has(num)) map.set(num, u);
  }

  list.forEach((item, i) => {
    if (typeof item === "string") {
      const t = item.trim();
      if (t.startsWith("http")) add(i + 1, t);
      return;
    }
    if (item && typeof item === "object") {
      const o = item;
      const url =
        o.url || o.uri || o.href || o.link || o.source_url || o.source;
      const rawIdx =
        o.index ?? o.ref ?? o.ref_id ?? o.id ?? o.number ?? o.citation_id;
      if (url && String(url).trim().startsWith("http")) {
        const n = Number(rawIdx);
        if (!Number.isNaN(n) && n >= 1) add(n, url);
        else add(i + 1, url);
      }
    }
  });
}

/**
 * Category-scoped map only — use for claims whose [n] indices refer to that API category block.
 * @param {unknown} dossier
 * @param {string} apiCategoryKey e.g. financial_disclosures
 * @returns {Map<number, string>}
 */
export function buildRefUrlMapForApiCategory(dossier, apiCategoryKey) {
  const map = new Map();
  if (!apiCategoryKey || !dossier?.deep_research?.categories) return map;
  const block = dossier.deep_research.categories[apiCategoryKey];
  if (!block || typeof block !== "object") return map;
  ingestReferencesList(block.references, map);
  ingestReferencesList(block.source_references, map);
  return map;
}

/**
 * Legacy/global merge: first index wins across categories — prefer buildRefUrlMapForApiCategory for claims.
 * @param {unknown} dossier
 * @returns {Map<number, string>}
 */
export function buildRefUrlMap(dossier) {
  /** @type {Map<number, string>} */
  const map = new Map();

  if (dossier && typeof dossier === "object") {
    ingestReferencesList(dossier.references, map);
    ingestReferencesList(dossier.source_references, map);
    const dr = dossier.deep_research;
    if (dr && typeof dr === "object") {
      ingestReferencesList(dr.references, map);
      const cats = dr.categories;
      if (cats && typeof cats === "object") {
        for (const block of Object.values(cats)) {
          if (block && typeof block === "object") {
            ingestReferencesList(block.references, map);
            ingestReferencesList(block.source_references, map);
          }
        }
      }
    }
  }

  return map;
}

/**
 * @param {string} str
 * @returns {number[]}
 */
export function parseBracketRefIds(str) {
  const s = String(str || "");
  const out = [];
  const re = /\[(\d+)\]/g;
  let m;
  while ((m = re.exec(s)) !== null) {
    const n = parseInt(m[1], 10);
    if (!Number.isNaN(n)) out.push(n);
  }
  return out;
}

/**
 * @param {number} n
 * @param {Map<number, string>} refMap
 * @param {unknown} sourcesFallback claim.sources
 */
export function urlForRefIndex(n, refMap, sourcesFallback) {
  if (refMap.has(n)) return refMap.get(n) || null;
  if (Array.isArray(sourcesFallback)) {
    const byZero = sourcesFallback[n - 1];
    if (byZero != null && String(byZero).trim().startsWith("http")) {
      return String(byZero).trim();
    }
  }
  return null;
}

/**
 * Ordered unique URLs for a claim: bracket IDs resolved, then raw http entries in sources.
 * @param {Record<string, unknown>} claim
 * @param {Map<number, string>} refMap
 */
export function resolvedClaimSourceUrls(claim, refMap) {
  const urls = [];
  const seen = new Set();
  const push = (u) => {
    const x = String(u || "").trim();
    if (!x.startsWith("http") || seen.has(x)) return;
    seen.add(x);
    urls.push(x);
  };

  const primary = claim?.source != null ? String(claim.source) : "";
  const ids = parseBracketRefIds(primary);
  if (ids.length) {
    for (const n of ids) {
      const u = urlForRefIndex(n, refMap, claim?.sources);
      if (u) push(u);
    }
  } else if (primary.trim().startsWith("http")) {
    push(primary.trim());
  }

  const extra = claim?.sources;
  if (Array.isArray(extra)) {
    for (const x of extra) {
      if (x != null && String(x).trim().startsWith("http")) push(String(x).trim());
    }
  }

  return urls;
}
