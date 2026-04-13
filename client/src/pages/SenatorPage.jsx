import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import BottomBar from "../components/BottomBar.jsx";
import CategoryChips from "../components/CategoryChips.jsx";
import ClaimsAccordion from "../components/ClaimsAccordion.jsx";
import GapAnalysis from "../components/GapAnalysis.jsx";
import InfluenceGraphSections from "../components/InfluenceGraphSections.jsx";
import InvestigationReceipt from "../components/InvestigationReceipt.jsx";
import LoadingScreen from "../components/LoadingScreen.jsx";
import PatternAlerts from "../components/PatternAlerts.jsx";
import StaffNetwork from "../components/StaffNetwork.jsx";
import StoryAngles from "../components/StoryAngles.jsx";
import Timeline from "../components/Timeline.jsx";
import { DIRECTORY_SENATORS } from "../data/senatorsDirectory.js";
import { apiHeaders, apiUrl } from "../lib/api.js";
import {
  categoriesWithClaims,
  concernTierFromDossier,
  countTotalFindings,
  firstNarrativeParagraph,
  formatDisplayDate,
  timelineClaims,
} from "../lib/dossierParse.js";

function scrollToAnchor(id) {
  requestAnimationFrame(() => {
    document.getElementById(id)?.scrollIntoView({
      behavior: "smooth",
      block: "start",
    });
  });
}

function initials(name) {
  const parts = (name || "").split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    return (
      (parts[0][0] || "") + (parts[parts.length - 1][0] || "")
    ).toUpperCase();
  }
  return (name || "?").slice(0, 2).toUpperCase();
}

function tierBadgeClass(tier) {
  const t = (tier || "MODERATE").toUpperCase();
  if (t === "CRITICAL") return "var(--critical)";
  if (t === "HIGH") return "var(--high)";
  if (t === "LOW") return "var(--low)";
  return "var(--moderate)";
}

/** True when we should render the full dossier layout (partial data OK). */
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

export default function SenatorPage() {
  const { bioguide_id } = useParams();
  const [loadStatus, setLoadStatus] = useState("initial");
  const [dossier, setDossier] = useState(null);
  const [pollSession, setPollSession] = useState(0);
  const completedRef = useRef(false);

  useEffect(() => {
    setPollSession(0);
    setDossier(null);
    setLoadStatus("initial");
  }, [bioguide_id]);

  const dirMeta = DIRECTORY_SENATORS.find(
    (s) => s.bioguide_id === bioguide_id
  );
  const displayName =
    dossier?.subject?.name || dirMeta?.name || bioguide_id || "Senator";

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
      try {
        const res = await fetch(
          apiUrl(
            `/api/v1/senators/${encodeURIComponent(bioguide_id || "")}/dossier`
          ),
          { headers: apiHeaders() }
        );

        if (cancelled) return;

        if (res.status === 401 || res.status === 403) {
          setLoadStatus("api_key");
          clearTimers();
          return;
        }

        if (res.status === 404) {
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
          setDossier(null);
          return;
        }

        if (dossierHasRenderableContent(data)) {
          completedRef.current = true;
          setDossier(data);
          setLoadStatus("complete");
          clearTimers();
        }
      } catch {
        if (!cancelled) {
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
  }, [bioguide_id, pollSession]);

  const runInvestigation = async () => {
    const bg = encodeURIComponent(bioguide_id || "");
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
        <BottomBar variant="senator" onShareReceipt={shareReceipt} />
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
        <BottomBar variant="senator" />
      </div>
    );
  }

  if (loadStatus === "not_found") {
    return (
      <div className="oc-dossier-wrap">
        <div className="oc-error-panel">
          <h1>NO DOSSIER FOUND</h1>
          <p>No dossier found for this senator yet.</p>
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
        <BottomBar variant="senator" />
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
        <BottomBar variant="senator" />
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
        <BottomBar variant="senator" />
      </div>
    );
  }

  const data = dossier;
  if (!data) {
    return (
      <div className="oc-dossier-wrap">
        <div className="oc-error-panel">
          <h1>DOSSIER UNAVAILABLE</h1>
          <p>No data returned.</p>
          <p>
            <Link to="/">← Back to directory</Link>
          </p>
        </div>
        <BottomBar variant="senator" />
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

  const cats = data.deep_research?.categories || {};
  const narrative =
    firstNarrativeParagraph(cats) ||
    "Public records research in progress for this official.";
  const tier = concernTierFromDossier(data);
  const tColor = tierBadgeClass(tier);
  const nFind = countTotalFindings(cats);
  const nCat = categoriesWithClaims(cats).length;
  const tlines = timelineClaims(cats);
  const updated = formatDisplayDate(
    data.completed_at || data.generated_at || ""
  );

  const downloadPdfSidebar = async () => {
    const id = data.dossier_id;
    if (!id) return;
    try {
      const r = await fetch(apiUrl(`/api/v1/dossiers/${id}/pdf`), {
        headers: apiHeaders(),
      });
      if (!r.ok) throw new Error("pdf");
      const blob = await r.blob();
      const u = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = u;
      a.download = `dossier-${id}.pdf`;
      a.click();
      URL.revokeObjectURL(u);
    } catch {
      window.open(apiUrl(`/api/v1/dossiers/${id}/pdf`), "_blank");
    }
  };

  return (
    <div className="oc-dossier-wrap">
      <div className="oc-dossier-shell">
        <aside className="oc-dossier-sidebar">
          <div className="oc-avatar" aria-hidden>
            {initials(displayName)}
          </div>
          <h1 className="oc-sidebar-name">{displayName.toUpperCase()}</h1>
          <p className="oc-sidebar-meta">
            {state}
            {party ? ` · ${party === "D" ? "Democrat" : party === "R" ? "Republican" : party === "I" ? "Independent" : party}` : ""}
          </p>
          <p className="oc-sidebar-since">
            {sinceYear ? `In office since ${sinceYear}` : ""}
          </p>

          <hr className="oc-sidebar-hr" />
          <p className="oc-sidebar-section-title">CONCERN LEVEL</p>
          <div className="oc-tier" style={{ color: tColor }}>
            <span
              className="oc-tier-dot"
              style={{ background: tColor }}
            />
            {tier}
          </div>

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
            <div className="oc-hero-dossier-top">
              <h2 className="oc-hero-dossier-title">
                {displayName.toUpperCase()}
              </h2>
              <span
                className="oc-badge-tier"
                style={{ color: tColor, borderColor: tColor }}
              >
                {tier}
              </span>
            </div>
            <hr className="oc-hero-rule" />
            <p className="oc-hero-narrative">{narrative}</p>
            <CategoryChips
              categories={cats}
              onNavigate={(id) => scrollToAnchor(id)}
            />
          </section>

          <Timeline claims={tlines} />
          <ClaimsAccordion categories={cats} />
          <GapAnalysis gaps={data.gap_analysis} />
          <StaffNetwork staff={data.staff_network} />
          <InfluenceGraphSections dossier={data} />
          <PatternAlerts alerts={data.pattern_alerts} />
          <StoryAngles dossier={data} />
          <InvestigationReceipt dossier={data} />

          <p className="oc-footer-disclaimer" style={{ marginTop: "2rem" }}>
            {data.disclaimer ||
              "These findings document public records only. They do not prove causation or wrongdoing."}
          </p>
        </main>
      </div>

      <BottomBar variant="senator" onShareReceipt={shareReceipt} />
    </div>
  );
}
