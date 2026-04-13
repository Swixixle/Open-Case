import { Link, useParams } from "react-router-dom";

export default function SenatorPage() {
  const { bioguide_id } = useParams();
  return (
    <div className="oc-home" style={{ padding: "2rem" }}>
      <p className="oc-header-brand">OPEN CASE</p>
      <p style={{ color: "var(--text-muted)" }}>
        Dossier view for <span className="oc-mono">{bioguide_id}</span> — full
        layout next.
      </p>
      <p>
        <Link to="/">← Directory</Link>
      </p>
    </div>
  );
}
