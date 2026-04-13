import { useEffect, useMemo, useState } from "react";
import SenatorCard from "../components/SenatorCard.jsx";
import BottomBar from "../components/BottomBar.jsx";
import { DIRECTORY_SENATORS } from "../data/senatorsDirectory.js";
import { apiHeaders, apiUrl } from "../lib/api.js";
import { statsFromDossier } from "../lib/dossierStats.js";

async function tryFetchDossierList() {
  const url = apiUrl("/api/v1/senators/dossiers");
  const res = await fetch(url, { headers: apiHeaders() });
  if (!res.ok) return null;
  const data = await res.json();
  return Array.isArray(data) ? data : data?.dossiers ?? null;
}

async function fetchDossierForCard(bg) {
  const url = apiUrl(`/api/v1/senators/${encodeURIComponent(bg)}/dossier`);
  const res = await fetch(url, { headers: apiHeaders() });
  if (!res.ok) return null;
  return res.json();
}

export default function HomePage() {
  const [query, setQuery] = useState("");
  const [cards, setCards] = useState(() =>
    DIRECTORY_SENATORS.map((s) => ({
      ...s,
      concern_tier: "MODERATE",
      finding_count: 0,
      last_updated: "",
    }))
  );
  const [loadNote, setLoadNote] = useState("Loading directory…");

  useEffect(() => {
    let cancelled = false;

    async function run() {
      const list = await tryFetchDossierList().catch(() => null);
      if (cancelled) return;

      if (list && list.length) {
        const merged = list.map((row) => ({
          name: row.name || row.senator_name || "Unknown",
          bioguide_id: row.bioguide_id || row.bioguideId,
          state: row.state || "—",
          party: row.party || "—",
          concern_tier: (row.concern_tier || "MODERATE").toUpperCase(),
          finding_count: row.finding_count ?? 0,
          last_updated: row.last_updated || row.completed_at || "",
        }));
        setCards(merged);
        setLoadNote("Directory from API.");
        return;
      }

      setLoadNote(
        "Using static senator list — enriching from API when a key is set."
      );
      const base = DIRECTORY_SENATORS.map((s) => ({
        ...s,
        concern_tier: "MODERATE",
        finding_count: 0,
        last_updated: "",
      }));

      const enriched = await Promise.all(
        base.map(async (row) => {
          const data = await fetchDossierForCard(row.bioguide_id).catch(
            () => null
          );
          const st = statsFromDossier(data);
          if (!st) return row;
          return {
            ...row,
            concern_tier: st.concern_tier,
            finding_count: st.finding_count,
            last_updated: st.last_updated,
          };
        })
      );

      if (!cancelled) setCards(enriched);
    }

    run();
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return cards;
    return cards.filter(
      (c) =>
        c.name.toLowerCase().includes(q) ||
        c.bioguide_id.toLowerCase().includes(q) ||
        c.state.toLowerCase().includes(q)
    );
  }, [cards, query]);

  return (
    <div className="oc-home">
      <header className="oc-header-bar">
        <div>
          <div className="oc-header-brand">OPEN CASE</div>
          <p className="oc-header-sub">
            Public records. Signed findings. No verdicts.
          </p>
        </div>
        <span className="oc-header-tag">VERIFIED RECORDS</span>
      </header>

      <div className="oc-search-wrap">
        <label className="sr-only" htmlFor="oc-search">
          Search directory
        </label>
        <input
          id="oc-search"
          className="oc-search"
          type="search"
          placeholder="Search senators, staffers, donors, or firms..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          autoComplete="off"
        />
      </div>

      <p className="oc-home-status">{loadNote}</p>

      <div id="directory" className="oc-grid">
        {filtered.map((c) => (
          <SenatorCard
            key={c.bioguide_id}
            name={c.name}
            state={c.state}
            party={c.party}
            bioguide_id={c.bioguide_id}
            concern_tier={c.concern_tier}
            finding_count={c.finding_count}
            last_updated={c.last_updated}
          />
        ))}
      </div>

      <footer id="receipt" className="oc-footer-disclaimer">
        All findings are documented from public records. Open Case does not
        assert causation or wrongdoing.
      </footer>

      <BottomBar />
    </div>
  );
}
