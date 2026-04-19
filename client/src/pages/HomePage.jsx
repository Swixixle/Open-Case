import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams, useNavigate } from "react-router-dom";
import "./HomePage.css";
import OfficialCard from "../components/OfficialCard.jsx";

/** Shown on landing trust strip; update when pytest count changes meaningfully. */
const PYTEST_FLOOR_DISPLAY = 344;
import BottomBar from "../components/BottomBar.jsx";
import ConcernBadge from "../components/ConcernBadge.jsx";
import { DIRECTORY_OFFICIALS } from "../data/officialsDirectory.js";
import {
  apiHeaders,
  apiUrl,
  fetchCasesList,
  fetchSubjectsSearch,
} from "../lib/api.js";
import { statsFromDossier } from "../lib/dossierStats.js";
import {
  GOVERNMENT_NAV_TREE,
  navMatchesSelection,
} from "../lib/navigationTaxonomy.js";
import { subjectTypeLabel } from "../lib/subjectLabels.js";
import { concernTierFromReport, topPatternAlertScore } from "../lib/dossierParse.js";
import {
  didYouMeanFromTopHit,
  liveRankBoost,
  matchConfidenceFromScore,
  mergeLegacySearchResults,
} from "../lib/subjectSearchUi.js";

async function tryFetchDossierList() {
  const url = apiUrl("/api/v1/senators/dossiers");
  const res = await fetch(url, { headers: apiHeaders() });
  if (!res.ok) return null;
  const data = await res.json();
  return Array.isArray(data) ? data : data?.dossiers ?? null;
}

function navTreeForLevel(levelKey) {
  if (!levelKey) return null;
  return GOVERNMENT_NAV_TREE.find((t) => t.levelId === levelKey) || null;
}

function branchGroupMatchesUrl(br, branch, level) {
  return br.items.some(
    (it) => it.branch === branch && it.government_level === level
  );
}

async function fetchDossierForCard(bg) {
  const url = apiUrl(`/api/v1/senators/${encodeURIComponent(bg)}/dossier`);
  const res = await fetch(url, { headers: apiHeaders() });
  if (res.status === 401 || res.status === 403 || res.status === 404) {
    return null;
  }
  if (!res.ok) return null;
  try {
    return await res.json();
  } catch {
    return null;
  }
}

function filterDirectoryRows(branch, level, type) {
  if (!branch && !level && !type) return DIRECTORY_OFFICIALS;
  return DIRECTORY_OFFICIALS.filter((row) => {
    if (type && row.subject_type !== type) return false;
    if (level && row.government_level !== level) return false;
    if (branch && row.branch !== branch) return false;
    return true;
  });
}

async function enrichCaseCards(caseRows) {
  const sliced = caseRows.slice(0, 8);
  return Promise.all(
    sliced.map(async (c) => {
      let pattern_top_score = null;
      let concern_tier = "MODERATE";
      try {
        const r = await fetch(
          apiUrl(`/api/v1/cases/${encodeURIComponent(c.id)}/report`),
          { headers: apiHeaders() }
        );
        if (r.ok) {
          const rep = await r.json();
          pattern_top_score = topPatternAlertScore(rep);
          concern_tier = concernTierFromReport(rep);
        }
      } catch {
        /* ignore */
      }
      return {
        name: c.subject_name,
        title: c.title,
        bioguide_id: "",
        case_id: c.id,
        state: c.jurisdiction || "—",
        party: "—",
        subject_type: c.subject_type,
        branch: c.branch || "",
        government_level: c.government_level || "",
        concern_tier,
        finding_count: 0,
        last_updated: c.created_at || "",
        is_building: false,
        pattern_top_score,
      };
    })
  );
}

