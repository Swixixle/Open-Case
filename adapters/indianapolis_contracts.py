"""Indianapolis / Marion County open records via gis.indy.gov (DMD tax abatement projects).

The city contract register on data.indy.gov is primarily a document hub; this adapter
uses the published DMD Tax Abatement Projects table as structured municipal award data.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from adapters.base import AdapterResponse, AdapterResult, BaseAdapter

MAP_LAYER_BASE = (
    "https://gis.indy.gov/server/rest/services/OpenData/OpenData_NonSpatial/MapServer/9"
)
DATA_PORTAL_PAGE = "https://opendata.arcgis.com/datasets/0c299eefe5454f5b84f1d67a41c2f741_9"
MAX_FETCH = 80
MAX_RETURN = 40
HTTP_TIMEOUT = 60.0


def _tokens(query: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) >= 4]


def _ms_to_date(ms: Any) -> date | None:
    if ms is None:
        return None
    try:
        iv = int(ms)
    except (TypeError, ValueError):
        return None
    if iv <= 0:
        return None
    try:
        return datetime.fromtimestamp(iv / 1000.0, tz=timezone.utc).date()
    except (OverflowError, OSError, ValueError):
        return None


def _parse_cost(raw: Any) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _feature_matches(query: str, attrs: dict[str, Any]) -> bool:
    blob = " ".join(
        str(attrs.get(k) or "")
        for k in ("NAME", "PROJECT_DESCRIPTION", "PRIMARY_PROJECT_ADDRESS", "NAICS_CODE")
    ).lower()
    toks = _tokens(query)
    if not toks:
        return False
    return any(t in blob for t in toks)


def _row_url(object_id: Any) -> str:
    params = {
        "where": f"OBJECTID={object_id}",
        "outFields": "*",
        "returnGeometry": "false",
        "f": "json",
    }
    return f"{MAP_LAYER_BASE}/query?{urlencode(params)}"


class IndianapolisContractsAdapter(BaseAdapter):
    source_name = "INDY_TAX_ABATEMENT"

    async def search(self, query: str, query_type: str = "person") -> AdapterResponse:
        q = (query or "").strip()
        params = {
            "where": "1=1",
            "outFields": "*",
            "returnGeometry": "false",
            "orderByFields": "APPROVED_DATE DESC",
            "resultRecordCount": str(MAX_FETCH),
            "f": "json",
        }
        list_url = f"{MAP_LAYER_BASE}/query?{urlencode(params)}"
        try:
            async with httpx.AsyncClient(
                timeout=HTTP_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": "OpenCase/IndyContracts (civic research)"},
            ) as client:
                resp = await client.get(list_url)
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, httpx.RequestError, ValueError) as e:
            return AdapterResponse(
                source_name=self.source_name,
                query=q,
                results=[
                    AdapterResult(
                        source_name=self.source_name,
                        source_url=DATA_PORTAL_PAGE,
                        entry_type="gap_documented",
                        title="INDY_TAX_ABATEMENT: Network or parse failure (dataset hub only)",
                        body=(
                            f"Could not query gis.indy.gov layer; documented for retry. {e!s}. "
                            f"Hub: {DATA_PORTAL_PAGE}"
                        ),
                        confidence="confirmed",
                        is_absence=True,
                    )
                ],
                found=True,
                empty_success=True,
                error=str(e),
                error_kind="network",
            )

        err = payload.get("error")
        if err:
            return AdapterResponse(
                source_name=self.source_name,
                query=q,
                results=[
                    AdapterResult(
                        source_name=self.source_name,
                        source_url=DATA_PORTAL_PAGE,
                        entry_type="gap_documented",
                        title="INDY_TAX_ABATEMENT: MapServer error (dataset hub only)",
                        body=f"ArcGIS error: {json.dumps(err)[:1800]}. Hub: {DATA_PORTAL_PAGE}",
                        confidence="confirmed",
                        is_absence=True,
                    )
                ],
                found=True,
                empty_success=True,
                error=json.dumps(err)[:2000],
                error_kind="processing",
            )

        features: list[dict[str, Any]] = payload.get("features") or []
        results: list[AdapterResult] = []

        matched = [f for f in features if _feature_matches(q, f.get("attributes") or {})]
        chosen = matched[:MAX_RETURN] if matched else features[: min(MAX_RETURN, len(features))]

        for f in chosen:
            attrs = f.get("attributes") or {}
            oid = attrs.get("OBJECTID")
            name = str(attrs.get("NAME") or "").strip() or "Tax abatement project"
            desc = str(attrs.get("PROJECT_DESCRIPTION") or "").strip()
            addr = str(attrs.get("PRIMARY_PROJECT_ADDRESS") or "").strip()
            approved = _ms_to_date(attrs.get("APPROVED_DATE"))
            cost = _parse_cost(attrs.get("TOTAL_PROJECT_COST_DOLLARS"))
            src = _row_url(oid)
            context_note = (
                "Matched project description/name to subject query."
                if matched
                else (
                    "No name/description match; showing recent DMD tax abatement projects "
                    "for municipal fiscal context (not vendor contracts)."
                )
            )
            body = (
                f"{context_note} Dataset hub: {DATA_PORTAL_PAGE}. "
                f"Address: {addr or 'n/a'}. "
                f"Description: {desc[:3500]}"
            )
            raw_data = {
                "object_id": oid,
                "project_name": name,
                "primary_address": addr,
                "approved_date_ms": attrs.get("APPROVED_DATE"),
                "total_project_cost_raw": attrs.get("TOTAL_PROJECT_COST_DOLLARS"),
                "naics_code": attrs.get("NAICS_CODE"),
                "layer_list_query": list_url,
            }
            results.append(
                AdapterResult(
                    source_name=self.source_name,
                    source_url=src,
                    entry_type="government_record",
                    title=f"Indianapolis DMD tax abatement: {name[:900]}",
                    body=body[:8000],
                    date_of_event=approved.isoformat() if approved else None,
                    amount=cost,
                    matched_name=name,
                    raw_data=raw_data,
                )
            )

        portal_row = AdapterResult(
            source_name=self.source_name,
            source_url=DATA_PORTAL_PAGE,
            entry_type="government_record",
            title="Indianapolis open data: DMD tax abatement projects (dataset)",
            body=(
                "Primary tabular municipal source for this adapter: DMD Tax Abatement Projects "
                f"on the Indianapolis open data portal. Layer: {MAP_LAYER_BASE}. "
                "City contract PDFs are linked from indy.gov; this table is machine-queryable."
            ),
            raw_data={"hub": DATA_PORTAL_PAGE, "map_layer": MAP_LAYER_BASE},
        )
        results.insert(0, portal_row)

        warn = (
            "data.indy.gov lists a City Contracts document hub without a single contracts API; "
            "this run uses DMD Tax Abatement Projects (gis.indy.gov) as structured award data."
        )

        return AdapterResponse(
            source_name=self.source_name,
            query=q,
            results=results,
            found=True,
            result_hash=json.dumps({"n": len(results), "q": q}, sort_keys=True),
            parse_warning=warn,
        )
