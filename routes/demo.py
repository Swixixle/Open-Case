"""
Public one-click demo: batch investigations for a fixed senator cohort.

Enable with OPEN_CASE_PUBLIC_DEMO=1. Server must still hold adapter keys (e.g. FEC,
Congress) in environment — end users do not pass keys unless using optional power-user
overrides for supported adapters.
"""

from __future__ import annotations

import csv
import html as html_module
import io
import json
import os
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from core.demo_credentials import demo_api_key_overrides
from database import get_db
from models import CaseContributor, CaseFile, Investigator, SubjectProfile
from payloads import apply_case_file_signature
from routes.investigate import InvestigateRequest, execute_investigation_for_case
from routes.reporting import _collect_report_payload, get_base_url
from scoring import add_credibility

router = APIRouter(prefix="/api/v1", tags=["demo"])

# Cohort: diverse parties/regions; bioguides are stable Congress.gov identifiers.
DEMO_COHORT: list[dict[str, str]] = [
    {"id": "warren", "name": "Elizabeth Warren", "state": "MA", "party": "D", "bioguide_id": "W000817"},
    {"id": "rubio", "name": "Marco Rubio", "state": "FL", "party": "R", "bioguide_id": "R000595"},
    {"id": "tester", "name": "Jon Tester", "state": "MT", "party": "D", "bioguide_id": "T000464"},
    {"id": "collins", "name": "Susan Collins", "state": "ME", "party": "R", "bioguide_id": "C001035"},
    {"id": "sanders", "name": "Bernie Sanders", "state": "VT", "party": "I", "bioguide_id": "S000033"},
    {"id": "warnock", "name": "Raphael Warnock", "state": "GA", "party": "D", "bioguide_id": "W000790"},
    {"id": "hawley", "name": "Josh Hawley", "state": "MO", "party": "R", "bioguide_id": "H001079"},
]


