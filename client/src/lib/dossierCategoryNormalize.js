/**
 * Render-time editorial bucketing for dossier deep_research.categories.
 * Does not modify API payloads — builds a parallel structure for display only.
 */

import { categoryLabel } from "./constants.js";

export const EDITORIAL_FINANCIAL = "editorial_financial_disclosures";
export const EDITORIAL_CAMPAIGN = "editorial_campaign_finance";
export const EDITORIAL_REVOLVING = "editorial_revolving_door";
export const EDITORIAL_ALLEGATIONS = "editorial_allegations_controversies";
export const EDITORIAL_COMMITTEE = "editorial_committee_authority";
export const EDITORIAL_OTHER = "editorial_other_records";

export const EDITORIAL_CATEGORY_ORDER = [
  EDITORIAL_FINANCIAL,
  EDITORIAL_CAMPAIGN,
  EDITORIAL_REVOLVING,
  EDITORIAL_ALLEGATIONS,
  EDITORIAL_COMMITTEE,
  EDITORIAL_OTHER,
];

const RX = {
  campaign:
    /\b(fec|pac|pacs|fundraising|contribution|contributions|donor|donors|campaign committee|cash on hand|schedule\s*[ab]|itemized|election campaign|fec\.gov|raised \$|total receipts|disbursement|campaign finance)\b/i,
  personalFinance:
    /\b(financial disclosure|periodic transaction|stock\s+(sale|purchase)|asset|assets|liabilit|blind trust|mutual fund holding|bond holding|annuity|deposit account|personal finance disclosure)\b/i,
  revolving:
    /\b(lobby|lobbying|lobbyist|registered lobbyist|revolving door|left (the )?(senate|house|congress|office) to|post-?government employment|private sector (role|employment)|became (a )?(consultant|lobbyist)|k street)\b/i,
  committee:
    /\b(committee|subcommittee|chair(man|woman|person)?|ranking member|jurisdiction|oversight (role|authority)|hearing gavel)\b/i,
  allegations:
    /\b(allegation|allegations|complaint|investigation|propublica|inspector general|ig report|disputed|misconduct|ethics complaint|whistleblower|controversy|telework|discrepancy|unsubstantiated)\b/i,
};

function textOfClaim(claim) {
  return String(claim?.claim || claim?.text || "").trim();
}

/**
 * @param {string} sourceKey deep_research category key from API
 * @param {string} text
 */
export function editorialBucketForClaim(sourceKey, text) {
  const t = text.toLowerCase();
  const hasCampaign = RX.campaign.test(text);
  const hasPersonal = RX.personalFinance.test(text);
  const hasRevolving = RX.revolving.test(text);
  const hasCommittee = RX.committee.test(text);
  const hasAlleg = RX.allegations.test(text);

  if (sourceKey === "donor_vs_vote_record") {
    if (hasRevolving && !hasCampaign) return EDITORIAL_REVOLVING;
    return EDITORIAL_CAMPAIGN;
  }

  if (sourceKey === "ethics_and_investigations") {
    if (hasCommittee && !hasAlleg && !t.includes("ethics complaint"))
      return EDITORIAL_COMMITTEE;
    return EDITORIAL_ALLEGATIONS;
  }

  if (sourceKey === "revolving_door") {
    if (hasRevolving || /\bstaff\b/i.test(text)) return EDITORIAL_REVOLVING;
    if (hasCampaign && !hasRevolving) return EDITORIAL_CAMPAIGN;
    return EDITORIAL_REVOLVING;
  }

  if (sourceKey === "financial_disclosures") {
    if (hasAlleg && !hasPersonal && !hasCampaign) return EDITORIAL_ALLEGATIONS;
    if (hasCommittee && !hasPersonal && !hasCampaign) return EDITORIAL_COMMITTEE;
    if (hasCampaign && !hasPersonal) return EDITORIAL_CAMPAIGN;
    return EDITORIAL_FINANCIAL;
  }

  if (sourceKey === "public_statements_vs_votes") {
    if (hasCommittee) return EDITORIAL_COMMITTEE;
    if (hasAlleg) return EDITORIAL_ALLEGATIONS;
    return EDITORIAL_OTHER;
  }

  if (sourceKey === "recent_news") {
    if (hasAlleg) return EDITORIAL_ALLEGATIONS;
    if (hasCommittee) return EDITORIAL_COMMITTEE;
    if (hasCampaign) return EDITORIAL_CAMPAIGN;
    return EDITORIAL_OTHER;
  }

  if (hasAlleg) return EDITORIAL_ALLEGATIONS;
  if (hasRevolving) return EDITORIAL_REVOLVING;
  if (hasCommittee) return EDITORIAL_COMMITTEE;
  if (hasCampaign) return EDITORIAL_CAMPAIGN;
  if (hasPersonal) return EDITORIAL_FINANCIAL;
  return EDITORIAL_OTHER;
}

