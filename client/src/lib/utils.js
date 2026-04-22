/**
 * Official Congress portrait (Bioguide) — current members.
 * @see https://www.congress.gov/help/field-values/member-bioguide-ids
 */
export function getCongressHeadshotUrl(bioguideId) {
  if (!bioguideId || String(bioguideId).length < 2) return null;
  const id = String(bioguideId).trim();
  const firstLetter = id[0].toUpperCase();
  return `https://bioguide.congress.gov/bioguide/photo/${firstLetter}/${id}.jpg`;
}

/** Two-letter initials for fallback avatars. */
export function getDisplayInitials(name) {
  const parts = (name || "").split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    return (
      (parts[0][0] || "") + (parts[parts.length - 1][0] || "")
    ).toUpperCase();
  }
  return (name || "?").slice(0, 2).toUpperCase();
}
