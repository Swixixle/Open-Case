import { useEffect, useMemo, useRef, useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import BottomBar from "../components/BottomBar.jsx";
import CategoryChips from "../components/CategoryChips.jsx";
import ClaimsAccordion from "../components/ClaimsAccordion.jsx";
import ConcernBadge from "../components/ConcernBadge.jsx";
import EpistemicBar from "../components/EpistemicBar.jsx";
import GapAnalysis from "../components/GapAnalysis.jsx";
import InfluenceGraphSections from "../components/InfluenceGraphSections.jsx";
import InvestigationSummary from "../components/InvestigationSummary.jsx";
import InvestigationReceipt from "../components/InvestigationReceipt.jsx";
import LoadingScreen from "../components/LoadingScreen.jsx";
import MarkdownBlock from "../components/MarkdownBlock.jsx";
import DataStatusBanner from "../components/DataStatusBanner.jsx";
import PatternAlerts from "../components/PatternAlerts.jsx";
import SixTabProfile from "../components/SixTabProfile.jsx";
import StaffNetwork from "../components/StaffNetwork.jsx";
import StoryAngles from "../components/StoryAngles.jsx";
import Timeline from "../components/Timeline.jsx";
import { DIRECTORY_OFFICIALS } from "../data/officialsDirectory.js";
import { apiUrl, fetchCaseLookupByBioguide, fetchCaseReport, apiHeaders } from "../lib/api.js";
import CongressPortrait from "../components/CongressPortrait.jsx";
import {
  categoriesWithClaims,
  concernTierFromDossier,
  concernTierFromReport,
  countTotalFindings,
  epistemicDistributionFromDossier,
  firstNarrativeParagraph,
  formatDisplayDate,
  dataStatusBannerReportFromDossier,
  dossierGovernmentLevel,
  timelineClaims,
  topPatternAlertScore,
} from "../lib/dossierParse.js";
import {
  firstEditorialNarrative,
  normalizeDossierCategories,
} from "../lib/dossierCategoryNormalize.js";
import { buildRefUrlMap } from "../lib/sourceRefResolve.js";
import { subjectTypeLabel } from "../lib/subjectLabels.js";

const CASE_UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function scrollToAnchor(id) {
  requestAnimationFrame(() => {
    document.getElementById(id)?.scrollIntoView({
      behavior: "smooth",
      block: "start",
    });
  });
}

function dossierHasRenderableContent(data) {
  if (!data || typeof data !== "object") return false;
  if (data.status === "building") return false;
  const cats = data.deep_research?.categories;
  const hasCats =
    cats && typeof cats === "object" && Object.keys(cats).length > 0;
  const hasAlerts =
    Array.isArray(data.pattern_alerts) && data.pattern_alerts.length > 0;
  if (data.status === "completed") return true;
  return Boolean(hasCats || hasAlerts);
}

function reportTimelineToClaims(timeline) {
  if (!Array.isArray(timeline)) return [];
  return timeline.map((e) => ({
    date: e.date_of_event,
    claim: [e.title, e.body].filter(Boolean).join(" — "),
    source: e.source_url,
    epistemic_level: e.epistemic_level,
    confidence: e.confidence,
    _categoryLabel: (e.entry_type || "event").replace(/_/g, " "),
    _categoryKey: e.entry_type,
  }));
}

function cacheKey(kind, id) {
  return `oc-cache-${kind}-${id}`;
}

function reportCaseGovernmentLevel(report) {
  const idBlock = report?.sections?.identity?.[0];
  const fromCase = idBlock?.case_government_level;
  if (fromCase) return fromCase;
  return idBlock?.profile?.government_level || "";
}

export default function OfficialPage() {
  const { id } = useParams();
  const isCaseUuid = id && CASE_UUID_RE.test(id);

  if (isCaseUuid) {
    return <OfficialCasePage caseId={id} />;
  }
  return <OfficialSenatorDossierPage bioguideId={id} />;
}

/** Case report-driven profile (UUID). */
function OfficialCasePage({ caseId }) {
  const [loadStatus, setLoadStatus] = useState("initial");
  const [report, setReport] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [streamDoneAt, setStreamDoneAt] = useState(null);
  const [newRuleIds, setNewRuleIds] = useState(() => new Set());
  const esRef = useRef(null);
  /* Bumps on effect cleanup so in-flight fetches from a previous effect (e.g. React Strict Mode
   * double-mount) do not call setState; avoids stuck "Loading case" when the first response
   * is discarded and the second request must win. */
  const reportLoadTokenRef = useRef(0);

  useEffect(() => {
    const loadToken = ++reportLoadTokenRef.current;
    const ac = new AbortController();
    setLoadStatus("initial");
    setReport(null);
    setStreamDoneAt(null);
    setNewRuleIds(new Set());

    const cached = (() => {
      try {
        const raw = localStorage.getItem(cacheKey("report", caseId));
        return raw ? JSON.parse(raw) : null;
      } catch {
        return null;
      }
    })();
    if (cached && typeof cached === "object" && cached !== null && !Array.isArray(cached)) {
      setReport(cached);
    }

    setRefreshing(true);
    fetchCaseReport(caseId, { signal: ac.signal, demoInternalSignals: true })
      .then((data) => {
        if (loadToken !== reportLoadTokenRef.current) {
          if (import.meta.env.DEV) {
            // eslint-disable-next-line no-console
            console.info("[open-case] case report: stale response ignored", {
              caseId,
              loadToken,
              current: reportLoadTokenRef.current,
            });
          }
          return;
        }
        if (!data) {
          setLoadStatus("not_found");
          if (import.meta.env.DEV) {
            // eslint-disable-next-line no-console
            console.info("[open-case] case report: not_found", { caseId, loadStatus: "not_found" });
          }
          return;
        }
        if (import.meta.env.DEV) {
          // eslint-disable-next-line no-console
          console.info("[open-case] case report: applying to state", {
            caseId,
            loadToken,
            signals: Array.isArray(data.signals) ? data.signals.length : null,
            pattern_alerts: Array.isArray(data.pattern_alerts)
              ? data.pattern_alerts.length
              : null,
            subject: data.subject,
          });
        }
        try {
          localStorage.setItem(cacheKey("report", caseId), JSON.stringify(data));
        } catch {
          /* ignore */
        }
        setReport(data);
        setLoadStatus("complete");
        if (import.meta.env.DEV) {
          // eslint-disable-next-line no-console
          console.info("[open-case] case report: set complete", {
            caseId,
            loadToken,
            nextLoadStatus: "complete",
          });
        }
      })
      .catch((e) => {
        const isAbort =
          (e && typeof e === "object" && e.name === "AbortError") ||
          (e && String(e).includes("aborted"));
        if (isAbort) {
          if (import.meta.env.DEV) {
            // eslint-disable-next-line no-console
            console.info(
              "[open-case] case report fetch aborted (remount or navigation)",
              caseId
            );
          }
          return;
        }
        if (loadToken !== reportLoadTokenRef.current) {
          return;
        }
        // eslint-disable-next-line no-console
        console.error("[open-case] case report fetch failed", caseId, e);
        setLoadStatus("load_error");
      })
      .finally(() => {
        setRefreshing(false);
      });

    return () => {
      ac.abort();
      reportLoadTokenRef.current += 1;
    };
  }, [caseId]);

  useEffect(() => {
    if (!report?.pattern_alerts_refresh_pending || !report?.pattern_alerts_stream?.path) {
      return;
    }
    const path = report.pattern_alerts_stream.path;
    const url = apiUrl(path);
    const es = new EventSource(url);
    esRef.current = es;
    es.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "pattern_alerts" && Array.isArray(msg.pattern_alerts)) {
          const incoming = msg.pattern_alerts;
          setReport((r) => {
            if (!r) return r;
            const prev = new Set((r.pattern_alerts || []).map((a) => a.rule_id));
            const fresh = new Set();
            for (const a of incoming) {
              if (a.rule_id && !prev.has(a.rule_id)) fresh.add(a.rule_id);
            }
            requestAnimationFrame(() => setNewRuleIds(fresh));
            return {
              ...r,
              pattern_alerts: incoming,
              pattern_alerts_refresh_pending: false,
            };
          });
          setStreamDoneAt(new Date());
        }
        if (msg.type === "error") {
          setStreamDoneAt(new Date());
        }
      } catch {
        /* ignore */
      }
    };
    es.onerror = () => {
      es.close();
    };
    return () => {
      es.close();
      esRef.current = null;
    };
  }, [report?.pattern_alerts_refresh_pending, report?.pattern_alerts_stream?.path, caseId]);

  const refUrlMap = useMemo(
    () => buildRefUrlMap(report || {}),
    [report]
  );

  const shareReceipt = () => {
    const verifyHref = apiUrl(`/api/v1/cases/${caseId}/report/view`);
    const verifyAbs =
      typeof window !== "undefined"
        ? verifyHref.startsWith("http")
          ? verifyHref
          : `${window.location.origin}${verifyHref}`
        : verifyHref;
    const title = `Open Case — ${report?.subject || "Case"}`;
    if (navigator.share) {
      navigator.share({ title, text: `Verify report: ${verifyAbs}`, url: verifyAbs }).catch(() => {
        navigator.clipboard?.writeText(`${title}\n${verifyAbs}`);
      });
    } else {
      navigator.clipboard?.writeText(`${title}\n${verifyAbs}`);
    }
  };

  if (loadStatus === "initial" && !report) {
    return (
      <>
        <LoadingScreen subjectName="Loading case" stateLine="CASE REPORT" />
        <BottomBar variant="official" onShareReceipt={shareReceipt} />
      </>
    );
  }

  if (loadStatus === "not_found") {
    return (
      <div className="oc-dossier-wrap">
        <div className="oc-error-panel">
          <h1>NO REPORT FOUND</h1>
          <p>No signed case report for this id.</p>
          <p>
            <Link to="/">← Back</Link>
          </p>
        </div>
        <BottomBar variant="official" />
      </div>
    );
  }

  if (loadStatus === "load_error" && !report) {
    return (
      <div className="oc-dossier-wrap">
        <div className="oc-error-panel">
          <h1>REPORT UNAVAILABLE</h1>
          <p>
            The report could not be loaded. Check the connection, CORS, and that{" "}
            <code className="oc-mono">VITE_OPEN_CASE_API_BASE</code> matches the API
            (see browser console).
          </p>
          <p>
            <Link to="/">← Back</Link>
          </p>
        </div>
        <BottomBar variant="official" />
      </div>
    );
  }

  const displayName = report?.subject || report?.title || "Subject";
  const subjType = report?.subject_type || "public_official";
  const titleLine = subjectTypeLabel(subjType);
  const tier = concernTierFromReport(report);
  const epDist = report?.epistemic_distribution;
  const lastInv = formatDisplayDate(report?.opened_at || "");
  const tlines = reportTimelineToClaims(report?.timeline);
  const alerts = report?.pattern_alerts || [];
  const photoBioguide =
    report?.sections?.identity?.[0]?.profile?.bioguide_id || null;

  return (
    <div className="oc-dossier-wrap">
      {report?.pattern_alerts_refresh_pending || refreshing ? (
        <p className="oc-updating-banner oc-mono">
          Updating record…
        </p>
      ) : null}
      {streamDoneAt ? (
        <p className="oc-stream-done oc-mono">
          Record updated {streamDoneAt.toLocaleTimeString()}
        </p>
      ) : null}

      <div className="oc-dossier-shell">
        <aside className="oc-dossier-sidebar">
          <CongressPortrait
            bioguideId={photoBioguide}
            name={displayName}
            variant="sidebar"
          />
          <h1 className="oc-sidebar-name">{displayName.toUpperCase()}</h1>
          <p className="oc-sidebar-meta">{titleLine}</p>

          <hr className="oc-sidebar-hr" />
          <p className="oc-sidebar-section-title">CONCERN LEVEL</p>
          <ConcernBadge level={tier} size="md" />

          <hr className="oc-sidebar-hr" />
          <p className="oc-sidebar-section-title">EPISTEMIC MIX</p>
          <EpistemicBar distribution={epDist} />

          <hr className="oc-sidebar-hr" />
          <p className="oc-sidebar-section-title">LAST INVESTIGATED</p>
          <p className="oc-sidebar-muted">{lastInv}</p>

          <hr className="oc-sidebar-hr" />
          <a
            className="oc-btn-sidebar"
            href={apiUrl(`/api/v1/cases/${encodeURIComponent(caseId)}/report`)}
            target="_blank"
            rel="noopener noreferrer"
          >
            DOWNLOAD JSON
          </a>
          <button type="button" className="oc-btn-sidebar" onClick={shareReceipt}>
            SHARE RECEIPT ↑
          </button>
          <a
            className="oc-btn-sidebar"
            href={apiUrl(`/api/v1/cases/${encodeURIComponent(caseId)}/report/view`)}
            target="_blank"
            rel="noopener noreferrer"
          >
            VERIFY →
          </a>
        </aside>

        <main className="oc-dossier-main">
          <InvestigationSummary caseId={caseId} />
          <section className="oc-hero-dossier">
            <div className="oc-hero-dossier-head">
              <CongressPortrait
                bioguideId={photoBioguide}
                name={displayName}
                variant="hero"
              />
              <div className="oc-hero-dossier-head-main">
                <div className="oc-hero-dossier-top">
                  <h2 className="oc-hero-dossier-title">
                    {displayName.toUpperCase()}
                  </h2>
                  <ConcernBadge level={tier} size="lg" />
                </div>
                <p className="oc-sidebar-meta" style={{ marginTop: "0.5rem" }}>
                  {titleLine}
                </p>
              </div>
            </div>
            <hr className="oc-hero-rule" />
            <EpistemicBar distribution={epDist} />
            <MarkdownBlock className="oc-hero-narrative" style={{ marginTop: "1rem" }}>
              {report?.summary ||
                "Public records case file. Review pattern alerts and tabbed evidence below."}
            </MarkdownBlock>
          </section>

          {String(reportCaseGovernmentLevel(report)).toLowerCase() === "local" ? (
            <DataStatusBanner report={report} />
          ) : null}

          <PatternAlerts alerts={alerts} newRuleIds={newRuleIds} />

          <SixTabProfile mode="report" report={report} subjectType={subjType} />

          <Timeline
            claims={tlines}
            refUrlMap={refUrlMap}
            dossier={report}
            defaultCollapsed
            maxItemsWhenCollapsed={3}
            criticalOnlyWhenCollapsed
          />

          <InvestigationReceipt caseReport={report} caseId={caseId} />

          <p className="oc-footer-disclaimer" style={{ marginTop: "2rem" }}>
            {report?.legal_liability_note ||
              "These findings document public records only. They do not prove causation or wrongdoing."}
          </p>
        </main>
      </div>

      <BottomBar variant="official" onShareReceipt={shareReceipt} />
    </div>
  );
}

