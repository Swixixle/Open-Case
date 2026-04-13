import { categoryLabel } from "../lib/constants.js";

export default function CategoryChips({ categories, onNavigate }) {
  const entries = Object.entries(categories || {}).filter(
    ([, v]) => Array.isArray(v?.claims) && v.claims.length > 0
  );
  if (!entries.length) return null;

  return (
    <div className="oc-chips">
      {entries.map(([key, v]) => (
        <button
          key={key}
          type="button"
          className="oc-chip"
          onClick={() => onNavigate?.(`cat-${key}`)}
        >
          {categoryLabel(key).toUpperCase()} ({v.claims.length})
        </button>
      ))}
    </div>
  );
}
