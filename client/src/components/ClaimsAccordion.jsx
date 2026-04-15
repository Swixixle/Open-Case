import { useEffect, useMemo, useState } from "react";
import { categoryLabel } from "../lib/constants.js";
import EntityGroupedClaimList from "./EntityGroupedClaimList.jsx";

export default function ClaimsAccordion({ categories }) {
  const keys = useMemo(() => {
    return Object.keys(categories || {}).filter((k) => {
      const claims = categories[k]?.claims;
      return Array.isArray(claims) && claims.length > 0;
    });
  }, [categories]);

  const initialOpen = useMemo(() => {
    const o = {};
    keys.forEach((k, i) => {
      o[k] = i < 2;
    });
    return o;
  }, [keys]);

  const [open, setOpen] = useState(initialOpen);

  const keySig = keys.join("|");
  useEffect(() => {
    const o = {};
    keys.forEach((k, i) => {
      o[k] = i < 2;
    });
    setOpen(o);
  }, [keySig]);

  const toggle = (k) => {
    setOpen((prev) => ({ ...prev, [k]: !prev[k] }));
  };

  if (!keys.length) return null;

  return (
    <section className="oc-section" aria-label="Findings by category">
      <h2 className="oc-section-title">DOCUMENTED FINDINGS</h2>
      {keys.map((key) => {
        const block = categories[key];
        const claims = block.claims || [];
        const isOpen = open[key];
        return (
          <div key={key} id={`cat-${key}`} className="oc-accordion">
            <button
              type="button"
              className="oc-accordion-header"
              aria-expanded={isOpen}
              onClick={() => toggle(key)}
            >
              <span className="oc-accordion-dot" aria-hidden />
              <span>{categoryLabel(key).toUpperCase()}</span>
              <span className="oc-accordion-count">({claims.length})</span>
            </button>
            {isOpen ? (
              <div className="oc-accordion-body">
                <EntityGroupedClaimList claims={claims} categoryKey={key} />
              </div>
            ) : null}
          </div>
        );
      })}
    </section>
  );
}