/** Senator dossier path (bioguide id). */
function OfficialSenatorDossierPage({ bioguideId }) {
  const [loadStatus, setLoadStatus] = useState("initial");
  const [dossier, setDossier] = useState(null);
  const [pollSession, setPollSession] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [caseRedirectId, setCaseRedirectId] = useState(null);
  const completedRef = useRef(false);

  useEffect(() => {
    setPollSession(0);
    setDossier(null);
    setLoadStatus("initial");
    setCaseRedirectId(null);
  }, [bioguideId]);

  const dirMeta = DIRECTORY_OFFICIALS.find(
    (s) => s.bioguide_id === bioguideId
  );
  const displayName =
    dossier?.subject?.name || dirMeta?.name || bioguideId || "Official";
  const subjectType = dirMeta?.subject_type || "senator";

  useEffect(() => {
    try {
      const raw = localStorage.getItem(cacheKey("dossier", bioguideId));
      if (raw) {
        const c = JSON.parse(raw);
        if (dossierHasRenderableContent(c)) setDossier(c);
      }
    } catch {
      /* ignore */
    }
  }, [bioguideId]);

  useEffect(() => {
    completedRef.current = false;
    let intervalId;
    let timeoutId;
    let cancelled = false;

    const clearTimers = () => {
      if (intervalId) clearInterval(intervalId);
      if (timeoutId) clearTimeout(timeoutId);
      intervalId = undefined;
      timeoutId = undefined;
    };

    const fetchOnce = async () => {
      if (cancelled) return;
      setRefreshing(true);
      try {
        const res = await fetch(
          apiUrl(
            `/api/v1/senators/${encodeURIComponent(bioguideId || "")}/dossier`
          ),
          { headers: apiHeaders() }
        );

        if (cancelled) return;
        setRefreshing(false);

        if (res.status === 401 || res.status === 403) {
          setLoadStatus("api_key");
          clearTimers();
          return;
        }

        if (res.status === 404) {
          const lookup = await fetchCaseLookupByBioguide(bioguideId);
          if (lookup?.case_id && !cancelled) {
            setCaseRedirectId(lookup.case_id);
            clearTimers();
            return;
          }
          setLoadStatus("not_found");
          setDossier(null);
          clearTimers();
          return;
        }

        let data;
        try {
          data = await res.json();
        } catch {
          setLoadStatus("network");
          clearTimers();
          return;
        }

        if (!res.ok) {
          setLoadStatus("network");
          clearTimers();
          return;
        }

        if (data.status === "building") {
          setLoadStatus("building");
          return;
        }

        if (dossierHasRenderableContent(data)) {
          completedRef.current = true;
          setDossier(data);
          setLoadStatus("complete");
          try {
            localStorage.setItem(cacheKey("dossier", bioguideId), JSON.stringify(data));
          } catch {
            /* ignore */
          }
          clearTimers();
        }
      } catch {
        if (!cancelled) {
          setRefreshing(false);
          setLoadStatus("network");
          clearTimers();
        }
      }
    };

    fetchOnce();
    intervalId = setInterval(fetchOnce, 5000);

    timeoutId = setTimeout(() => {
      if (intervalId) clearInterval(intervalId);
      intervalId = undefined;
      if (!cancelled && !completedRef.current) {
        setLoadStatus((s) => (s === "complete" ? "complete" : "timeout"));
      }
    }, 90000);

    return () => {
      cancelled = true;
      clearTimers();
    };
  }, [bioguideId, pollSession]);

  const runInvestigation = async () => {
    const bg = encodeURIComponent(bioguideId || "");
    try {
      const res = await fetch(apiUrl(`/api/v1/senators/${bg}/dossier`), {
        method: "POST",
        headers: {
          ...apiHeaders(),
          "Content-Type": "application/json",
        },
      });
      if (res.status === 401 || res.status === 403) {
        setLoadStatus("api_key");
        return;
      }
      if (!res.ok) {
        setLoadStatus("network");
        return;
      }
      setDossier(null);
      setPollSession((n) => n + 1);
      setLoadStatus("building");
    } catch {
      setLoadStatus("network");
    }
  };

  const retryPolling = () => {
    setDossier(null);
    setLoadStatus("initial");
    setPollSession((n) => n + 1);
  };

  if (caseRedirectId) {
    return <Navigate to={`/official/${caseRedirectId}`} replace />;
  }

  const shareReceipt = () => {
    const basePath = import.meta.env.BASE_URL.replace(/\/$/, "");
    const id = dossier?.dossier_id;
    if (!id) {
      scrollToAnchor("receipt");
      return;
    }
    const url = `${window.location.origin}${basePath}/verify/${id}`;
    const title = `Open Case — ${displayName}`;
    if (navigator.share) {
      navigator.share({ title, text: `Verify receipt: ${url}`, url }).catch(() => {
        navigator.clipboard?.writeText(`${title}\n${url}`);
      });
    } else {
      navigator.clipboard?.writeText(`${title}\n${url}`);
    }
  };

  if (loadStatus === "initial" || loadStatus === "building") {
    const isBuilding = loadStatus === "building";
    return (
      <>
        <LoadingScreen
          subjectName={displayName}
          senatorName={displayName}
          sub={
            isBuilding
              ? "Deep research in progress. This takes 2–5 minutes per senator."
              : ""
          }
          stateLine={
            isBuilding
              ? "Polling for completed dossier…"
              : dirMeta
                ? `FIELD REPORT · ${dirMeta.state}`
                : "FIELD REPORT · US SENATE"
          }
        />
        <BottomBar variant="official" onShareReceipt={shareReceipt} />
      </>
    );
  }

  if (loadStatus === "api_key") {
    return (
      <div className="oc-dossier-wrap">
        <div className="oc-error-panel">
          <h1>API KEY</h1>
          <p>API key missing or invalid. Set VITE_OPEN_CASE_API_KEY.</p>
          <p>
            <Link to="/">← Back to directory</Link>
          </p>
        </div>
        <BottomBar variant="official" />
      </div>
    );
  }

  if (loadStatus === "not_found") {
    return (
      <div className="oc-dossier-wrap">
        <div className="oc-error-panel">
          <h1>NO DOSSIER FOUND</h1>
          <p>No dossier found for this official yet.</p>
          <p>
            <button
              type="button"
              className="oc-btn-sidebar"
              onClick={runInvestigation}
            >
              Run investigation →
            </button>
          </p>
          <p>
            <Link to="/">← Back to directory</Link>
          </p>
        </div>
        <BottomBar variant="official" />
      </div>
    );
  }

  if (loadStatus === "timeout") {
    return (
      <div className="oc-dossier-wrap">
        <div className="oc-error-panel">
          <h1>TAKING LONGER THAN EXPECTED</h1>
          <p>The dossier build is still running or the request timed out.</p>
          <p>
            <button type="button" className="oc-btn-sidebar" onClick={retryPolling}>
              Retry →
            </button>
          </p>
          <p>
            <Link to="/">← Back to directory</Link>
          </p>
        </div>
        <BottomBar variant="official" />
      </div>
    );
  }

  if (loadStatus === "network") {
    return (
      <div className="oc-dossier-wrap">
        <div className="oc-error-panel">
          <h1>CONNECTION</h1>
          <p>Could not connect to Open Case API.</p>
          <p>
            <button type="button" className="oc-btn-sidebar" onClick={retryPolling}>
              Retry →
            </button>
          </p>
          <p>
            <Link to="/">← Back to directory</Link>
          </p>
        </div>
        <BottomBar variant="official" />
      </div>
    );
  }

  const data = dossier;
  if (!data) {
    return (
      <div className="oc-dossier-wrap">
        <div className="oc-error-panel">
          <h1>RECORD UNAVAILABLE</h1>
          <p>No data returned.</p>
          <p>
            <Link to="/">← Back to directory</Link>
          </p>
        </div>
        <BottomBar variant="official" />
      </div>
    );
  }

  const sub = data.subject || {};
  const state = sub.state || dirMeta?.state || "";
  const party = sub.party || dirMeta?.party || "";
  const committees = Array.isArray(sub.committees) ? sub.committees : [];
  const yearsInOffice = sub.years_in_office;
  const sinceYear =
    typeof yearsInOffice === "number" && yearsInOffice > 0
      ? new Date().getFullYear() - yearsInOffice
      : null;

  const rawCats = data.deep_research?.categories || {};
  const displayCategories = useMemo(
    () => normalizeDossierCategories(rawCats),
    [rawCats]
  );
  const refUrlMap = useMemo(() => buildRefUrlMap(data), [data]);
  const narrative =
    firstEditorialNarrative(displayCategories) ||
    firstNarrativeParagraph(rawCats) ||
    "Public records research in progress for this official.";
  const tier = concernTierFromDossier(data);
  const epDist = epistemicDistributionFromDossier(data);
  const nFind = countTotalFindings(displayCategories);
  const nCat = categoriesWithClaims(displayCategories).length;
  const tlines = timelineClaims(displayCategories);
  const updated = formatDisplayDate(
    data.completed_at || data.generated_at || ""
  );
  const topScore = topPatternAlertScore(data);

  const downloadPdfSidebar = async () => {
    const did = data.dossier_id;
    if (!did) return;
    try {
      const r = await fetch(apiUrl(`/api/v1/dossiers/${did}/pdf`), {
        headers: apiHeaders(),
      });
      if (!r.ok) throw new Error("pdf");
      const blob = await r.blob();
      const u = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = u;
      a.download = `dossier-${did}.pdf`;
      a.click();
      URL.revokeObjectURL(u);
    } catch {
      window.open(apiUrl(`/api/v1/dossiers/${did}/pdf`), "_blank");
    }
  };

  return (
    <div className="oc-dossier-wrap">
      {refreshing ? (
        <p className="oc-updating-banner oc-mono">Updating record…</p>
      ) : null}
      <div className="oc-dossier-shell">
        <aside className="oc-dossier-sidebar">
          <CongressPortrait
            bioguideId={bioguideId}
            name={displayName}
            variant="sidebar"
          />
          <h1 className="oc-sidebar-name">{displayName.toUpperCase()}</h1>
          <p className="oc-sidebar-meta">
            {subjectTypeLabel(subjectType)}
            {state ? ` · ${state}` : ""}
            {party
              ? ` · ${party === "D" ? "Democrat" : party === "R" ? "Republican" : party === "I" ? "Independent" : party}`
              : ""}
          </p>
          <p className="oc-sidebar-since">
            {sinceYear ? `In office since ${sinceYear}` : ""}
          </p>

          <hr className="oc-sidebar-hr" />
          <p className="oc-sidebar-section-title">CONCERN LEVEL</p>
          <ConcernBadge level={tier} size="md" />

          <hr className="oc-sidebar-hr" />
          <p className="oc-sidebar-section-title">EPISTEMIC MIX</p>
          {epDist ? (
            <EpistemicBar distribution={epDist} />
          ) : (
            <p className="oc-sidebar-muted">From pattern alerts only when present.</p>
          )}

          <hr className="oc-sidebar-hr" />
          <p className="oc-sidebar-section-title">COMMITTEES</p>
          {committees.length ? (
            <ul className="oc-sidebar-list">
              {committees.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          ) : (
            <p className="oc-sidebar-muted">No committee data</p>
          )}

          <hr className="oc-sidebar-hr" />
          <p className="oc-sidebar-section-title">QUICK STATS</p>
          <p className="oc-sidebar-muted">
            {nFind} total findings
            <br />
            {nCat} categories flagged
            <br />
            Top alert {topScore != null ? topScore.toFixed(3) : "—"}
            <br />
            Updated {updated}
          </p>

          <hr className="oc-sidebar-hr" />
          <button
            type="button"
            className="oc-btn-sidebar"
            onClick={downloadPdfSidebar}
          >
            DOWNLOAD PDF
          </button>
          <button
            type="button"
            className="oc-btn-sidebar"
            onClick={shareReceipt}
          >
            SHARE RECEIPT ↑
          </button>
          <Link
            className="oc-btn-sidebar"
            to={data.dossier_id ? `/verify/${data.dossier_id}` : "/"}
          >
            VERIFY →
          </Link>
        </aside>

        <main className="oc-dossier-main">
          <section className="oc-hero-dossier">
            <div className="oc-hero-dossier-head">
              <CongressPortrait
                bioguideId={bioguideId}
                name={displayName}
                variant="hero"
              />
              <div className="oc-hero-dossier-head-main">
                <div className="oc-hero-dossier-top">
                  <h2 className="oc-hero-dossier-title">
                    {displayName.toUpperCase()}
                  </h2>
                  <ConcernBadge level={tier} size="lg" />
                </div>
              </div>
            </div>
            <hr className="oc-hero-rule" />
            {epDist ? <EpistemicBar distribution={epDist} /> : null}
            <MarkdownBlock className="oc-hero-narrative">{narrative}</MarkdownBlock>
            <CategoryChips
              categories={displayCategories}
              onNavigate={(anchor) => scrollToAnchor(anchor)}
            />
          </section>

          {String(dossierGovernmentLevel(data, dirMeta)).toLowerCase() === "local" ? (
            <DataStatusBanner
              report={dataStatusBannerReportFromDossier(data, dirMeta, displayName)}
            />
          ) : null}

          <PatternAlerts alerts={data.pattern_alerts} />

          <SixTabProfile
            mode="dossier"
            dossier={data}
            subjectType={subjectType}
            displayCategories={displayCategories}
            refUrlMap={refUrlMap}
          />

          <Timeline
            claims={tlines}
            refUrlMap={refUrlMap}
            dossier={data}
            defaultCollapsed
            maxItemsWhenCollapsed={3}
            criticalOnlyWhenCollapsed
          />

          <ClaimsAccordion
            categories={displayCategories}
            refUrlMap={refUrlMap}
            dossier={data}
          />
          <GapAnalysis gaps={data.gap_analysis} />
          <StaffNetwork staff={data.staff_network} />
          <InfluenceGraphSections dossier={data} />
          <StoryAngles dossier={data} />
          <InvestigationReceipt dossier={data} />

          <p className="oc-footer-disclaimer" style={{ marginTop: "2rem" }}>
            {data.disclaimer ||
              "These findings document public records only. They do not prove causation or wrongdoing."}
          </p>
        </main>
      </div>

      <BottomBar variant="official" onShareReceipt={shareReceipt} />
    </div>
  );
}
