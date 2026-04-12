"""HTML → PDF for senator dossiers (pdfkit)."""
from __future__ import annotations

import html
import json
import os
from typing import Any
from uuid import UUID

from services.gap_analysis import GAP_ANALYSIS_DISCLAIMER
from services.senator_dossier import DOSSIER_DISCLAIMER


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def dossier_dict_from_stored_json(raw: str) -> dict[str, Any]:
    data = json.loads(raw or "{}")
    return data if isinstance(data, dict) else {}


def render_dossier_pdf_html(dossier: dict[str, Any], *, dossier_id: UUID) -> str:
    subject = dossier.get("subject") if isinstance(dossier.get("subject"), dict) else {}
    name = _esc(str(subject.get("name") or ""))
    state = _esc(str(subject.get("state") or ""))
    party = _esc(str(subject.get("party") or ""))
    committees = subject.get("committees") or []
    if not isinstance(committees, list):
        committees = []
    comm = _esc(", ".join(str(c) for c in committees if c))
    years = subject.get("years_in_office")
    gen = _esc(str(dossier.get("generated_at") or ""))
    sig = _esc(str(dossier.get("signature") or "")[:48])

    base = (os.environ.get("BASE_URL") or "https://open-case.onrender.com").rstrip("/")
    verify_href = _esc(f"{base}/api/v1/dossiers/{dossier_id}/public")

    gap_lines: list[str] = []
    for g in dossier.get("gap_analysis") or []:
        if not isinstance(g, dict):
            continue
        sentence = str(g.get("sentence") or "")
        sources = g.get("sources") or []
        src = ""
        if isinstance(sources, list) and sources:
            src = " — " + "; ".join(str(u) for u in sources[:3] if u)
        gap_lines.append(f"<p>{_esc(sentence)}{_esc(src)}</p>")

    dr = dossier.get("deep_research") or {}
    categories = dr.get("categories") if isinstance(dr, dict) else {}
    if not isinstance(categories, dict):
        categories = {}
    dr_blocks: list[str] = []
    for cat, block in categories.items():
        if not isinstance(block, dict):
            continue
        nar = str(block.get("narrative") or "").strip()
        if not nar:
            continue
        dr_blocks.append(f"<h3>{_esc(str(cat))}</h3><p>{_esc(nar)}</p>")

    staff_rows: list[str] = []
    for s in dossier.get("staff_network") or []:
        if not isinstance(s, dict):
            continue
        overlap = "yes" if s.get("donor_overlap") else "no"
        clients = s.get("lobbying_clients") or []
        cl = ", ".join(str(x) for x in clients[:5]) if isinstance(clients, list) else ""
        staff_rows.append(
            "<tr>"
            f"<td>{_esc(str(s.get('name') or ''))}</td>"
            f"<td>{_esc(str(s.get('role_at_office') or ''))}</td>"
            f"<td>{_esc(overlap)}</td>"
            f"<td>{_esc(cl)}</td>"
            "</tr>"
        )

    pal_lines: list[str] = []
    for p in dossier.get("pattern_alerts") or []:
        if not isinstance(p, dict):
            continue
        pal_lines.append(
            "<li>"
            f"<strong>{_esc(str(p.get('rule_id') or ''))}</strong>: "
            f"{_esc(str(p.get('donor_entity') or ''))} — "
            f"{_esc(str(p.get('disclaimer') or '')[:400])}"
            "</li>"
        )

    stock_rows: list[str] = []
    for st in dossier.get("stock_trade_proximity") or []:
        if not isinstance(st, dict):
            continue
        stock_rows.append(
            "<tr>"
            f"<td>{_esc(str(st.get('trade_date') or ''))}</td>"
            f"<td>{_esc(str(st.get('ticker') or ''))}</td>"
            f"<td>{_esc(str(st.get('company_name') or ''))}</td>"
            f"<td>{_esc(str(st.get('nearest_hearing_date') or ''))}</td>"
            f"<td>{_esc(str(st.get('days_between') or ''))}</td>"
            "</tr>"
        )

    afp = dossier.get("amendment_fingerprint")
    afp_html = ""
    if isinstance(afp, dict):
        afp_html = (
            f"<p><strong>Total amendment votes:</strong> {_esc(str(afp.get('total_amendment_votes')))} — "
            f"<strong>Alignment rate:</strong> {_esc(str(afp.get('alignment_rate')))} — "
            f"<strong>Enforcement-related (Yea):</strong> "
            f"{_esc(str(afp.get('enforcement_stripping_count')))}</p>"
            f"<p><em>{_esc(str(afp.get('disclaimer') or ''))}</em></p>"
        )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/><style>
body {{ font-family: Helvetica, Arial, sans-serif; margin: 24px; color: #1a1a1a; }}
header {{ background: #001a4d; color: #fff; padding: 16px 20px; margin: -24px -24px 24px -24px; }}
h1 {{ margin: 0; font-size: 20px; letter-spacing: 0.04em; }}
h2 {{ color: #001a4d; border-bottom: 2px solid #001a4d; padding-bottom: 4px; margin-top: 28px; }}
.meta {{ margin: 12px 0; font-size: 13px; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 12px; }}
th, td {{ border: 1px solid #ccc; padding: 6px 8px; text-align: left; }}
th {{ background: #f0f4fa; }}
footer {{ margin-top: 36px; font-size: 11px; color: #444; border-top: 1px solid #ccc; padding-top: 12px; }}
</style></head><body>
<header><h1>OPEN CASE INVESTIGATION DOSSIER</h1></header>
<div class="meta">
  <strong>Subject:</strong> {name} — {state} ({party})<br/>
  <strong>Committees:</strong> {comm}<br/>
  <strong>Years in office (approx.):</strong> {_esc(str(years))}<br/>
  <strong>Generated:</strong> {gen}<br/>
  <strong>Signature (truncated):</strong> {sig}
</div>
<h2>Gap Analysis</h2>
{"".join(gap_lines) if gap_lines else "<p>No gap sentences generated.</p>"}
<p><em>{_esc(GAP_ANALYSIS_DISCLAIMER)}</em></p>
<h2>Deep Research</h2>
{"".join(dr_blocks) if dr_blocks else "<p>No narratives returned.</p>"}
<h2>Staff Network</h2>
<table>
  <tr><th>Name</th><th>Role</th><th>Donor overlap</th><th>Lobbying clients (sample)</th></tr>
  {"".join(staff_rows) if staff_rows else "<tr><td colspan='4'>No staff rows.</td></tr>"}
</table>
<h2>Pattern Alerts</h2>
<ul>{"".join(pal_lines) if pal_lines else "<li>No pattern alerts for this case.</li>"}</ul>
<h2>Stock trade proximity</h2>
<table>
  <tr><th>Trade date</th><th>Ticker</th><th>Company</th><th>Nearest hearing</th><th>Days</th></tr>
  {"".join(stock_rows) if stock_rows else "<tr><td colspan='5'>No flagged stock trades.</td></tr>"}
</table>
<h2>Amendment fingerprint</h2>
{afp_html if afp_html else "<p>No amendment fingerprint data.</p>"}
<footer>
  Verify at {verify_href}<br/>
  {_esc(DOSSIER_DISCLAIMER)}
</footer>
</body></html>"""


def dossier_to_pdf_bytes(dossier: dict[str, Any], *, dossier_id: UUID) -> bytes:
    import pdfkit

    html_doc = render_dossier_pdf_html(dossier, dossier_id=dossier_id)
    return pdfkit.from_string(html_doc, False, options={"quiet": ""})
