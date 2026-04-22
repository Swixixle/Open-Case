import { useState, useCallback } from "react";
import { getCongressHeadshotUrl, getDisplayInitials } from "../lib/utils.js";

/**
 * Circular Congress headshot or initials fallback.
 * @param {"card" | "sidebar" | "hero"} variant
 */
export default function CongressPortrait({
  bioguideId,
  name = "",
  variant = "card",
  className = "",
}) {
  const [failed, setFailed] = useState(false);
  const url = getCongressHeadshotUrl(bioguideId);
  const initials = getDisplayInitials(name);
  const onError = useCallback(() => setFailed(true), []);

  const sizeClass = {
    card: "oc-portrait--card",
    sidebar: "oc-portrait--sidebar",
    hero: "oc-portrait--hero",
  }[variant] || "oc-portrait--card";

  if (!url || failed) {
    return (
      <div
        className={`oc-portrait oc-portrait--fallback ${sizeClass} ${className}`.trim()}
        aria-hidden
        title={name || undefined}
      >
        {initials}
      </div>
    );
  }

  return (
    <div className={`oc-portrait-wrap ${sizeClass} ${className}`.trim()}>
      <img
        className={`oc-portrait oc-portrait--img ${sizeClass}`}
        src={url}
        alt={name ? `Official Congress photo: ${name}` : "Official Congress photo"}
        onError={onError}
        loading="lazy"
        decoding="async"
      />
    </div>
  );
}
