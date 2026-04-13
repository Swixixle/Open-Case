import { Link, useParams } from "react-router-dom";

export default function VerifyPage() {
  const { dossier_id } = useParams();
  return (
    <div className="oc-home" style={{ padding: "2rem" }}>
      <p className="oc-header-brand">VERIFY</p>
      <p style={{ color: "var(--text-muted)" }}>
        Receipt <span className="oc-mono">{dossier_id}</span> — full verify UI
        next.
      </p>
      <p>
        <Link to="/">← Home</Link>
      </p>
    </div>
  );
}
