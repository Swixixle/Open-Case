import { Link } from "react-router-dom";
import { apiHeaders, apiUrl } from "../lib/api.js";
import {
  categoriesWithClaims,
  countTotalFindings,
  formatDisplayDate,
} from "../lib/dossierParse.js";

export default function InvestigationReceipt({ dossier }) {
  if (!dossier || dossier.status !== "completed") return null;

  const sub = dossier.subject || {};
  const name = sub.name || dossier.senator_name || "Subject";
  const party = sub.party || "";
  const state = sub.state || "";
  const subjLine = `${name}${party || state ? ` (${party}-${state})` : ""}`;
  const cats = dossier.deep_research?.categories || {};
  const nCat = categoriesWithClaims(cats).length;
  const nFind = countTotalFindings(cats);
  const when = formatDisplayDate(
    dossier.completed_at || dossier.generated_at || ""
  );
  const ver = dossier.version ?? "—";
  const sigB64 =
    typeof dossier.signature === "string" && !dossier.signature.startsWith("{")
      ? dossier.signature
      : "";
  let sigShort = "—";
  if (sigB64) {
    sigShort = `ed25519:${sigB64.slice(0, 20)}…`;
  } else if (dossier.content_hash) {
    sigShort = `sha256:${String(dossier.content_hash).slice(0, 16)}…`;
  }

  const dossierId = dossier.dossier_id;
  const basePath = import.meta.env.BASE_URL.replace(/\/$/, "");
  const verifyPath = `${basePath}/verify/${dossierId}`;
  const verifyAbs =
    typeof window !== "undefined"
      ? `${window.location.origin}${verifyPath}`
      : verifyPath;

  const shareReceipt = async () => {
    const title = `Open Case — ${name}`;
    const text = `Investigation receipt: ${name}. Verify: ${verifyAbs}`;
    try {
      if (navigator.share) {
        await navigator.share({ title, text, url: verifyAbs });
        return;
      }
    } catch {
      /* fall through */
    }
    try {
      await navigator.clipboard.writeText(`${title}\n${verifyAbs}`);
    } catch {
      /* ignore */
    }
  };

  const downloadJson = () => {
    const blob = new Blob([JSON.stringify(dossier, null, 2)], {
      type: "application/json",
    });
    const u = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = u;
    a.download = `dossier-${dossierId}.json`;
    a.click();
    URL.revokeObjectURL(u);
  };

  const downloadPdf = async () => {
    try {
      const r = await fetch(apiUrl(`/api/v1/dossiers/${dossierId}/pdf`), {
        headers: apiHeaders(),
      });
      if (!r.ok) throw new Error("PDF request failed");
      const blob = await r.blob();
      const u = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = u;
      a.download = `dossier-${dossierId}.pdf`;
      a.click();
      URL.revokeObjectURL(u);
    } catch {
      window.open(apiUrl(`/api/v1/dossiers/${dossierId}/pdf`), "_blank");
    }
  };

  return (
    <div className="oc-receipt" id="receipt">
      <p className="oc-receipt-title">OPEN CASE INVESTIGATION RECEIPT</p>
      <hr className="oc-receipt-rule" />
      <div className="oc-receipt-row">
        <span>Subject:</span>
        <span>{subjLine}</span>
      </div>
      <div className="oc-receipt-row">
        <span>Generated:</span>
        <span>{when}</span>
      </div>
      <div className="oc-receipt-row">
        <span>Categories:</span>
        <span>{nCat} researched</span>
      </div>
      <div className="oc-receipt-row">
        <span>Findings:</span>
        <span>{nFind} documented</span>
      </div>
      <div className="oc-receipt-row">
        <span>Version:</span>
        <span>{ver}</span>
      </div>
      <div className="oc-receipt-row">
        <span>Signature:</span>
        <span>{sigShort}</span>
      </div>
      <hr className="oc-receipt-rule" />
      <div className="oc-receipt-actions">
        <Link to={`/verify/${dossierId}`}>VERIFY →</Link>
        <button type="button" onClick={shareReceipt}>
          SHARE ↑
        </button>
        <button type="button" onClick={downloadJson}>
          DOWNLOAD JSON
        </button>
        <button type="button" onClick={downloadPdf}>
          DOWNLOAD PDF
        </button>
      </div>
    </div>
  );
}
