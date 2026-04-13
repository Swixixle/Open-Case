export default function LoadingScreen({ senatorName = "", stateLine = "" }) {
  return (
    <div className="oc-loading">
      <p className="oc-loading-brand">OPEN CASE</p>
      <h1 className="oc-loading-name">{senatorName.toUpperCase()}</h1>
      <div className="oc-loading-dots" aria-hidden>
        <span />
        <span />
        <span />
      </div>
      <p className="oc-loading-sub">Retrieving public records...</p>
      {stateLine ? <p className="oc-loading-field">{stateLine}</p> : null}
      <div className="oc-loading-rule" />
      <p className="oc-loading-disclaimer oc-mono">
        RECORDS ARE AI-ASSISTED AND SOURCED FROM
        <br />
        PUBLIC DATABASES. ALL FINDINGS REQUIRE
        <br />
        INDEPENDENT VERIFICATION.
      </p>
    </div>
  );
}