function defaultEditorialForSourceNarrative(sourceKey) {
  switch (sourceKey) {
    case "donor_vs_vote_record":
      return EDITORIAL_CAMPAIGN;
    case "financial_disclosures":
      return EDITORIAL_FINANCIAL;
    case "revolving_door":
      return EDITORIAL_REVOLVING;
    case "ethics_and_investigations":
      return EDITORIAL_ALLEGATIONS;
    case "public_statements_vs_votes":
      return EDITORIAL_OTHER;
    case "recent_news":
      return EDITORIAL_OTHER;
    default:
      return EDITORIAL_OTHER;
  }
}

/**
 * @param {Record<string, { claims?: unknown[], narrative?: string }>} rawCategories
 */
export function normalizeDossierCategories(rawCategories) {
  const out = {};
  for (const k of EDITORIAL_CATEGORY_ORDER) {
    out[k] = { claims: [], narrative: "" };
  }

  if (!rawCategories || typeof rawCategories !== "object") return out;

  for (const [sourceKey, block] of Object.entries(rawCategories)) {
    const narrative = String(block?.narrative || "").trim();
    const claims = block?.claims;
    const narrativeTarget = defaultEditorialForSourceNarrative(sourceKey);
    if (narrative) {
      const cur = out[narrativeTarget].narrative || "";
      out[narrativeTarget].narrative = cur
        ? `${cur}\n\n${narrative}`
        : narrative;
    }

    if (!Array.isArray(claims)) continue;
    for (const claim of claims) {
      if (!claim || typeof claim !== "object") continue;
      const txt = textOfClaim(claim);
      if (!txt) continue;
      const bucket = editorialBucketForClaim(sourceKey, txt);
      if (!out[bucket]) out[bucket] = { claims: [], narrative: "" };
      out[bucket].claims.push({
        ...claim,
        _sourceCategoryKey: sourceKey,
      });
    }
  }

  for (const block of Object.values(out)) {
    if (Array.isArray(block.claims)) {
      block.claims = [...block.claims];
    }
    if (!block.narrative?.trim()) delete block.narrative;
    if (!block.claims?.length) delete block.claims;
  }

  return out;
}

/** First non-empty narrative in editorial order (for hero). */
export function firstEditorialNarrative(normalized) {
  if (!normalized || typeof normalized !== "object") return "";
  for (const k of EDITORIAL_CATEGORY_ORDER) {
    const n = String(normalized[k]?.narrative || "").trim();
    if (n) return n;
  }
  return "";
}

/** Collect claims that originated from a given API category key (after normalization). */
export function collectClaimsBySourceKey(normalized, sourceKey) {
  const out = [];
  if (!normalized || typeof normalized !== "object") return out;
  for (const block of Object.values(normalized)) {
    const claims = block?.claims;
    if (!Array.isArray(claims)) continue;
    for (const c of claims) {
      if (c?._sourceCategoryKey === sourceKey) out.push(c);
    }
  }
  return out;
}

export function editorialCategoryLabel(key) {
  return categoryLabel(key);
}