def _public_demo_enabled() -> bool:
    v = os.getenv("OPEN_CASE_PUBLIC_DEMO", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _demo_investigator_handle() -> str:
    return (os.getenv("OPEN_CASE_DEMO_INVESTIGATOR_HANDLE", "demo_public").strip() or "demo_public")


def _require_demo() -> None:
    if not _public_demo_enabled():
        raise HTTPException(status_code=404, detail="Not found")


def _unique_demo_slug(db: Session, fig: dict[str, str]) -> str:
    base = f"open-case-public-demo-{fig['id']}"
    slug = base
    while db.scalar(select(CaseFile.id).where(CaseFile.slug == slug)):
        slug = f"{base}-{uuid.uuid4().hex[:6]}"
    return slug


def _find_demo_case(db: Session, fig_id: str, handle: str) -> CaseFile | None:
    prefix = f"open-case-public-demo-{fig_id}"
    return db.scalars(
        select(CaseFile)
        .where(
            CaseFile.created_by == handle,
            CaseFile.slug.startswith(prefix),
        )
        .order_by(CaseFile.created_at.desc())
        .limit(1)
    ).first()


def _ensure_demo_investigator(db: Session, handle: str) -> None:
    row = db.scalar(select(Investigator).where(Investigator.handle == handle))
    if row:
        return
    db.add(Investigator(handle=handle, public_key=""))
    db.flush()


def _get_or_create_demo_case(db: Session, fig: dict[str, str], handle: str) -> CaseFile:
    case = _find_demo_case(db, fig["id"], handle)
    if case:
        return case

    slug = _unique_demo_slug(db, fig)
    case = CaseFile(
        slug=slug,
        title=f"Public demo: {fig['name']}",
        subject_name=fig["name"],
        subject_type="senator",
        jurisdiction="United States",
        government_level="federal",
        branch="legislative",
        status="open",
        created_by=handle,
        summary="Seeded by Open Case public demo cohort (receipts, not verdicts).",
        is_public=True,
    )
    db.add(case)
    db.flush()
    _ensure_demo_investigator(db, handle)
    db.add(
        CaseContributor(
            case_file_id=case.id,
            investigator_handle=handle,
            role="originator",
        )
    )
    db.add(
        SubjectProfile(
            case_file_id=case.id,
            subject_name=fig["name"],
            subject_type="senator",
            government_level="federal",
            branch="legislative",
            bioguide_id=fig["bioguide_id"].strip(),
            updated_by=handle,
        )
    )
    apply_case_file_signature(case, [], db=db)
    add_credibility(db, handle, 2, "opened demo case")
    db.commit()
    db.refresh(case)
    return case


def _case_receipt_hashes(case: CaseFile) -> list[str]:
    packed = (case.signed_hash or "").strip()
    if not packed:
        return []
    try:
        data = json.loads(packed)
        ch = data.get("content_hash")
        if ch:
            return [f"sha256:{ch}"]
    except json.JSONDecodeError:
        return ["invalid_signed_hash_json"]
    return []


def _claims_from_report(report: dict[str, Any]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for pa in (report.get("pattern_alerts") or [])[:8]:
        rid = pa.get("rule_id") or pa.get("rule") or "pattern"
        headline = (
            pa.get("headline")
            or pa.get("title")
            or pa.get("summary")
            or f"Pattern alert: {rid}"
        )
        label = pa.get("epistemic_level") or "REPORTED"
        srcs: list[str] = []
        for key in ("source_url", "url", "primary_url"):
            u = pa.get(key)
            if isinstance(u, str) and u.startswith("http"):
                srcs.append(u)
        claims.append(
            {
                "text": str(headline),
                "label": str(label),
                "rule_id": str(rid),
                "sources": srcs,
            }
        )
    for s in (report.get("top_leads") or [])[:6]:
        desc = s.get("description") or s.get("summary") or ""
        if not desc:
            continue
        claims.append(
            {
                "text": str(desc)[:2000],
                "label": str(s.get("epistemic_level") or "REPORTED"),
                "rule_id": str(s.get("signal_type") or "signal"),
                "sources": [],
            }
        )
    if not claims:
        totals = (report.get("totals") or {}).copy()
        claims.append(
            {
                "text": (
                    f"Investigation produced {totals.get('evidence_entries', 0)} evidence rows "
                    f"and {totals.get('signals_detected', 0)} signals — see full report."
                ),
                "label": "CONTEXTUAL",
                "rule_id": "DEMO_SUMMARY",
                "sources": [],
            }
        )
    return claims


def _sources_by_type_from_report(report: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for row in report.get("sources_checked") or []:
        name = str(row.get("source") or "unknown")
        q = str(row.get("query") or "")
        line = f"{name}: {q}".strip(": ")
        out.setdefault(name, []).append(line[:500])
    return out


class DemoInvestigationRequest(BaseModel):
    cohort: list[str] | None = Field(
        None,
        description="Subset of demo figure ids (e.g. warren). Default: full DEMO_COHORT.",
    )
    custom_api_keys: dict[str, str] | None = Field(
        None,
        description="Optional overrides for CredentialRegistry adapter ids (e.g. fec, congress).",
    )
    max_figures: int | None = Field(
        None,
        ge=1,
        le=7,
        description="Cap cohort size for smoke tests (default: all selected).",
    )


class FigureInvestigationResult(BaseModel):
    figure_id: str
    name: str
    state: str
    party: str
    bioguide_id: str
    case_id: str | None = None
    share_report_url: str | None = None
    claims: list[dict[str, Any]] = Field(default_factory=list)
    receipt_hashes: list[str] = Field(default_factory=list)
    totals: dict[str, Any] | None = None
    error: str | None = None


class ComparativeReport(BaseModel):
    generated_at: str
    cohort_summary: dict[str, Any]
    figures: list[FigureInvestigationResult]
    export_text_plain: str | None = None
    export_text_markdown: str | None = None
    export_html_card: str | None = None
    export_json: str | None = None
    export_csv: str | None = None
    philosophy_note: str = "Receipts, not verdicts. We document proximity and timing in public records."


class FigureDetailResponse(BaseModel):
    figure_id: str
    name: str
    state: str
    party: str
    bioguide_id: str
    case_id: str | None = None
    share_report_url: str | None = None
    claims: list[dict[str, Any]]
    receipt_hashes: list[str]
    sources_by_type: dict[str, list[str]]
    totals: dict[str, Any] | None = None


def _generate_plain_text(report: ComparativeReport) -> str:
    lines = [
        "Open Case – Comparative Report (Public Demo)",
        "We do not take sides; we show receipts.",
        "",
        report.philosophy_note,
        "",
        f"Generated: {report.generated_at}",
        f"Cohort size: {report.cohort_summary.get('cohort_size')}",
        f"Party breakdown: {report.cohort_summary.get('party_breakdown')}",
        "",
        "Top patterns (rule hits across cohort):",
    ]
    for p in report.cohort_summary.get("top_patterns") or []:
        lines.append(f"- {p}")
    lines.append("")
    for fig in report.figures:
        lines.append(f"--- {fig.name} ({fig.party}, {fig.state}) ---")
        if fig.error:
            lines.append(f"Error: {fig.error}")
            continue
        if fig.share_report_url:
            lines.append(f"Report: {fig.share_report_url}")
        for c in fig.claims:
            lines.append(f"[{c.get('label')}] {c.get('text')}")
            lines.append(f"  Rule: {c.get('rule_id')} | Sources: {', '.join(c.get('sources') or [])}")
    return "\n".join(lines)


def _generate_markdown(report: ComparativeReport) -> str:
    lines = [
        "# Open Case – Comparative Report (Public Demo)",
        "",
        "> We do not take sides; we show receipts.",
        "",
        report.philosophy_note,
        "",
        f"**Generated:** {report.generated_at}",
        f"**Cohort size:** {report.cohort_summary.get('cohort_size')}",
        f"**Party breakdown:** `{report.cohort_summary.get('party_breakdown')}`",
        "",
        "## Top patterns",
    ]
    for p in report.cohort_summary.get("top_patterns") or []:
        lines.append(f"- {p}")
    lines.append("")
    for fig in report.figures:
        lines.append(f"## {fig.name} ({fig.party}, {fig.state})")
        if fig.error:
            lines.append(f"**Error:** {fig.error}")
            lines.append("")
            continue
        if fig.share_report_url:
            lines.append(f"[Full report]({fig.share_report_url})")
            lines.append("")
        for c in fig.claims:
            lines.append(f"- **[{c.get('label')}]** {c.get('text')}")
            lines.append(f"  - Rule: `{c.get('rule_id')}`")
            lines.append(f"  - Sources: {', '.join(c.get('sources') or [])}")
        lines.append("")
    return "\n".join(lines)


def _generate_html_card(report: ComparativeReport) -> str:
    def esc(s: Any) -> str:
        return html_module.escape(str(s), quote=True)

    html = [
        '<article class="open-case-demo-report">',
        "<h2>Open Case – Comparative Report (Public Demo)</h2>",
        "<p><em>We do not take sides; we show receipts.</em></p>",
        f"<p>{esc(report.philosophy_note)}</p>",
        f"<p>Generated: {esc(report.generated_at)}<br>",
        f"Cohort size: {esc(report.cohort_summary.get('cohort_size'))}<br>",
        f"Party breakdown: {esc(report.cohort_summary.get('party_breakdown'))}</p>",
        "<h3>Top patterns</h3><ul>",
    ]
    for p in report.cohort_summary.get("top_patterns") or []:
        html.append(f"<li>{esc(p)}</li>")
    html.append("</ul>")
    for fig in report.figures:
        html.append(f"<h4>{esc(fig.name)} ({esc(fig.party)}, {esc(fig.state)})</h4>")
        if fig.error:
            html.append(f"<p><strong>Error:</strong> {esc(fig.error)}</p>")
            continue
        if fig.share_report_url:
            url = esc(fig.share_report_url)
            html.append(f'<p><a href="{url}">Full signed report</a></p>')
        html.append("<ul>")
        for c in fig.claims:
            html.append(
                "<li><strong>["
                + esc(c.get("label"))
                + "]</strong> "
                + esc(c.get("text"))
                + " — <code>"
                + esc(c.get("rule_id"))
                + "</code></li>"
            )
        html.append("</ul>")
    html.append("</article>")
    return "\n".join(html)


def _generate_json(report: ComparativeReport) -> str:
    data = report.model_dump()
    return json.dumps(data, indent=2, default=str)


def _generate_csv(report: ComparativeReport) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "figure_id",
            "name",
            "state",
            "party",
            "case_id",
            "claim_text",
            "label",
            "rule_id",
            "sources",
            "receipt_hashes",
            "error",
        ]
    )
    for fig in report.figures:
        if not fig.claims:
            writer.writerow(
                [
                    fig.figure_id,
                    fig.name,
                    fig.state,
                    fig.party,
                    fig.case_id or "",
                    "",
                    "",
                    "",
                    "",
                    "; ".join(fig.receipt_hashes),
                    fig.error or "",
                ]
            )
            continue
        for c in fig.claims:
            writer.writerow(
                [
                    fig.figure_id,
                    fig.name,
                    fig.state,
                    fig.party,
                    fig.case_id or "",
                    c.get("text", ""),
                    c.get("label", ""),
                    c.get("rule_id", ""),
                    "; ".join(c.get("sources") or []),
                    "; ".join(fig.receipt_hashes),
                    fig.error or "",
                ]
            )
    return output.getvalue()


@router.post("/demo/investigate", response_model=ComparativeReport)
async def run_demo_investigation(
    req: DemoInvestigationRequest,
    db: Session = Depends(get_db),
) -> ComparativeReport:
    _require_demo()
    handle = _demo_investigator_handle()
    _ensure_demo_investigator(db, handle)

    if req.cohort:
        id_set = {x.strip().lower() for x in req.cohort if x and str(x).strip()}
        cohort = [f for f in DEMO_COHORT if f["id"] in id_set]
        if not cohort:
            raise HTTPException(status_code=400, detail="No matching cohort ids")
    else:
        cohort = list(DEMO_COHORT)

    max_n = req.max_figures
    if max_n is not None:
        cohort = cohort[: int(max_n)]

    results: list[FigureInvestigationResult] = []
    rule_hits: Counter[str] = Counter()

    with demo_api_key_overrides(req.custom_api_keys):
        for fig in cohort:
            case = _get_or_create_demo_case(db, fig, handle)
            inv = InvestigateRequest(
                subject_name=fig["name"],
                investigator_handle=handle,
                bioguide_id=fig["bioguide_id"].strip(),
                proximity_days=90,
                fec_committee_id=None,
            )
            try:
                raw = await execute_investigation_for_case(
                    db,
                    case.id,
                    inv,
                    None,
                    include_unresolved=False,
                    debug=False,
                )
            except HTTPException as he:
                det = he.detail
                if not isinstance(det, str):
                    det = json.dumps(det, default=str)
                results.append(
                    FigureInvestigationResult(
                        figure_id=fig["id"],
                        name=fig["name"],
                        state=fig["state"],
                        party=fig["party"],
                        bioguide_id=fig["bioguide_id"],
                        case_id=str(case.id),
                        error=str(det),
                    )
                )
                continue
            except Exception as e:
                results.append(
                    FigureInvestigationResult(
                        figure_id=fig["id"],
                        name=fig["name"],
                        state=fig["state"],
                        party=fig["party"],
                        bioguide_id=fig["bioguide_id"],
                        case_id=str(case.id),
                        error=str(e),
                    )
                )
                continue

            if isinstance(raw, JSONResponse):
                try:
                    body = json.loads(raw.body.decode())
                except Exception:
                    body = {"detail": raw.body.decode()[:500]}
                err = body.get("detail")
                if isinstance(err, list):
                    err = json.dumps(err)
                results.append(
                    FigureInvestigationResult(
                        figure_id=fig["id"],
                        name=fig["name"],
                        state=fig["state"],
                        party=fig["party"],
                        bioguide_id=fig["bioguide_id"],
                        case_id=str(case.id),
                        error=str(err or "investigation_failed"),
                    )
                )
                continue

            case = db.scalar(select(CaseFile).where(CaseFile.id == case.id))
            if not case:
                results.append(
                    FigureInvestigationResult(
                        figure_id=fig["id"],
                        name=fig["name"],
                        state=fig["state"],
                        party=fig["party"],
                        bioguide_id=fig["bioguide_id"],
                        error="case_missing_after_run",
                    )
                )
                continue

            report = _collect_report_payload(
                case.id,
                db,
                bump_view=False,
                include_unreviewed=False,
                section=None,
            )
            for pa in report.get("pattern_alerts") or []:
                rid = pa.get("rule_id") or pa.get("rule")
                if rid:
                    rule_hits[str(rid)] += 1

            base = get_base_url().rstrip("/")
            share = f"{base}/api/v1/cases/{case.id}/report/view"
            results.append(
                FigureInvestigationResult(
                    figure_id=fig["id"],
                    name=fig["name"],
                    state=fig["state"],
                    party=fig["party"],
                    bioguide_id=fig["bioguide_id"],
                    case_id=str(case.id),
                    share_report_url=share,
                    claims=_claims_from_report(report),
                    receipt_hashes=_case_receipt_hashes(case),
                    totals=report.get("totals"),
                )
            )

    party_breakdown: dict[str, int] = {}
    for r in results:
        party_breakdown[r.party] = party_breakdown.get(r.party, 0) + 1

    top_patterns = [f"{rid} ({n} figures)" for rid, n in rule_hits.most_common(8)]
    if not top_patterns:
        top_patterns = [
            "Run completed — open individual reports for timing and proximity signals.",
        ]

    cohort_summary = {
        "cohort_size": len(results),
        "party_breakdown": party_breakdown,
        "top_patterns": top_patterns,
        "pattern_rule_counts": dict(rule_hits),
    }

    out = ComparativeReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        cohort_summary=cohort_summary,
        figures=results,
    )
    out.export_text_plain = _generate_plain_text(out)
    out.export_text_markdown = _generate_markdown(out)
    out.export_html_card = _generate_html_card(out)
    out.export_json = _generate_json(out)
    out.export_csv = _generate_csv(out)
    return out


