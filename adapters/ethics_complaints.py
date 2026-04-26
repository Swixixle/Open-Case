"""
OCE (Office of Congressional Conduct / legacy OCE) public reports list — HTML scraper.

List page: https://oce.house.gov/reports  (Drupal ``views-row`` list items).
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from adapters.base import AdapterResponse, AdapterResult, BaseAdapter

OCE_BASE = "https://oce.house.gov"
OCE_REPORTS_PATH = "/reports"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; OpenCase/1.0) ethics-complaints",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Skip generic quarterly / aggregate reports that rarely name one member
_SKIP_TITLE_SUBSTRS = (
    "quarter 20",
    "quarter 202",
    "quarter 201",
    "quarter 19",
    "quarterly",
    "annual report",
    "annual 20",
)


def _abs_url(href: str) -> str:
    if (href or "").strip().lower().startswith("http"):
        return href.strip()
    return urljoin(OCE_BASE, href or "")


def _text(el: Any) -> str:
    if el is None:
        return ""
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True) or "").strip()


def _parse_time_iso(s: str | None) -> str | None:
    if not s:
        return None
    t = str(s).strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", t)
    if m:
        return m.group(1)
    return None


def _classify_report(title: str, rtype: str) -> str:
    tl = (title or "").lower()
    if "dismiss" in tl:
        return "Dismissal"
    if "letter" in tl and "referral" not in tl:
        return "Letter"
    if "referral" in tl or "referred" in tl:
        return "Referral"
    if "quarter" in tl or (rtype or "").lower() == "quarterly reports":
        return "Report"
    if "investigation" in (rtype or "").lower() or "occ" in tl or "oce" in tl:
        return "Investigation"
    return "Investigation"


def _status_from_text(title: str, body: str) -> str:
    bundle = f"{title} {body}".lower()
    if "dismiss" in bundle:
        return "Dismissed"
    if "closed" in bundle:
        return "Closed"
    if "referred" in bundle or "referral" in bundle or "transmitted" in bundle:
        return "Referred"
    if "pending" in bundle or "ongoing" in bundle:
        return "Pending"
    return "Reported"


def _epistemic_for_row(title: str, body: str) -> str:
    bundle = f"{title} {body}".lower()
    if "dismiss" in bundle or "closed" in bundle or "referred" in bundle:
        return "REPORTED"
    if "alleg" in bundle or "pending" in bundle:
        return "ALLEGED"
    return "REPORTED"


def _name_match(row_text: str, query: str) -> bool:
    q = (query or "").strip().lower()
    if not q or len(q) < 2:
        return False
    bundle = row_text.lower()
    if q in bundle:
        return True
    parts = [p for p in re.split(r"[\s,]+", q) if len(p) > 1]
    if not parts:
        return False
    for p in parts:
        if p in bundle:
            return True
    if len(parts) >= 2 and parts[-1] in bundle:
        return True
    return False


def _result_hash(results: list[AdapterResult], q: str) -> str:
    h = hashlib.sha256(
        json.dumps(
            [r.raw_data for r in results if r.raw_data], default=str, sort_keys=True
        ).encode()
    ).hexdigest()[:32]
    return f"{q}|{h}"


def _pick_pdf_url(soup_fr: Any, report_url: str) -> str | None:
    for a in soup_fr.select('a[href$=".pdf"], a[href*=".pdf"]'):
        h = a.get("href")
        if h and ".pdf" in h.lower():
            return _abs_url(h)
    return None


def _parse_list_html(html: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    soup = BeautifulSoup(html, "html.parser")
    for row in soup.select("div.views-row.evo-views-row, div.views-row"):
        title_el = row.select_one("div.views-field-title a, .views-field-title a")
        if not title_el or not title_el.get("href"):
            continue
        t_href = _abs_url(str(title_el.get("href")))
        title = _text(title_el)
        if not title:
            continue
        tl = title.lower()
        if any(s in tl for s in _SKIP_TITLE_SUBSTRS) and "rep." not in tl and "regarding" not in tl:
            # Broad quarterly; keep if it clearly names a person in title
            if not re.search(r"rep\.|mr\.|ms\.|mrs\.|regarding", tl):
                continue
        time_el = row.select_one("time[datetime]")
        filed = _parse_time_iso(time_el.get("datetime") if time_el else None)
        type_a = row.select_one(
            "span.views-field-field-evo-article-type a, .views-field-field-evo-article-type a"
        )
        rtype = _text(type_a) if type_a else ""
        body_el = row.select_one("div.views-field-body .field-content, .views-field-body .field-content")
        body = _text(body_el)
        # PDF may be in body
        fr = body_el or row
        pdf = _pick_pdf_url(fr, t_href)
        primary = pdf or t_href
        out.append(
            {
                "title": title,
                "report_page_url": t_href,
                "primary_url": primary,
                "filed_date": filed,
                "category_label": rtype,
                "body": body,
            }
        )
    return out


def _to_adapter_results(
    source_name: str, rows: list[dict[str, Any]], name_query: str
) -> list[AdapterResult]:
    matched: list[dict[str, Any]] = []
    for r in rows:
        row_blob = f"{r.get('title', '')} {r.get('body', '')}"
        if not _name_match(row_blob, name_query):
            continue
        matched.append(r)
    out: list[AdapterResult] = []
    for r in matched:
        title = str(r.get("title") or "")
        body_txt = str(r.get("body") or "")
        rtype = str(r.get("category_label") or "")
        itype = _classify_report(title, rtype)
        st = _status_from_text(title, body_txt)
        epi = _epistemic_for_row(title, body_txt)
        issued = r.get("filed_date")
        purl = str(r.get("primary_url") or r.get("report_page_url") or "")
        if not purl.startswith("http"):
            purl = OCE_BASE + OCE_REPORTS_PATH
        ev_title = f"OCE Investigation: {title[:180]}{'…' if len(title) > 180 else ''}"
        body_out = f"{(body_txt or title)[:500]}{'…' if len(body_txt) > 500 else ''} Category: {rtype}. Status: {st}."
        disp = body_txt[:4000] if body_txt and len(body_txt) > 80 else None
        raw: dict[str, Any] = {
            "issue_type": itype,
            "chamber": "House",
            "source_body": "OCE",
            "filed_date": issued,
            "subject_matter": (body_txt or title)[:8000] or title,
            "status": st,
            "disposition": disp,
            "resolution_date": None,
            "epistemic_level": epi,
            "source_url": purl,
            "report_page_url": r.get("report_page_url"),
            "category_label": rtype,
            "oce_row": {k: v for k, v in r.items() if k != "body"},
        }
        out.append(
            AdapterResult(
                source_name=source_name,
                source_url=purl,
                entry_type="ethics_issue",
                title=ev_title,
                body=body_out[:8000],
                date_of_event=issued,
                confidence="unverified",
                raw_data=raw,
            )
        )
    return out


class EthicsComplaintsAdapter(BaseAdapter):
    source_name = "Ethics & Conduct Oversight"
    OCE_REPORTS_URL = f"{OCE_BASE}{OCE_REPORTS_PATH}"

    async def search(self, query: str, query_type: str = "name") -> AdapterResponse:
        """
        Scrape the public OCE/OCC report list, ``query`` = member / staff name
        to match in title or summary (``query_type`` reserved; use ``name``).
        """
        _ = query_type
        name_q = (query or "").strip()
        if not name_q or len(name_q) < 2:
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error="name query must be at least 2 characters",
                error_kind="processing",
            )
        url = f"{OCE_BASE}{OCE_REPORTS_PATH}"
        try:
            async with httpx.AsyncClient(
                timeout=45.0, follow_redirects=True, headers=HEADERS
            ) as client:
                r = await client.get(url)
        except (httpx.HTTPError, httpx.RequestError) as e:
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error=str(e),
                error_kind="network",
            )
        if r.status_code != 200:
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error=f"OCE list HTTP {r.status_code}",
                error_kind="network",
            )
        rows = _parse_list_html(r.text)[:200]
        results = _to_adapter_results(self.source_name, rows, name_q)[:100]
        rhash = _result_hash(results, name_q)
        return AdapterResponse(
            source_name=self.source_name,
            query=query,
            results=results,
            found=True,
            result_hash=rhash,
            empty_success=not bool(results),
            parse_warning=None
            if results
            else f"No OCE public report rows matched “{name_q}”.",
        )
