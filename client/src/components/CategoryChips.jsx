import { categoryLabel } from "../lib/constants.js";
import { EDITORIAL_CATEGORY_ORDER } from "../lib/dossierCategoryNormalize.js";

export default function CategoryChips({ categories, onNavigate }) {
  const raw = Object.entries(categories || {}).filter(
    ([, v]) => Array.isArray(v?.claims) && v.claims.length > 0
  );
  if (!raw.length) return null;

  const orderIndex = (k) => {
    const i = EDITORIAL_CATEGORY_ORDER.indexOf(k);
    return i === -1 ? 999 : i;
  };
  const entries = [...raw].sort((a, b) => orderIndex(a[0]) - orderIndex(b[0]));

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
