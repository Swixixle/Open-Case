/**
 * Display-layer validation for entity group headers (dossier claims).
 * Does not alter stored findings — only routing to safe labels for UI grouping.
 */

export const OTHER_DETAILS_LABEL = "Other details";

const MIN_HEADER_LENGTH = 4;

/** Entire label is treated as invalid (known bad extractions). */
const BANNED_EXACT = new Set(
  [
    "New Ideas",
    "Other Details",
    "Other details",
    "Large Cap",
    "Value Fund",
    "Growth Fund",
 ].map((s) => s.toLowerCase())
);

const STOP_TOKENS = new Set([
  "the",
  "and",
  "for",
  "with",
  "from",
  "that",
  "this",
  "into",
  "over",
  "under",
  "after",
  "before",
  "while",
  "during",
  "about",
  "between",
  "among",
  "without",
  "within",
  "through",
  "against",
]);

/** Patterns that indicate a fund/instrument fragment, not a stable entity. */
const FRAGMENT_PATTERNS = [
  /\bfund\s+class\b/i,
  /\bclass\s+[a-z]\s*$/i,
  /\bvalue\s+fund\b/i,
  /\bmoney\s+market\b/i,
  /\bprice\s+large\b/i,
  /\blarge\s+cap\b/i,
  /\bindex\s+fund\b/i,
  /\betf\b/i,
  /\browe\s+price\s+large\b/i,
  /^of\s+/i,
  /\s+of\s*$/i,
];

/**
 * Light cleanup before validation (does not invent names).
 * @param {string} name
 */
export function sanitizeEntityDisplayName(name) {
  if (name == null) return "";
  let s = String(name).trim().replace(/\s+/g, " ");
  s = s.replace(/[.,;:!?]+$/g, "").trim();
  return s;
}

/**
 * @param {string} name
 * @param {{ claimText?: string }} [_context]
 */
export function displayNameValid(name, _context = {}) {
  const raw = sanitizeEntityDisplayName(name);
  if (!raw) return false;
  if (raw === OTHER_DETAILS_LABEL) return true;
  if (raw.length < MIN_HEADER_LENGTH) return false;
  const lower = raw.toLowerCase();
  if (BANNED_EXACT.has(lower)) return false;
  for (const re of FRAGMENT_PATTERNS) {
    if (re.test(raw)) return false;
  }
  if (/^of\s+/i.test(raw)) return false;

  const tokens = raw.split(/\s+/).filter(Boolean);
  if (!tokens.length) return false;
  const alphaTokens = tokens.filter((t) => /[a-zA-Z]/.test(t));
  if (!alphaTokens.some((t) => t.length >= 2)) return false;
  if (
    alphaTokens.length > 0 &&
    alphaTokens.every((t) => STOP_TOKENS.has(t.toLowerCase()))
  ) {
    return false;
  }

  const letters = raw.replace(/[^a-zA-Z]/g, "");
  if (letters.length < 3) return false;

  return true;
}

/**
 * If `name` failed validation, see if the claim text mentions another valid * label from the same dossier batch (no invented parents).
 * @param {string} invalidName
 * @param {string} claimText
 * @param {string[]} validLabels
 */
export function foldToMentionedValidLabel(invalidName, claimText, validLabels) {
  const text = String(claimText || "");
  if (!text.trim() || !validLabels.length) return null;
  const inv = sanitizeEntityDisplayName(invalidName).toLowerCase();
  let best = null;
  let bestLen = 0;
  for (const v of validLabels) {
    if (!displayNameValid(v)) continue;
    const vs = sanitizeEntityDisplayName(v);
    if (!vs) continue;
    if (vs.toLowerCase() === inv) continue;
    const esc = vs.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const re = new RegExp(`\\b${esc}\\b`, "i");
    if (re.test(text) && vs.length > bestLen) {
      best = vs;
      bestLen = vs.length;
    }
  }
  return best;
}
