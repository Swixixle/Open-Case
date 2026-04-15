import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams, useNavigate } from "react-router-dom";
import OfficialCard from "../components/OfficialCard.jsx";
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
  DEFAULT_FEDERAL_SENATE_NAV,
  GOVERNMENT_NAV_TREE,
  TOP_LEVELS,
  flattenNavLeaves,
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

  const [activeTop, setActiveTop] = useState("federal");
  const [allRecordsMode, setAllRecordsMode] = useState(false);
  const [expandedNavGroups, setExpandedNavGroups] = useState(
    () => new Set(["legislative"])
  );
  const [searchDraft, setSearchDraft] = useState("");
  const [query, setQuery] = useState("");
  const [searchPayload, setSearchPayload] = useState(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [cards, setCards] = useState([]);
  const [loadNote, setLoadNote] = useState("Loading directory…");

  useEffect(() => {
    if (branch && level && type) {
      setAllRecordsMode(false);
      return;
    }
    if (allRecordsMode) return;
    if (activeTop !== "federal") return;
    const next = new URLSearchParams();
    next.set("branch", DEFAULT_FEDERAL_SENATE_NAV.branch);
    next.set("level", DEFAULT_FEDERAL_SENATE_NAV.government_level);
    next.set("type", DEFAULT_FEDERAL_SENATE_NAV.subject_type);
    setParams(next, { replace: true });
  }, [branch, level, type, activeTop, allRecordsMode, setParams]);

  useEffect(() => {
    if (!branch && !level && !type) return;
    const hit = flattenNavLeaves().find(
      (i) =>
        i.branch === branch &&
        i.government_level === level &&
        i.subject_type === type
    );
    if (hit) setActiveTop(hit.topLevelId);
  }, [branch, level, type]);

  useEffect(() => {
    const tree = GOVERNMENT_NAV_TREE.find((x) => x.levelId === activeTop);
    if (!tree?.branches?.length) {
      setExpandedNavGroups(new Set());
      return;
    }
    const hit = tree.branches.find((br) =>
      br.items.some((it) => navMatchesSelection(it, branch, level, type))
    );
    if (hit) {
      setExpandedNavGroups(new Set([hit.branchId]));
    } else {
      setExpandedNavGroups(new Set());
    }
  }, [activeTop, branch, level, type]);

  const toggleNavGroup = useCallback((branchId) => {
    setExpandedNavGroups((prev) => {
      if (prev.has(branchId) && prev.size === 1) return new Set();
      return new Set([branchId]);
    });
  }, []);

  const setNavFilter = useCallback(
    (item) => {
      setAllRecordsMode(false);
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

      if (list && list.length && !branch && !level && !type) {
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
  }, [branch, level, type]);

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

  const subTree = GOVERNMENT_NAV_TREE.find((t) => t.levelId === activeTop);
  const selectValue =
    branch && level && type ? `${branch}|${level}|${type}` : "__all";

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

      <div className="oc-search-wrap oc-search-wrap--home">
        <label className="sr-only" htmlFor="oc-search">
          Search any public official
        </label>
        <div className="oc-search-with-results">
          <input
            id="oc-search"
            className="oc-search"
            type="search"
            placeholder="Search any public official"
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

      <nav className="oc-branch-nav" aria-label="Government branches">
        <div className="oc-branch-tabs">
          {TOP_LEVELS.map((t) => {
            const isFed = t.id === "federal";
            const active = isFed
              ? activeTop === "federal" && !allRecordsMode
              : activeTop === t.id;
            return (
              <button
                key={t.id}
                type="button"
                className={`oc-branch-tab ${active ? "is-active" : ""}`}
                onClick={() => {
                  if (t.id === activeTop && !allRecordsMode) return;
                  setActiveTop(t.id);
                  setAllRecordsMode(false);
                  clearNavFilter();
                }}
              >
                {t.label}
              </button>
            );
          })}
          <button
            type="button"
            className={`oc-branch-tab oc-branch-tab--ghost ${allRecordsMode ? "is-active" : ""}`}
            onClick={() => {
              setAllRecordsMode(true);
              setActiveTop("federal");
              clearNavFilter();
            }}
          >
            All
          </button>
        </div>

        <div className="oc-branch-mobile">
          <label className="sr-only" htmlFor="oc-branch-select">
            Branch navigation
          </label>
          <select
            id="oc-branch-select"
            className="oc-branch-select"
            value={selectValue}
            onChange={(e) => {
              const v = e.target.value;
              if (v === "__all") {
                setAllRecordsMode(true);
                setActiveTop("federal");
                clearNavFilter();
                return;
              }
              setAllRecordsMode(false);
              const [b, l, ty] = v.split("|");
              const next = new URLSearchParams();
              next.set("branch", b);
              next.set("level", l);
              next.set("type", ty);
              setParams(next, { replace: true });
            }}
          >
            <option value="__all">All records</option>
            {flattenNavForSelect().map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>

        {subTree ? (
          <div className="oc-subnav">
            {subTree.branches.map((br) => {
              const open = expandedNavGroups.has(br.branchId);
              return (
                <div
                  key={br.branchId}
                  className={`oc-subnav-group ${open ? "" : "is-collapsed"}`}
                >
                  <button
                    type="button"
                    className="oc-subnav-group-toggle"
                    aria-expanded={open}
                    onClick={() => toggleNavGroup(br.branchId)}
                  >
                    <span>{br.label}</span>
                    <span className="oc-subnav-chevron" aria-hidden>
                      {open ? "\u25BC" : "\u25B6"}
                    </span>
                  </button>
                  <div className="oc-subnav-links">
                    {br.items.map((item) => {
                      const active = navMatchesSelection(
                        item,
                        branch,
                        level,
                        type
                      );
                      return (
                        <button
                          key={`${item.subject_type}-${item.label}`}
                          type="button"
                          className={`oc-subnav-link ${active ? "is-active" : ""}`}
                          onClick={() => setNavFilter(item)}
                        >
                          {item.label}
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        ) : null}
      </nav>

      <section className="oc-featured-finding" aria-label="Featured finding">
        <div className="oc-featured-inner">
          <p className="oc-featured-kicker">Featured finding</p>
          <div className="oc-featured-row">
            <span className="oc-featured-name">Tom Cotton</span>
            <span className="oc-mono oc-featured-rule">SOFT_BUNDLE_V1</span>
            <span className="oc-mono oc-featured-score">Score 0.921</span>
          </div>
          <p className="oc-featured-quote">
            &ldquo;A cluster of financial services donations arrived within days of a
            Senate vote&rdquo;
          </p>
          <Link className="oc-featured-cta" to="/official/C001095">
            View case →
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
        All findings are documented from public records. Open Case does not
        assert causation or wrongdoing.
      </footer>

      <BottomBar />
    </div>
  );
}

function flattenNavForSelect() {
  return flattenNavLeaves().map((item) => ({
    value: `${item.branch}|${item.government_level}|${item.subject_type}`,
    label: `${item.topLevelId.toUpperCase()} · ${item.branchGroupLabel} · ${item.label}`,
  }));
}
