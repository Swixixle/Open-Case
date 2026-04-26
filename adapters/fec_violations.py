"""
FEC OpenFEC ``/v1/legal/search/`` — MURs, administrative fines, and ADRs for a committee.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import httpx

from adapters.base import AdapterResponse, AdapterResult, BaseAdapter
from adapters.fec import _fec_interpret_body_api_error
from core.credentials import CredentialRegistry, CredentialUnavailable

LEGAL_SEARCH = "/legal/search/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; OpenCase/1.0) fec-violations",
    "Accept": "application/json",
}

_SEARCH_TYPES: tuple[tuple[str, str, str], ...] = (
    ("murs", "murs", "MUR"),
    ("admin_fines", "admin_fines", "AF"),
    ("adrs", "adrs", "ADR"),
)


def _fec_key() -> str:
    try:
        k = CredentialRegistry.get_credential("fec")
    except CredentialUnavailable:
        k = None
    return (k or "DEMO_KEY").strip()


def _source_url(maybe_path: str) -> str:
    u = (maybe_path or "").strip()
    if u.startswith("http"):
        return u
    if u.startswith("/"):
        return f"https://www.fec.gov{u}"
    return f"https://www.fec.gov/data/legal/{u}" if u else "https://www.fec.gov/data/legal/search/enforcement/"


async def _committee_display_name(
    client: httpx.AsyncClient, api_key: str, committee_id: str
) -> str | None:
    """Resolve registered committee name for legal keyword search."""
    cid = (committee_id or "").strip().upper()
    if not re.match(r"^C[0-9A-Z]{7,12}$", cid):
        return None
    url = f"{FECViolationsAdapter.BASE_URL}/committee/{cid}/"
    try:
        r = await client.get(
            url, params={"api_key": api_key, "per_page": 1}, timeout=30.0
        )
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except json.JSONDecodeError:
        return None
    rec = None
    if isinstance(data, dict):
        if isinstance(data.get("results"), list) and data["results"]:
            rec = data["results"][0]
        else:
            rec = data
    if not isinstance(rec, dict):
        return None
    name = (rec.get("name") or rec.get("committee_id_raw") or "").strip()
    return name or None


def _parse_day(s: str | None) -> str | None:
    if not s:
        return None
    t = str(s).strip()[:10]
    if len(t) == 10 and t[4] == "-":
        return t
    return None


def _respondents_json(item: dict[str, Any]) -> str:
    r = item.get("respondents")
    if r is None:
        return "[]"
    if isinstance(r, str):
        return json.dumps([r], ensure_ascii=False)
    if isinstance(r, list):
        out: list[str] = []
        for x in r:
            if isinstance(x, str):
                out.append(x)
            elif isinstance(x, dict):
                out.append(
                    str(x.get("name") or x.get("respondent") or x)[:512]
                )
        return json.dumps(out, ensure_ascii=False)
    if isinstance(r, dict):
        return json.dumps([str(r.get("name") or r)], ensure_ascii=False)
    return json.dumps([str(r)], ensure_ascii=False)


def _subject_text(item: dict[str, Any], case_label: str) -> str:
    subs = item.get("subjects")
    if isinstance(subs, list) and subs:
        parts: list[str] = []
        for s in subs:
            if isinstance(s, dict) and s.get("subject"):
                parts.append(str(s["subject"]))
            elif isinstance(s, str):
                parts.append(s)
        if parts:
            return "; ".join(parts)[:8000]
    n = (item.get("name") or "").strip()
    if n:
        return n[:8000]
    return f"{case_label} matter (no subject line in API)"


def _disposition_text(item: dict[str, Any]) -> str | None:
    d = item.get("dispositions")
    if isinstance(d, list) and d:
        bits: list[str] = []
        for x in d:
            if isinstance(x, dict):
                bits.append(str(x.get("action") or x.get("text") or x)[:2000])
            else:
                bits.append(str(x)[:2000])
        if bits:
            return " | ".join(bits)[:8000]
    if isinstance(d, str) and d.strip():
        return d.strip()[:8000]
    return None


def _fine_amount(item: dict[str, Any]) -> int | None:
    for k in (
        "final_determination_amount",
        "payment_amount",
        "reason_to_believe_fine_amount",
    ):
        v = item.get(k)
        if v is None:
            continue
        try:
            return int(float(v))
        except (TypeError, ValueError):
            continue
    return None


def _status_str(item: dict[str, Any], _case_label: str) -> str:
    c = (item.get("case_status") or "").strip()
    if c:
        return c[:64]
    if _parse_day(str(item.get("close_date") or "")) or item.get("close_date"):
        return "Closed"
    return "Open"


def _epistemic(item: dict[str, Any], case_label: str) -> str:
    st = (item.get("case_status") or "").lower()
    if "closed" in st or st == "closed":
        return "VERIFIED"
    if _parse_day(str(item.get("close_date") or "")):
        return "VERIFIED"
    if case_label == "AF" and (item.get("final_determination_date") or _fine_amount(item)):
        return "VERIFIED"
    return "REPORTED"


def _rows_from_payload(
    data: dict[str, Any], response_key: str, case_type: str, label: str
) -> list[dict[str, Any]]:
    if not isinstance(data, dict) or "error" in data:
        return []
    block = data.get(response_key)
    if not isinstance(block, list):
        return []
    return [{**row, "_case_type": case_type, "_fec_kind": label} for row in block if isinstance(row, dict)]


def _row_key(row: dict[str, Any]) -> str:
    return "|".join(
        (
            str(row.get("_fec_kind") or ""),
            str(row.get("_case_type") or ""),
            str(row.get("no") or row.get("case_serial") or ""),
        )
    )


def _to_results(
    source_name: str, rows: list[dict[str, Any]], search_q: str
) -> list[AdapterResult]:
    out: list[AdapterResult] = []
    for item in rows:
        ct = str(item.get("_case_type") or "MUR")
        label = str(item.get("_fec_kind") or "murs")
        mur_no = str(item.get("no") or item.get("case_serial") or "")[:32] or "?"
        opened = _parse_day(str(item.get("open_date") or "")) or _parse_day(
            str(item.get("reason_to_believe_action_date") or "")
        )
        closed = _parse_day(str(item.get("close_date") or "")) or _parse_day(
            str(item.get("final_determination_date") or "")
        )
        dis = _disposition_text(item) or ""
        fine = _fine_amount(item)
        rjson = _respondents_json(item)
        subj = _subject_text(item, ct)
        st = _status_str(item, ct)
        epi = _epistemic(item, ct)
        src = _source_url(str(item.get("url") or ""))
        body_parts = [f"Respondents (API): {rjson}."]
        if dis:
            body_parts.append(f"Disposition: {dis[:2000]}{'…' if len(dis) > 2000 else ''}")
        if fine is not None:
            body_parts.append(f"Fine (where applicable): ${fine:,}.")
        body_parts.append(f"Status: {st}.")
        body = " ".join(body_parts)[:8000]
        title = f"{ct} {mur_no}: {subj[:120]}{'…' if len(subj) > 120 else ''}"
        raw: dict[str, Any] = {
            "mur_number": mur_no,
            "case_type": ct,
            "filed_date": opened,
            "closed_date": closed,
            "respondent_names": rjson,
            "subject_matter": subj,
            "disposition": dis or None,
            "fine_amount": fine,
            "status": st,
            "source_url": src,
            "epistemic_level": epi,
            "fec_legal_type": label,
            "search_query": search_q,
            "raw": {k: v for k, v in item.items() if not k.startswith("_")},
        }
        out.append(
            AdapterResult(
                source_name=source_name,
                source_url=src,
                entry_type="fec_violation",
                title=title,
                body=body,
                date_of_event=opened or closed,
                amount=float(fine) if fine is not None else None,
                confidence="confirmed",
                raw_data=raw,
            )
        )
    return out


def _result_hash(items: list[AdapterResult], q: str) -> str:
    h = hashlib.sha256(
        json.dumps(
            [sorted((r.raw_data or {}).items()) for r in items],
            default=str,
            sort_keys=True,
        ).encode()
    ).hexdigest()[:32]
    return f"{q}|{h}"


class FECViolationsAdapter(BaseAdapter):
    source_name = "FEC Enforcement"
    BASE_URL = "https://api.open.fec.gov/v1"

    async def search(self, query: str, query_type: str = "committee") -> AdapterResponse:
        """
        ``query`` = principal FEC committee id (e.g. C00…) or a committee / keyword string.
        Legal search uses the resolved committee name when ``query`` is a committee id.
        """
        q0 = (query or "").strip()
        if not q0:
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error="empty query",
                error_kind="processing",
            )
        try:
            api_key = _fec_key()
        except Exception:
            api_key = "DEMO_KEY"
        if not api_key:
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error="FEC API key not available",
                error_kind="credential",
            )
        all_flat: list[dict[str, Any]] = []
        search_q = q0
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                resolved = await _committee_display_name(client, api_key, q0)
                if resolved:
                    search_q = resolved
                for url_key, json_key, case_code in _SEARCH_TYPES:
                    params: dict[str, str | int] = {
                        "api_key": api_key,
                        "type": url_key,
                        "q": search_q,
                        "per_page": 100,
                    }
                    r = await client.get(
                        f"{self.BASE_URL}{LEGAL_SEARCH}",
                        params=params,
                        headers=HEADERS,
                    )
                    try:
                        data = r.json()
                    except json.JSONDecodeError:
                        return AdapterResponse(
                            source_name=self.source_name,
                            query=query,
                            results=[],
                            found=False,
                            error="FEC legal search: invalid JSON",
                            error_kind="processing",
                        )
                    fe = _fec_interpret_body_api_error(data)
                    if fe is not None:
                        kind, msg = fe
                        return AdapterResponse(
                            source_name=self.source_name,
                            query=query,
                            results=[],
                            found=False,
                            error=msg,
                            error_kind=kind,
                        )
                    if r.status_code != 200:
                        return AdapterResponse(
                            source_name=self.source_name,
                            query=query,
                            results=[],
                            found=False,
                            error=f"HTTP {r.status_code} from legal/search",
                            error_kind="network",
                        )
                    all_flat.extend(
                        _rows_from_payload(data, json_key, case_code, url_key)
                    )
        except (httpx.HTTPError, httpx.RequestError) as e:
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error=str(e),
                error_kind="network",
            )
        # De-duplicate
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for row in all_flat:
            k = _row_key(row)
            if k in seen:
                continue
            seen.add(k)
            deduped.append(row)
        results = _to_results(self.source_name, deduped, search_q)
        rhash = _result_hash(results, search_q)
        return AdapterResponse(
            source_name=self.source_name,
            query=query,
            results=results,
            found=True,
            result_hash=rhash,
            empty_success=not bool(results),
            parse_warning=None
            if results
            else f"No MUR/AF/ADR rows for search “{search_q}”.",
        )
