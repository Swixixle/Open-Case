"""Indianapolis awarded-contract sources (procurement-focused; additive to INDY_TAX_ABATEMENT).

Primary and secondary portals do not expose stable, server-rendered tabular APIs in the
responses we can fetch without a browser session. This adapter documents observed structure
and emits gap rows so investigations complete honestly. When a future integration (GraphQL,
export, or HTML contract list) is confirmed, add rows here with source tags INDY_PROCUREMENT / INDY_GATEWAY_CONTRACT_DOC and populate vendor_canonical via normalize_vendor_name().
"""

from __future__ import annotations

import json
import re

import httpx

from adapters.base import AdapterResponse, AdapterResult, BaseAdapter

PRIMARY_PORTAL_URL = "https://www.indy.gov/activity/city-county-contracts"
GATEWAY_BASE_URL = "https://gateway.ifionline.org/"
HTTP_TIMEOUT = 45.0
USER_AGENT = "OpenCase/IndyProcurement (civic research; contact agency for bulk access)"


def normalize_vendor_name(raw: str) -> str:
    """
    Adapter-side normalization for vendor strings (suffixes, punctuation, light expansion).
    Stored as vendor_canonical on each procurement row for pattern-engine matching.
    Not a substitute for human alias review (no fuzzy merge).
    """
    if not raw or not str(raw).strip():
        return ""
    s = str(raw).strip().upper()
    s = s.replace("&", " AND ")
    for ch in ".,()\"'":
        s = s.replace(ch, " ")
    s = re.sub(r"\s+", " ", s).strip()
    # Light abbreviation expansion (token boundaries)
    s = re.sub(r"\bMUN\b", "MUNICIPAL", s)
    s = re.sub(r"\bDEPT\b", "DEPARTMENT", s)
    s = re.sub(r"\bSVC\b", "SERVICES", s)
    s = re.sub(r"\bSVCS\b", "SERVICES", s)
    tail_noise = (
        "LIMITED LIABILITY COMPANY",
        "LIMITED LIABILITY CORP",
        "L L C",
        "LLC",
        "L L P",
        "LLP",
        "INCORPORATED",
        "INC",
        "CORPORATION",
        "CORP",
        "COMPANY",
        "CO",
        "LP",
        "PLLC",
        "PC",
    )
    parts = s.split()
    while parts and parts[-1] in tail_noise:
        parts.pop()
    return " ".join(parts).strip()


async def _fetch_text(client: httpx.AsyncClient, url: str) -> tuple[int, str]:
    r = await client.get(url)
    return r.status_code, r.text


class IndianapolisProcurementAdapter(BaseAdapter):
    """Tags: INDY_PROCUREMENT (primary), INDY_GATEWAY_CONTRACT_DOC (secondary)."""

    source_name = "INDY_PROCUREMENT"

    async def search(self, query: str, query_type: str = "person") -> AdapterResponse:
        q = (query or "").strip()
        results: list[AdapterResult] = []
        parse_parts: list[str] = []

        try:
            async with httpx.AsyncClient(
                timeout=HTTP_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            ) as client:
                primary_status, primary_html = await _fetch_text(client, PRIMARY_PORTAL_URL)
                gw_status, gateway_html = await _fetch_text(client, GATEWAY_BASE_URL)
        except (httpx.HTTPError, httpx.RequestError) as e:
            return AdapterResponse(
                source_name=self.source_name,
                query=q,
                results=[
                    AdapterResult(
                        source_name=self.source_name,
                        source_url=PRIMARY_PORTAL_URL,
                        entry_type="gap_documented",
                        title="INDY_PROCUREMENT: network failure reaching portals",
                        body=f"Could not fetch procurement portals: {e!s}",
                        confidence="confirmed",
                        is_absence=True,
                    )
                ],
                found=True,
                empty_success=True,
                parse_warning=str(e),
                error_kind="network",
            )

        # --- Primary portal structure (documented; no row parsing) ---
        primary_doc = (
            f"HTTP status {primary_status}. Static HTML is a client-rendered shell: "
            "typically includes <div id=\"app\" activity_id=\"city-county-contracts\"></div> "
            "inside <main> with almost no vendor/contract rows in the initial document. "
            "The activity bundle loads content via Apollo GraphQL using the activity_id as "
            "slug (see www.indy.gov/js/activity-*.js: query activity, variables.slug). "
            "The React activity view may redirect the browser via external_content_url when "
            "set, so the vendor-search UI may live off this host. "
            "Open Case does not guess GraphQL endpoints or parse minified bundles here."
        )
        if "activity_id=\"city-county-contracts\"" in primary_html or (
            "activity_id='city-county-contracts'" in primary_html
        ):
            primary_doc += " Confirmed: activity_id=city-county-contracts present in fetched HTML."
        else:
            primary_doc += (
                " Note: expected activity_id marker not found in this fetch; page may have changed."
            )

        parse_parts.append(primary_doc)
        results.append(
            AdapterResult(
                source_name="INDY_PROCUREMENT",
                source_url=PRIMARY_PORTAL_URL,
                entry_type="gap_documented",
                title="INDY_PROCUREMENT: City/County Contracts portal (structure only)",
                body=primary_doc,
                confidence="confirmed",
                is_absence=True,
                raw_data={
                    "portal": "indy_gov_activity",
                    "activity_slug": "city-county-contracts",
                    "http_status": primary_status,
                    "html_length": len(primary_html),
                    "query": q,
                },
            )
        )

        # --- Secondary Gateway structure (ASP.NET WebForms; no POST guessing) ---
        gateway_doc = (
            f"HTTP status {gw_status}. Response is Indiana Gateway (Classic ASP.NET): "
            "contains <form name=\"aspnetForm\" method=\"post\" action=\"default.aspx\"> "
            "with __VIEWSTATE. Local contract document search requires form posts and "
            "session navigation; not automated in this adapter to avoid incorrect parameters."
        )
        if "__VIEWSTATE" in gateway_html:
            gateway_doc += " Confirmed: __VIEWSTATE present (WebForms)."
        parse_parts.append(gateway_doc)
        results.append(
            AdapterResult(
                source_name="INDY_GATEWAY_CONTRACT_DOC",
                source_url=GATEWAY_BASE_URL,
                entry_type="gap_documented",
                title="INDY_GATEWAY_CONTRACT_DOC: Gateway (document search; not automated)",
                body=gateway_doc,
                confidence="confirmed",
                is_absence=True,
                raw_data={
                    "portal": "ifionline_gateway",
                    "http_status": gw_status,
                    "html_length": len(gateway_html),
                    "query": q,
                },
            )
        )

        # Tertiary: data.indy.gov — no structured vendor-award dataset wired here;
        # tax abatement / other layers remain on INDY_TAX_ABATEMENT adapter only.
        parse_parts.append(
            "Tertiary data.indy.gov: no separate vendor-award feature service added here; "
            "budget PDFs and unrelated layers are out of scope for this adapter."
        )

        return AdapterResponse(
            source_name=self.source_name,
            query=q,
            results=results,
            found=True,
            empty_success=True,
            result_hash=json.dumps(
                {"primary_status": primary_status, "gateway_status": gw_status, "q": q},
                sort_keys=True,
            ),
            parse_warning=" | ".join(parse_parts),
        )
