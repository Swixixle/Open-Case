import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import BottomBar from "../components/BottomBar.jsx";
import { apiUrl } from "../lib/api.js";
import { formatDisplayDate } from "../lib/dossierParse.js";

export default function VerifyPage() {
  const { dossier_id } = useParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function run() {
      setLoading(true);
      setError(null);
      try {
        const r = await fetch(
          apiUrl(`/api/v1/dossiers/${encodeURIComponent(dossier_id)}/public`)
        );
        const j = await r.json().catch(() => ({}));
        if (cancelled) return;
        if (!r.ok) {
          setError(
            typeof j?.detail === "string"
              ? j.detail
              : "Receipt not found or not yet available."
          );
          setData(null);
          return;
        }
        setData(j);
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
  }, [dossier_id]);

  const sub = data?.subject || {};
  const name = sub.name || "—";
  const generated = formatDisplayDate(
    data?.generated_at || data?.completed_at || ""
  );
  const sig =
    typeof data?.signature === "string" && !data.signature.startsWith("{")
      ? data.signature
      : "";
  const sigLine = sig ? `ed25519:${sig.slice(0, 28)}…` : "—";
  const hasSig = Boolean(sig && data?.content_hash);

  if (loading) {
    return (
      <div className="oc-verify">
        <p className="oc-header-brand">OPEN CASE</p>
        <p className="oc-empty-note">Loading verification…</p>
        <BottomBar variant="senator" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="oc-verify">
        <p className="oc-header-brand">OPEN CASE — RECEIPT VERIFICATION</p>
        <div className="oc-error-panel">
          <p>{error}</p>
          <Link to="/">← Home</Link>
        </div>
        <BottomBar variant="senator" />
      </div>
    );
  }

  const bgId = sub.bioguide_id || "";

  return (
    <div className="oc-verify">
      <p className="oc-header-brand">OPEN CASE — RECEIPT VERIFICATION</p>

      <div
        className={`oc-verify-badge ${hasSig ? "oc-verify-badge--ok" : "oc-verify-badge--bad"}`}
      >
        {hasSig
          ? "SIGNED RECEIPT (FIELDS PRESENT)"
          : "MISSING SIGNATURE / HASH"}
      </div>

      <div className="oc-verify-block">
        <strong>Subject:</strong> {name}
        <br />
        <strong>Generated:</strong> {generated}
        <br />
        <strong>Verified (client view):</strong>{" "}
        {new Date().toLocaleString()}
        <br />
        <strong>Signature:</strong>{" "}
        <span className="oc-verify-sig">{sigLine}</span>
        <br />
        <strong>Content hash:</strong>{" "}
        <span className="oc-verify-sig">
          {(data.content_hash || "—").toString().slice(0, 48)}
          …
        </span>
      </div>

      <p className="oc-verify-block">
        This receipt was cryptographically signed by Open Case / Nikodemus
        Systems. The signature confirms this dossier payload matches the
        published content hash when verified with the service public key.
        Independent verification is encouraged.
      </p>

      {bgId ? (
        <p>
          <Link to={`/senator/${bgId}`}>VIEW FULL DOSSIER →</Link>
        </p>
      ) : (
        <p>
          <Link to="/">← DIRECTORY</Link>
        </p>
      )}

      <BottomBar variant="senator" />
    </div>
  );
}
