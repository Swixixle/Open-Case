export const CATEGORY_LABELS = {
  ethics_and_investigations: "Ethics & Investigations",
  financial_disclosures: "Financial Disclosures",
  donor_vs_vote_record: "Donors vs Votes",
  public_statements_vs_votes: "Statements vs Votes",
  revolving_door: "Revolving Door",
  recent_news: "Recent News",
};

export function categoryLabel(key) {
  return CATEGORY_LABELS[key] || key.replace(/_/g, " ");
}