@router.get("/demo/figure/{figure_id}", response_model=FigureDetailResponse)
def get_figure_investigation(
    figure_id: str,
    db: Session = Depends(get_db),
) -> FigureDetailResponse:
    _require_demo()
    fig = next((f for f in DEMO_COHORT if f["id"] == figure_id.strip().lower()), None)
    if not fig:
        raise HTTPException(status_code=404, detail="Figure not in demo cohort")

    handle = _demo_investigator_handle()
    case = _find_demo_case(db, fig["id"], handle)
    if not case:
        raise HTTPException(
            status_code=404,
            detail="No investigation yet — run POST /api/v1/demo/investigate first.",
        )

    report = _collect_report_payload(
        case.id,
        db,
        bump_view=False,
        include_unreviewed=False,
        section=None,
    )
    base = get_base_url().rstrip("/")
    share = f"{base}/api/v1/cases/{case.id}/report/view"
    return FigureDetailResponse(
        figure_id=fig["id"],
        name=fig["name"],
        state=fig["state"],
        party=fig["party"],
        bioguide_id=fig["bioguide_id"],
        case_id=str(case.id),
        share_report_url=share,
        claims=_claims_from_report(report),
        receipt_hashes=_case_receipt_hashes(case),
        sources_by_type=_sources_by_type_from_report(report),
        totals=report.get("totals"),
    )


@router.get("/demo/cohort")
def get_demo_cohort() -> dict[str, Any]:
    _require_demo()
    return {
        "philosophy_note": "Receipts, not verdicts.",
        "figures": DEMO_COHORT,
        "post_path": "/api/v1/demo/investigate",
    }