export default function HomePage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const branch = params.get("branch") || "";
  const level = params.get("level") || "";
  const type = params.get("type") || "";

  const isAllView = !branch && !level && !type;
  const [searchDraft, setSearchDraft] = useState("");
  const [query, setQuery] = useState("");
  const [searchPayload, setSearchPayload] = useState(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [cards, setCards] = useState([]);
  const [loadNote, setLoadNote] = useState("Loading directory…");

  const setTopLevelOnly = useCallback(
    (levelId) => {
      const next = new URLSearchParams();
      next.set("level", levelId);
      setParams(next, { replace: true });
    },
    [setParams]
  );

  const selectBranchGroup = useCallback(
    (br) => {
      const it = br.items[0];
      const next = new URLSearchParams();
      next.set("branch", it.branch);
      next.set("level", it.government_level);
      setParams(next, { replace: true });
    },
    [setParams]
  );

  const setNavLeaf = useCallback(
    (item) => {
      const next = new URLSearchParams();
      next.set("branch", item.branch);
      next.set("level", item.government_level);
      next.set("type", item.subject_type);
      setParams(next, { replace: true });
    },
    [setParams]
  );

  const clearNavFilter = useCallback(() => {
    setParams({}, { replace: true });
  }, [setParams]);

  useEffect(() => {
    const t = setTimeout(() => setQuery(searchDraft.trim()), 300);
    return () => clearTimeout(t);
  }, [searchDraft]);

  useEffect(() => {
    let cancelled = false;
    if (query.length < 2) {
      setSearchPayload(null);
      return;
    }
    (async () => {
      const data = await fetchSubjectsSearch(query).catch(() => null);
      if (cancelled) return;
      setSearchPayload(data && typeof data === "object" ? data : null);
    })();
    return () => {
      cancelled = true;
    };
  }, [query]);

  const rankedSearchHits = useMemo(() => {
    if (!searchPayload) return [];
    const raw =
      Array.isArray(searchPayload.results) && searchPayload.results.length
        ? searchPayload.results.map((r, i) => ({
            ...r,
            key: `${r.source || "res"}-${r.case_id || r.bioguide_id || r.name || i}`,
          }))
        : mergeLegacySearchResults(searchPayload);
    const scored = raw.map((row) => ({
      ...row,
      _rank: (Number(row.match_score) || 0) + liveRankBoost(searchDraft, row.name),
    }));
    scored.sort((a, b) => b._rank - a._rank);
    return scored.slice(0, 8).map(({ _rank, ...r }) => ({
      ...r,
      match_confidence: matchConfidenceFromScore(r.match_score),
      concern_tier: "MODERATE",
      last_investigated: "",
    }));
  }, [searchPayload, searchDraft]);

  const didYouMean = useMemo(
    () => didYouMeanFromTopHit(searchDraft, rankedSearchHits[0]),
    [searchDraft, rankedSearchHits]
  );

  useEffect(() => {
    let cancelled = false;

    async function run() {
      const caseApi = await fetchCasesList({
        branch: branch || undefined,
        government_level: level || undefined,
        subject_type: type || undefined,
        limit: 8,
      }).catch(() => null);

      if (cancelled) return;

      if (caseApi?.cases?.length) {
        const mapped = await enrichCaseCards(caseApi.cases);
        if (!cancelled) {
          setCards(mapped);
          setLoadNote("Recent cases from API.");
        }
        return;
      }

      const list = await tryFetchDossierList().catch(() => null);
      if (cancelled) return;

      if (list && list.length && isAllView) {
        const merged = list.slice(0, 8).map((row) => ({
          name: row.name || row.senator_name || "Unknown",
          title: "U.S. Senator",
          bioguide_id: row.bioguide_id || row.bioguideId,
          case_id: row.case_id || "",
          state: row.state || "—",
          party: row.party || "—",
          subject_type: "senator",
          branch: "legislative",
          government_level: "federal",
          concern_tier: (row.concern_tier || "MODERATE").toUpperCase(),
          finding_count: row.finding_count ?? 0,
          last_updated: row.last_updated || row.completed_at || "",
          is_building: false,
          pattern_top_score: null,
        }));
        setCards(merged);
        setLoadNote("Directory from API.");
        return;
      }

      setLoadNote(
        "Using static directory — open cases in Open Case to populate more rows."
      );
      const base = filterDirectoryRows(branch, level, type).map((s) => ({
        ...s,
        title:
          s.subject_type === "senator"
            ? "U.S. Senator"
            : subjectTypeLabel(s.subject_type),
        concern_tier: "MODERATE",
        finding_count: 0,
        last_updated: "",
        is_building: false,
        pattern_top_score: null,
      }));

      const enriched = await Promise.all(
        base.map(async (row) => {
          if (!row.bioguide_id) return row;
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
            is_building: st.is_building,
            pattern_top_score: st.pattern_top_score,
          };
        })
      );

      if (!cancelled) setCards(enriched.slice(0, 8));
    }

    run();
    return () => {
      cancelled = true;
    };
  }, [branch, level, type, isAllView]);

  const filteredCards = useMemo(() => {
    const q = searchDraft.trim().toLowerCase();
    if (!q) return cards;
    return cards.filter(
      (c) =>
        c.name.toLowerCase().includes(q) ||
        (c.bioguide_id && c.bioguide_id.toLowerCase().includes(q)) ||
        c.state.toLowerCase().includes(q)
    );
  }, [cards, searchDraft]);

  const subTree = navTreeForLevel(level);
  const activeBranchGroup =
    subTree && branch && level
      ? subTree.branches.find((br) => branchGroupMatchesUrl(br, branch, level))
      : null;

  return (
    <div className="oc-home oc-reporter-home">
      <header className="oc-reporter-hero">
        <div className="oc-reporter-hero-inner">
          <p className="oc-reporter-eyebrow">Open Case · Investigative infrastructure</p>
          <h1 className="oc-reporter-headline">
            Money-and-vote pattern detection on live public records
          </h1>
          <p className="oc-reporter-sub">
            Surface proximity between filings, votes, and disclosures — with epistemic labels and
            Ed25519-signed receipts. This engine documents patterns; it does not deliver legal
            verdicts.
          </p>
          <p className="oc-reporter-live" aria-live="polite">
            <span className="oc-reporter-live-dot" aria-hidden />
            Pipeline runs on your deployment: investigate, seal, verify
          </p>
          <div className="oc-reporter-hero-meta">
            <span className="oc-reporter-pill oc-reporter-pill--accent">
              Pattern engine + adapters
            </span>
            <span className="oc-reporter-pill">FEC · Congress · LDA · Courts · Local (pilot)</span>
          </div>
        </div>
      </header>

      <div className="oc-reporter-trust" aria-label="Quality and integrity indicators">
        <div className="oc-reporter-trust-item">
          <span>
            <strong>{PYTEST_FLOOR_DISPLAY}</strong> automated tests in CI
          </span>
        </div>
        <div className="oc-reporter-trust-item">
          <span>
            <strong>Ed25519</strong> signed case bundles &amp; dossiers
          </span>
        </div>
        <div className="oc-reporter-trust-item">
          <span>Source-linked evidence, not narrative-only AI claims</span>
        </div>
      </div>

      <p className="oc-reporter-search-hint">
        Search ties into{" "}
        <code className="oc-mono">GET /api/v1/subjects/search</code> (name + filters). Open a
        profile to see pattern alerts and the signed report.
      </p>

      <div className="oc-search-wrap oc-search-wrap--home">
        <label className="sr-only" htmlFor="oc-search">
          Search officials by name
        </label>
        <div className="oc-search-with-results">
          <input
            id="oc-search"
            className="oc-search"
            type="search"
            placeholder="Enter politician or official name (e.g. Tom Cotton)"
            value={searchDraft}
            onChange={(e) => {
              setSearchDraft(e.target.value);
              setSearchOpen(true);
            }}
            onFocus={() => setSearchOpen(true)}
            onBlur={() => setTimeout(() => setSearchOpen(false), 200)}
            autoComplete="off"
          />
          {searchOpen && query.length >= 2 ? (
            <div className="oc-search-results">
              {didYouMean ? (
                <div className="oc-did-you-mean">
                  Did you mean{" "}
                  <strong className="oc-did-you-mean-name">{didYouMean}</strong>?
                </div>
              ) : null}
              {rankedSearchHits.length ? (
                rankedSearchHits.map((h) => (
                  <button
                    key={h.key}
                    type="button"
                    className={`oc-search-hit oc-search-hit--match-${h.match_confidence}`}
                    onClick={() => {
                      if (h.case_id) navigate(`/official/${h.case_id}`);
                      else if (h.bioguide_id) navigate(`/official/${h.bioguide_id}`);
                    }}
                  >
                    <span className="oc-search-hit-name">{h.name}</span>
                    <span className="oc-mono oc-search-hit-type">
                      {subjectTypeLabel(h.subject_type)}
                    </span>
                    <ConcernBadge level={h.concern_tier} size="sm" />
                    <span className="oc-search-hit-date oc-search-hit-confidence">
                      {h.match_confidence === "high" ? "Strong match" : "Likely match"}
                    </span>
                  </button>
                ))
              ) : (
                <div className="oc-search-empty">
                  <p>No matches in directory.</p>
                  <button
                    type="button"
                    className="oc-btn-sidebar"
                    onClick={() => navigate("/")}
                  >
                    Start investigation →
                  </button>
                </div>
              )}
            </div>
          ) : null}
        </div>
      </div>

      <nav className="oc-home-filter-nav" aria-label="Jurisdiction filters">
        <div className="oc-filter-row oc-filter-row--tier1">
          <button
            type="button"
            className={`oc-filter-btn ${isAllView ? "is-active" : ""}`}
            onClick={() => clearNavFilter()}
          >
            All
          </button>
          <button
            type="button"
            className={`oc-filter-btn ${level === "federal" ? "is-active" : ""}`}
            onClick={() => setTopLevelOnly("federal")}
          >
            Federal
          </button>
          <button
            type="button"
            className={`oc-filter-btn ${level === "state" ? "is-active" : ""}`}
            onClick={() => setTopLevelOnly("state")}
          >
            State
          </button>
          <button
            type="button"
            className={`oc-filter-btn ${level === "local" ? "is-active" : ""}`}
            onClick={() => setTopLevelOnly("local")}
          >
            Local
          </button>
        </div>

        {subTree?.branches?.length ? (
          <div
            className="oc-filter-row oc-filter-row--tier2"
            key={`tier2-${level}`}
          >
            {subTree.branches.map((br) => {
              const active =
                Boolean(branch && level) && branchGroupMatchesUrl(br, branch, level);
              return (
                <button
                  key={br.branchId}
                  type="button"
                  className={`oc-filter-btn ${active ? "is-active" : ""}`}
                  onClick={() => selectBranchGroup(br)}
                >
                  {br.label}
                </button>
              );
            })}
          </div>
        ) : null}

        {activeBranchGroup?.items?.length ? (
          <div
            className="oc-filter-row oc-filter-row--tier3"
            key={`tier3-${branch}-${level}`}
          >
            {activeBranchGroup.items.map((item) => {
              const active =
                Boolean(type) &&
                navMatchesSelection(item, branch, level, type);
              return (
                <button
                  key={`${item.subject_type}-${item.label}`}
                  type="button"
                  className={`oc-filter-btn ${active ? "is-active" : ""}`}
                  onClick={() => setNavLeaf(item)}
                >
                  {item.label}
                </button>
              );
            })}
          </div>
        ) : null}
      </nav>

      <section className="oc-featured-finding" aria-label="Featured example pattern">
        <div className="oc-featured-inner">
          <p className="oc-featured-kicker">Live example · Federal Senate</p>
          <div className="oc-featured-row">
            <span className="oc-featured-name">Tom Cotton</span>
            <span className="oc-mono oc-featured-rule">SOFT_BUNDLE_V1</span>
            <span className="oc-mono oc-featured-score">Score 0.921</span>
          </div>
          <p className="oc-featured-quote">
            &ldquo;A cluster of financial services and defense-sector donations landed within
            the proximity window of a Senate vote on S.J.Res. 95.&rdquo; — documented from FEC
            and roll-call context, not a finding of wrongdoing.
          </p>
          <Link className="oc-featured-cta" to="/official/C001095">
            Open Cotton profile (/official/C001095) →
          </Link>
        </div>
      </section>

      <p className="oc-home-status">{loadNote}</p>

      <div id="directory" className="oc-grid">
        {filteredCards.map((c) => (
          <OfficialCard
            key={c.case_id || c.bioguide_id || c.name}
            name={c.name}
            title={c.title}
            state={c.state}
            party={c.party}
            bioguide_id={c.bioguide_id}
            case_id={c.case_id}
            subject_type={c.subject_type}
            concern_tier={c.concern_tier}
            finding_count={c.finding_count}
            last_updated={c.last_updated}
            is_building={c.is_building}
            pattern_top_score={c.pattern_top_score}
          />
        ))}
      </div>

      <footer id="receipt" className="oc-footer-disclaimer">
        All findings are documented from public records. Open Case does not assert causation,
        corruption, or wrongdoing — only proximity and timing that reporters can verify.
      </footer>

      <BottomBar />
    </div>
  );
}
