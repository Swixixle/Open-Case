import { useCallback, useEffect, useState } from "react";
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

export default function SenatorPage() {
  const { bioguide_id } = useParams();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [building, setBuilding] = useState(false);

  const dirMeta = DIRECTORY_SENATORS.find(
    (s) => s.bioguide_id === bioguide_id
  );
  const displayName =
    data?.subject?.name || dirMeta?.name || bioguide_id || "Senator";

  const fetchDossier = useCallback(async () => {
    const r = await fetch(
      apiUrl(
        `/api/v1/senators/${encodeURIComponent(bioguide_id || "")}/dossier`
      ),
      { headers: apiHeaders() }
    );
    const j = await r.json().catch(() => ({}));
    return { ok: r.ok, status: r.status, body: j };
  }, [bioguide_id]);

  useEffect(() => {
    let cancelled = false;

    async function run() {
      setLoading(true);
      setError(null);
      try {
        const { ok, status, body } = await fetchDossier();
        if (cancelled) return;
        if (status === 401 || status === 403) {
          setError("API key missing or invalid. Set VITE_OPEN_CASE_API_KEY.");
          setData(null);
          setBuilding(false);
          return;
        }
        if (body?.status === "building") {
          setBuilding(true);
          setData(null);
          return;
        }
        setBuilding(false);
        if (!ok) {
          setError(
            typeof body?.detail === "string"
              ? body.detail
              : body?.detail?.[0]?.msg || "Could not load dossier."
          );
          setData(null);
          return;
        }
        setData(body);
      } catch (e) {
        if (!cancelled) setError(e.message || String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    run();
    return () => {
      cancelled = true;
    };
  }, [fetchDossier]);

  useEffect(() => {
    if (!building) return undefined;
    const id = setInterval(async () => {
      try {
        const { ok, body } = await fetchDossier();
        if (body?.status === "building") return;
        if (ok && body?.status === "completed") {
          setData(body);
          setBuilding(false);
        } else if (ok) {
          setData(body);
          setBuilding(false);
        }
      } catch {
        /* ignore */
      }
    }, 4000);
    return () => clearInterval(id);
  }, [building, fetchDossier]);

  const shareReceipt = () => {
    const basePath = import.meta.env.BASE_URL.replace(/\/$/, "");
    const id = data?.dossier_id;
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

  if (loading && !building) {
    return (
      <>
        <LoadingScreen
          senatorName={displayName}
          stateLine={
            dirMeta
              ? `FIELD REPORT · ${dirMeta.state}`
              : "FIELD REPORT · US SENATE"
          }
        />
        <BottomBar variant="senator" onShareReceipt={shareReceipt} />
      </>
    );
  }

  if (building) {
    return (
      <>
        <LoadingScreen
          senatorName={displayName}
          stateLine="DOSSIER BUILD IN PROGRESS — POLLING…"
        />
        <BottomBar variant="senator" />
      </>
    );
  }

  if (error || !data) {
    return (
      <div className="oc-dossier-wrap">
        <div className="oc-error-panel">
          <h1>DOSSIER UNAVAILABLE</h1>
          <p>{error || "No data returned."}</p>
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
