export const CATEGORY_LABELS = {
  ethics_and_investigations: "Ethics & Investigations",
  financial_disclosures: "Financial Disclosures",
  donor_vs_vote_record: "Donors vs Votes",
  public_statements_vs_votes: "Statements vs Votes",
  revolving_door: "Revolving Door",
  recent_news: "Recent News",
  /** Editorial (render-only) dossier buckets */
  editorial_financial_disclosures: "Financial Disclosures",
  editorial_campaign_finance: "Campaign Finance",
  editorial_revolving_door: "Revolving Door",
  editorial_allegations_controversies: "Allegations & Controversies",
  editorial_committee_authority: "Committee & Authority",
  editorial_other_records: "Other Records",
};

export function categoryLabel(key) {
  return CATEGORY_LABELS[key] || key.replace(/_/g, " ");
}
