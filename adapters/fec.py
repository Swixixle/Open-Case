from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import date, datetime
from typing import Any
from urllib.parse import quote_plus, urlencode

import httpx

from adapters.base import AdapterResponse, AdapterResult, BaseAdapter, apply_collision_rule
from core.credentials import CredentialRegistry, CredentialUnavailable

logger = logging.getLogger(__name__)

_CANDIDATE_SEARCH_PATH = "/candidates/search/"

_FEC_CREDENTIAL_ERROR_CODES = frozenset(
    {"API_KEY_INVALID", "API_KEY_MISSING", "FORBIDDEN"}
)


def _fec_key_source_label() -> str:
    """How the active FEC API key was sourced (never logs the key)."""
    if os.environ.get("FEC_API_KEY", "").strip():
        return "env"
    if CredentialRegistry._file_secret("fec"):
        return "file"
    return "fallback_demo"


# Public alias for callers that need the label in user-facing messages (e.g. investigate).
fec_credential_source_label = _fec_key_source_label


def _fec_schedule_a_query_label(query: str, query_type: str) -> str:
    if query_type == "committee":
        return f"committee_id={query}"
    return f"contributor_name={query}"


def _fec_interpret_body_api_error(data: Any) -> tuple[str, str] | None:
    """
    OpenFEC error object: {"error": {"code": "...", "message": "..."}}
    Without this, data.get("results", []) → [] looks like an honest empty run.
    """
    if not isinstance(data, dict) or "error" not in data:
        return None
    err = data["error"]
    if isinstance(err, dict):
        code = str(err.get("code", "UNKNOWN"))
        msg = str(err.get("message", err))
    else:
        code = "UNKNOWN"
        msg = str(err)
    codes_upper = {c.upper() for c in _FEC_CREDENTIAL_ERROR_CODES}
    if code.upper() in codes_upper:
        return (
            "credential",
            f"FEC API credential rejected: {code} — {msg}",
        )
    return ("processing", f"FEC API error: {code} — {msg}")


def _mask_url(url: str) -> str:
    return re.sub(r"(api_key=)([^&]+)", r"\1***", url, flags=re.IGNORECASE)


def _candidate_recency_key(cand: dict[str, Any]) -> str:
    """Sort key: most recently filed first (ISO date string from OpenFEC)."""
    for k in ("last_file_date", "last_f1_date", "first_file_date"):
        v = cand.get(k)
        if v:
            return str(v).strip()[:10]
    return ""


def _principal_committee_id_from_candidate_list(
    data: Any,
    *,
    state_filter: str | None,
) -> str | None:
    """
    OpenFEC ``candidates/search`` JSON ``results`` → first principal or fallback committee_id.
    """
    if not isinstance(data, dict):
        return None
    api_err = _fec_interpret_body_api_error(data)
    if api_err:
        return None
    matching: list[dict[str, Any]] = []
    for cand in data.get("results") or []:
        if not isinstance(cand, dict):
            continue
        if state_filter and str(cand.get("state") or "").upper() != state_filter:
            continue
        if cand.get("candidate_inactive") is True:
            continue
        matching.append(cand)
    matching.sort(key=_candidate_recency_key, reverse=True)
    for cand in matching:
        committees = cand.get("principal_committees") or []
        principal_ids: list[str] = []
        fallback_ids: list[str] = []
        for pc in committees:
            if not isinstance(pc, dict):
                continue
            cid = pc.get("committee_id")
            if not cid:
                continue
            cid_s = str(cid).strip().upper()
            des_full = str(pc.get("designation_full") or "")
            if "Principal" in des_full or pc.get("designation") == "P":
                principal_ids.append(cid_s)
            else:
                fallback_ids.append(cid_s)
        for cid_s in principal_ids + fallback_ids:
            if cid_s:
                return cid_s
    return None


async def resolve_principal_committee_id_for_official(
    subject_name: str,
    jurisdiction: str,
    *,
    bioguide_id: str | None = None,
) -> str | None:
    """
    OpenFEC principal campaign committee for a named federal candidate.

    Used when investigating a public official without an explicit fec_committee_id:
    schedule_a by committee_id returns receipts *to* that committee (donors to pair
    with votes). schedule_a by contributor_name searches the wrong economic direction
    for this use case.

    Resolution order: ``candidates/search`` by ``name`` + office (S/H/P); if none,
    the same path with ``q=bioguide_id`` so Congress bioguides that map to a candidate
    row can still yield ``principal_committees``.
    """
    try:
        api_key = CredentialRegistry.get_credential("fec") or "DEMO_KEY"
    except CredentialUnavailable:
        api_key = "DEMO_KEY"

    search_name = (subject_name or "").strip()
    bg_hint = (bioguide_id or "").strip().upper()
    if not search_name and not bg_hint:
        return None

    state_filter: str | None = None
    j = (jurisdiction or "").strip().upper()
    if len(j) == 2 and j.isalpha():
        state_filter = j

    cred_src = _fec_key_source_label()
    api = f"{FECAdapter.BASE_URL}{_CANDIDATE_SEARCH_PATH}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        if search_name:
            # S/H/P: multi-party / indie senators (e.g. Sanders) may have presidential (P)
            # rows with stale committees; Senate rows sorted by recency yield the active principal.
            for office in ("S", "H", "P"):
                params: dict[str, str | int] = {
                    "api_key": api_key,
                    "name": search_name,
                    "office": office,
                    "per_page": 50,
                    "sort": "-last_file_date",
                }
                try:
                    r = await client.get(api, params=params)
                except httpx.HTTPError:
                    continue
                req_url = _mask_url(str(r.request.url))
                if r.status_code == 429:
                    logger.warning(
                        "FEC candidates/search rate limited (%s) for %r",
                        cred_src,
                        search_name,
                    )
                    continue
                if r.status_code == 403:
                    logger.warning(
                        "FEC candidates/search HTTP 403 (credential_mode=%s)", cred_src
                    )
                    continue
                if r.status_code >= 400:
                    logger.warning(
                        "FEC candidates/search HTTP %s (credential_mode=%s) url=%s for name=%r office=%s",
                        r.status_code,
                        cred_src,
                        req_url,
                        search_name,
                        office,
                    )
                    continue
                try:
                    data = r.json()
                except Exception:
                    continue
                api_err = _fec_interpret_body_api_error(data)
                if api_err:
                    logger.warning(
                        "FEC candidates/search API error: %s (credential_mode=%s)",
                        api_err[1],
                        cred_src,
                    )
                    continue
                out = _principal_committee_id_from_candidate_list(
                    data, state_filter=state_filter
                )
                if out:
                    if bg_hint:
                        logger.debug(
                            "FEC principal committee resolved name=%r office=%s committee_id=%s bioguide_hint=%s",
                            search_name,
                            office,
                            out,
                            bg_hint,
                        )
                    return out

        if bg_hint:
            # Bioguide (Congress ID) in ``q`` can match linked candidate rows when name search does not.
            params: dict[str, str | int] = {
                "api_key": api_key,
                "q": bg_hint,
                "per_page": 100,
                "sort": "-last_file_date",
            }
            try:
                r2 = await client.get(api, params=params)
            except httpx.HTTPError:
                return None
            if r2.status_code == 429:
                logger.warning(
                    "FEC candidates/search rate limited (%s) for bioguide q=%r",
                    cred_src,
                    bg_hint,
                )
                return None
            if r2.status_code == 403:
                logger.warning(
                    "FEC candidates/search HTTP 403 (credential_mode=%s) bioguide q",
                    cred_src,
                )
                return None
            if r2.status_code >= 400:
                logger.warning(
                    "FEC candidates/search HTTP %s (credential_mode=%s) url=%s for bioguide q=%r",
                    r2.status_code,
                    cred_src,
                    _mask_url(str(r2.request.url)),
                    bg_hint,
                )
                return None
            try:
                data2 = r2.json()
            except Exception:
                return None
            api_err2 = _fec_interpret_body_api_error(data2)
            if api_err2:
                logger.warning(
                    "FEC candidates/search API error (bioguide q): %s (credential_mode=%s)",
                    api_err2[1],
                    cred_src,
                )
                return None
            out2 = _principal_committee_id_from_candidate_list(
                data2, state_filter=state_filter
            )
            if out2:
                logger.debug(
                    "FEC principal committee resolved via bioguide q=%s committee_id=%s",
                    bg_hint,
                    out2,
                )
                return out2
    return None


# Donor shapes that are very unlikely to be accidental multi-entity personal-name matches.
# FEC Schedule A transaction types treated as refunds / reversals for ingestion.
# Do not add 15Z here — negative 15Z rows are handled via amount check + warning.
FEC_SCHEDULE_A_SKIP_TRANSACTION_TYPES = frozenset({"22Z", "20Z", "17Z"})


def fec_schedule_a_row_exclusion_reason(item: dict[str, Any]) -> str | None:
    """
    If the Schedule A row must not become an AdapterResult, return a short reason code.
    Used by the adapter and by tests (refund / negative receipt guard).
    """
    if not isinstance(item, dict):
        return "invalid_item"
    tp = str(item.get("transaction_tp") or "").strip().upper()
    if tp in FEC_SCHEDULE_A_SKIP_TRANSACTION_TYPES:
        return "fec_refund_transaction_type"
    raw_amt = item.get("contribution_receipt_amount") or 0
    try:
        amt_f = float(raw_amt)
    except (TypeError, ValueError):
        return None
    if amt_f < 0:
        return "negative_amount"
    return None


def classify_donor_type(entity_type: str, committee_type: str | None) -> str:
    """
    Map OpenFEC Schedule A entity_type + recipient committee_type to a stable bucket.
    Returns: individual | corporation | super_pac | pac | party | candidate_cmte | other_org
    """
    et = (entity_type or "").strip().upper()
    ct = (committee_type or "").strip().upper()
    if len(ct) > 1:
        ct = ct[:1]

    if et == "IND":
        return "individual"
    if et == "ORG":
        return "corporation"
    if et in ("COM", "CCM"):
        if ct in {"U", "V", "W"}:
            return "super_pac"
        if ct in {"N", "Q"}:
            return "pac"
        if ct in {"X", "Y", "Z"}:
            return "party"
        if ct in {"H", "S", "P", "A", "B", "D"}:
            return "candidate_cmte"
    return "other_org"


_UNAMBIGUOUS_NAME_MARKERS = (
    "PAC",
    "INC",
    "LLC",
    "COMMITTEE",
    "CORP",
    "ASSOCIATION",
    "FOUNDATION",
    "FUND",
)


def _is_likely_unambiguous(donor_name: str) -> bool:
    """True if the contributor string looks like a legal entity, not a vague personal name."""
    if not donor_name or not str(donor_name).strip():
        return False
    upper = str(donor_name).upper()
    return any(marker in upper for marker in _UNAMBIGUOUS_NAME_MARKERS)


def _parse_contribution_receipt_date(raw: Any) -> date | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    if "T" in s:
        s = s.split("T", 1)[0]
    if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(s[:10]).date()
    except ValueError:
        return None


def _fec_election_two_year(d: date) -> int:
    """FEC two-year label is the even year ending the cycle (e.g. 2025-01-01 -> 2026)."""
    y = d.year
    return y if y % 2 == 0 else y + 1


def _format_amount_for_fec_receipts_url(amount: Any) -> str | None:
    try:
        f = float(amount)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    if abs(f - round(f)) < 1e-9:
        return str(int(round(f)))
    s = f"{f:.2f}"
    if s.endswith("0") and s[-2] == ".":
        s = s.rstrip("0").rstrip(".")
    return s or None


def build_fec_receipt_search_url(
    item: dict[str, Any],
    *,
    committee_id: str,
    two_year_period: int | None = None,
) -> str:
    """
    Public FEC data UI URL that reproduces a single reported receipt in the
    pre-filtered receipts table (verifiable from evidence rows).
    """
    com = item.get("committee")
    com_id = (committee_id or "").strip().upper()
    if not com_id and isinstance(com, dict):
        com_id = str(com.get("committee_id") or "").strip().upper()

    parts: list[tuple[str, str]] = [("data_type", "processed")]
    if com_id:
        parts.append(("committee_id", com_id))

    contributor = str(item.get("contributor_name") or "").strip()
    if contributor:
        parts.append(("contributor_name", contributor))

    d = _parse_contribution_receipt_date(item.get("contribution_receipt_date"))
    if two_year_period is not None:
        period = int(two_year_period)
    elif d is not None:
        period = _fec_election_two_year(d)
    else:
        period = _fec_election_two_year(date.today())
    parts.append(("two_year_transaction_period", str(period)))

    if d is not None:
        mmdd = d.strftime("%m/%d/%Y")
        parts.append(("min_date", mmdd))
        parts.append(("max_date", mmdd))

    amt = _format_amount_for_fec_receipts_url(item.get("contribution_receipt_amount"))
    if amt is not None:
        parts.append(("min_amount", amt))
        parts.append(("max_amount", amt))

    q = urlencode(parts, quote_via=quote_plus)
    return f"https://www.fec.gov/data/receipts/?{q}"


class FECAdapter(BaseAdapter):
    source_name = "FEC"
    BASE_URL = "https://api.open.fec.gov/v1"

    def _fec_schedule_failure(
        self,
        query: str,
        query_type: str,
        error: str,
        error_kind: str,
        cred_src: str,
    ) -> AdapterResponse:
        qctx = _fec_schedule_a_query_label(query, query_type)
        return AdapterResponse(
            source_name=self.source_name,
            query=query,
            results=[],
            found=False,
            error=f"{error} | credential_mode={cred_src} | {qctx}",
            error_kind=error_kind,
            credential_mode=cred_src,
        )

    def _fec_detail_line(
        self, cred_src: str, query: str, query_type: str, n_items: int
    ) -> str:
        qctx = _fec_schedule_a_query_label(query, query_type)
        if n_items == 0:
            tail = "0 receipts returned"
        else:
            tail = f"{n_items} schedule_a receipts"
        return f"credential_mode={cred_src} | {qctx} | {tail}"

    def _fec_committee_schedule_a_422_gap_response(
        self, committee_id: str, cred_src: str
    ) -> AdapterResponse:
        """
        OpenFEC returns HTTP 422 for some committee_id schedule_a queries even after
        dropping two_year_transaction_period (known with Bernie Sanders' principal
        committee across presidential/Senate filing shapes). Surface as gap_documented
        so investigate does not fail required-adapter checks — not a bug in this adapter.
        """
        cid = (committee_id or "").strip().upper()
        fec_url = f"https://www.fec.gov/data/receipts/?committee_id={cid}"
        title = "FEC Schedule A: committee query rejected (HTTP 422)"
        body = (
            f"OpenFEC declined schedule_a for committee_id {cid} (HTTP 422). "
            "Upstream limitation for certain committees with non-standard multi-cycle "
            "filings (commonly reported for Bernie Sanders). Manual receipts: "
            f"{fec_url}"
        )
        return AdapterResponse(
            source_name=self.source_name,
            query=committee_id,
            results=[
                AdapterResult(
                    source_name=self.source_name,
                    source_url=fec_url,
                    entry_type="gap_documented",
                    title=title,
                    body=body,
                    confidence="confirmed",
                    is_absence=True,
                    raw_data={
                        "gap_reason": "fec_schedule_a_http_422_committee",
                        "committee_id": cid,
                        "credential_mode": cred_src,
                    },
                )
            ],
            found=True,
            credential_mode=cred_src,
            parse_warning=f"FEC schedule_a HTTP 422 for committee {cid} (documented gap)",
        )

    async def search(
        self,
        query: str,
        query_type: str = "person",
        *,
        two_year_transaction_period: int | None = None,
    ) -> AdapterResponse:
        if query_type == "schedule_b":
            return await self.search_schedule_b(query)
        cred_src = _fec_key_source_label()
        try:
            api_key = CredentialRegistry.get_credential("fec") or "DEMO_KEY"
        except CredentialUnavailable:
            api_key = "DEMO_KEY"
        key_source = f"FEC key from {cred_src}"
        try:
            if query_type == "committee":
                params: dict[str, str | int | bool] = {
                    "committee_id": query,
                    "sort_hide_null": True,
                    "per_page": 100,
                    "api_key": api_key,
                }
                if two_year_transaction_period is not None:
                    params["two_year_transaction_period"] = int(two_year_transaction_period)
            else:
                params = {
                    "contributor_name": query,
                    "sort_hide_null": True,
                    "per_page": 20,
                    "api_key": api_key,
                }

            schedule_a_params_used: dict[str, str | int | bool] = params
            api_path = f"{self.BASE_URL}/schedules/schedule_a/"
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(api_path, params=params)
                req_url = _mask_url(str(response.request.url))
                if (
                    response.status_code == 422
                    and query_type == "committee"
                    and params.get("two_year_transaction_period") is not None
                ):
                    body_prev = (response.text or "")[:2000]
                    logger.error(
                        "FEC schedule_a HTTP 422 with two_year_transaction_period=%s — "
                        "retrying without cycle. url=%s response_preview=%s",
                        params.get("two_year_transaction_period"),
                        req_url,
                        body_prev,
                    )
                    params_retry = {
                        k: v
                        for k, v in params.items()
                        if k != "two_year_transaction_period"
                    }
                    schedule_a_params_used = params_retry
                    response = await client.get(api_path, params=params_retry)
                    req_url = _mask_url(str(response.request.url))

            if response.status_code == 422 and query_type == "committee":
                body_prev = (response.text or "")[:800]
                logger.warning(
                    "FEC schedule_a HTTP 422 for committee_id=%r url=%s — known upstream "
                    "limitation for some committees (notably Bernie Sanders principal-committee "
                    "queries across cycle layouts); returning gap_documented. preview=%s",
                    query,
                    req_url,
                    body_prev,
                )
                return self._fec_committee_schedule_a_422_gap_response(query, cred_src)

            logger.warning(
                "[FECAdapter DEBUG] query_type=%s query=%r key_source=%s status=%s url=%s",
                query_type,
                query,
                key_source,
                response.status_code,
                req_url,
            )

            if response.status_code == 429:
                return self._fec_schedule_failure(
                    query,
                    query_type,
                    "FEC API rate limit exceeded",
                    "rate_limited",
                    cred_src,
                )
            if response.status_code == 403:
                return self._fec_schedule_failure(
                    query,
                    query_type,
                    "FEC API authentication failed (HTTP 403) — check FEC_API_KEY",
                    "credential",
                    cred_src,
                )

            try:
                data = response.json()
            except Exception as e:
                if response.status_code >= 400:
                    return self._fec_schedule_failure(
                        query,
                        query_type,
                        f"FEC API HTTP {response.status_code} (invalid JSON body: {e!s})",
                        "processing",
                        cred_src,
                    )
                text = (
                    (response.text[:800] + "…")
                    if len(response.text) > 800
                    else response.text
                )
                logger.warning(
                    "[FECAdapter DEBUG] JSON parse failed: %s body_preview=%r",
                    e,
                    text,
                )
                raise

            if response.status_code >= 400:
                body_prev = (response.text or "")[:2000]
                logger.error(
                    "FEC schedule_a HTTP %s query_type=%s query=%r url=%s body_preview=%s",
                    response.status_code,
                    query_type,
                    query,
                    req_url,
                    body_prev,
                )
                api_err = _fec_interpret_body_api_error(data)
                if api_err:
                    ek, msg = api_err
                    return self._fec_schedule_failure(
                        query, query_type, msg, ek, cred_src
                    )
                return self._fec_schedule_failure(
                    query,
                    query_type,
                    f"FEC API HTTP {response.status_code}",
                    "processing",
                    cred_src,
                )

            api_err = _fec_interpret_body_api_error(data)
            if api_err:
                ek, msg = api_err
                return self._fec_schedule_failure(query, query_type, msg, ek, cred_src)

            raw_hash = hashlib.sha256(
                json.dumps(data, sort_keys=True).encode()
            ).hexdigest()

            items = data.get("results") or []
            if not isinstance(items, list):
                items = []
            logger.debug(
                "FEC raw schedule_a count: %s",
                len(items),
            )
            pagination = data.get("pagination", {}) if isinstance(data, dict) else {}
            api_msg = None
            if isinstance(data, dict):
                errs = data.get("errors")
                if isinstance(errs, list) and errs:
                    api_msg = errs[0] if isinstance(errs[0], str) else str(errs[0])
            logger.warning(
                "[FECAdapter DEBUG] results_count=%s pagination=%r api_errors_hint=%r",
                len(items),
                pagination,
                api_msg,
            )
            if not items:
                detail = self._fec_detail_line(cred_src, query, query_type, 0)
                empty = self._make_empty_response(query, parse_warning=detail)
                empty.result_hash = raw_hash
                empty.credential_mode = cred_src
                empty.parse_warning = detail
                return empty

            filtered_items: list[dict[str, Any]] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                reason = fec_schedule_a_row_exclusion_reason(item)
                if reason:
                    if reason == "negative_amount":
                        logger.warning(
                            "FEC schedule_a skipping row with negative amount "
                            "(transaction_tp=%r contribution_receipt_amount=%r)",
                            str(item.get("transaction_tp") or "").strip().upper(),
                            item.get("contribution_receipt_amount"),
                        )
                    continue
                filtered_items.append(item)

            if not filtered_items:
                detail = self._fec_detail_line(cred_src, query, query_type, 0)
                detail = f"{detail} | all rows skipped (refunds/negative amounts)"
                empty = self._make_empty_response(query, parse_warning=detail)
                empty.result_hash = raw_hash
                empty.credential_mode = cred_src
                empty.parse_warning = detail
                return empty

            unique_names = {
                str(item.get("contributor_name") or "").lower() for item in filtered_items
            }
            unique_names.discard("")
            collision_count = max(1, len(unique_names))

            ty_val = schedule_a_params_used.get("two_year_transaction_period")
            effective_two_year: int | None = None
            if ty_val is not None:
                try:
                    effective_two_year = int(ty_val)
                except (TypeError, ValueError):
                    effective_two_year = None

            results: list[AdapterResult] = []
            for item in filtered_items:
                amount = item.get("contribution_receipt_amount") or 0
                committee = item.get("committee") or {}
                recipient = (
                    committee.get("name", "Unknown committee")
                    if isinstance(committee, dict)
                    else "Unknown committee"
                )
                raw_date = item.get("contribution_receipt_date")
                date = (
                    str(raw_date).strip()
                    if raw_date is not None and str(raw_date).strip()
                    else ""
                )
                contributor_name = item.get("contributor_name") or ""
                try:
                    amt_f = float(amount)
                except (TypeError, ValueError):
                    amt_f = 0.0

                other = sorted(
                    n for n in unique_names if n != str(contributor_name).lower()
                )[:20]

                if query_type == "committee" or _is_likely_unambiguous(contributor_name):
                    row_collision_count = 1
                    row_collision_set: list[str] = []
                else:
                    row_collision_count = collision_count
                    row_collision_set = other

                # Full row (includes contribution_receipt_date) is stored on evidence;
                # temporal proximity / signal_scorer copy receipt_date into donor_cluster
                # weight_breakdown for calendar rules (e.g. SOFT_BUNDLE_V1).
                committee_obj = item.get("committee") or {}
                ct_val: str | None = None
                if isinstance(committee_obj, dict):
                    ct_val = committee_obj.get("committee_type")
                    if ct_val is not None:
                        ct_val = str(ct_val)
                et_val = str(item.get("entity_type") or "")
                row_data = dict(item)
                row_data["donor_type"] = classify_donor_type(et_val, ct_val)
                if query_type == "committee":
                    committee_id_for_url = (query or "").strip().upper()
                else:
                    _com = item.get("committee")
                    _com = _com if isinstance(_com, dict) else {}
                    committee_id_for_url = str(_com.get("committee_id") or "").strip().upper()
                row_source_url = build_fec_receipt_search_url(
                    item,
                    committee_id=committee_id_for_url,
                    two_year_period=effective_two_year,
                )
                ar = AdapterResult(
                    source_name=self.source_name,
                    source_url=row_source_url,
                    entry_type="financial_connection",
                    title=f"FEC Donation: ${amt_f:,.0f} to {recipient}",
                    body=(
                        f"{contributor_name} donated ${amt_f:,.0f} "
                        f"to {recipient} on {date}."
                    ),
                    date_of_event=date if date else None,
                    amount=amt_f,
                    matched_name=str(contributor_name) or None,
                    collision_count=row_collision_count,
                    collision_set=row_collision_set,
                    raw_data=row_data,
                )
                apply_collision_rule(ar)
                results.append(ar)

            success_detail = self._fec_detail_line(
                cred_src, query, query_type, len(items)
            )
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=results,
                found=True,
                result_hash=raw_hash,
                credential_mode=cred_src,
                parse_warning=success_detail,
            )

        except Exception as e:
            qctx = _fec_schedule_a_query_label(query, query_type)
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error=f"{e!s} | credential_mode={cred_src} | {qctx}",
                error_kind="processing",
                credential_mode=cred_src,
            )

    def _schedule_b_soft_empty(self, cred_src: str, cid: str, note: str) -> AdapterResponse:
        """Non-blocking empty result: never use error Kind — investigate treats Schedule B as optional."""
        return AdapterResponse(
            source_name=self.source_name,
            query=cid,
            results=[],
            found=False,
            error=None,
            error_kind=None,
            credential_mode=cred_src,
            empty_success=True,
            parse_warning=note,
        )

    async def search_schedule_b(self, committee_id: str) -> AdapterResponse:
        """FEC Schedule B — disbursements from the committee (upstream tracing)."""
        cred_src = _fec_key_source_label()
        try:
            api_key = CredentialRegistry.get_credential("fec") or "DEMO_KEY"
        except CredentialUnavailable:
            api_key = "DEMO_KEY"
        cid = (committee_id or "").strip().upper()
        if not cid:
            logger.warning("FEC Schedule B skipped: empty committee_id")
            return self._schedule_b_soft_empty(
                cred_src,
                cid,
                "Schedule B skipped: empty committee_id",
            )
        from datetime import date as _date

        cycle_year = _date.today().year
        params: dict[str, str | int] = {
            "committee_id": cid,
            "api_key": api_key,
            "per_page": 100,
            "sort": "-disbursement_date",
            "sort_hide_null": False,
            "two_year_transaction_period": cycle_year if cycle_year % 2 == 0 else cycle_year + 1,
        }
        api_path = f"{self.BASE_URL}/schedules/schedule_b/"
        source_url = f"https://www.fec.gov/data/disbursements/?committee_id={cid}"
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(api_path, params=params)
            if response.status_code != 200:
                logger.warning(
                    "FEC Schedule B non-200 (committee_id=%s): HTTP %s",
                    cid,
                    response.status_code,
                )
                return self._schedule_b_soft_empty(
                    cred_src,
                    cid,
                    f"Schedule B skipped: HTTP {response.status_code} for committee_id={cid}",
                )
            try:
                data = response.json()
            except Exception as e:
                logger.warning(
                    "FEC Schedule B invalid JSON (committee_id=%s): %s",
                    cid,
                    e,
                )
                return self._schedule_b_soft_empty(
                    cred_src,
                    cid,
                    f"Schedule B skipped: invalid JSON ({e!s})",
                )
            api_err = _fec_interpret_body_api_error(data)
            if api_err:
                _, msg = api_err
                logger.warning(
                    "FEC Schedule B API error body (committee_id=%s): %s",
                    cid,
                    msg,
                )
                return self._schedule_b_soft_empty(
                    cred_src,
                    cid,
                    f"Schedule B skipped: {msg}",
                )
            raw_hash = hashlib.sha256(
                json.dumps(data, sort_keys=True).encode()
            ).hexdigest()
            items = data.get("results") or []
            if not isinstance(items, list):
                items = []
            results: list[AdapterResult] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                amt = item.get("disbursement_amount") or 0
                try:
                    amt_f = float(amt)
                except (TypeError, ValueError):
                    amt_f = 0.0
                raw_dd = item.get("disbursement_date") or ""
                dd = str(raw_dd).strip()[:10] if raw_dd else ""
                rec_name = item.get("recipient_name") or "Unknown recipient"
                memo = item.get("memo_text") or ""
                desc = item.get("disbursement_description") or ""
                ar = AdapterResult(
                    source_name=self.source_name,
                    source_url=source_url,
                    entry_type="fec_disbursement",
                    title=f"FEC Disbursement: ${amt_f:,.0f} to {rec_name}",
                    body=f"Disbursement on {dd}: {desc} {memo}".strip()[:500],
                    date_of_event=dd if dd else None,
                    amount=amt_f,
                    matched_name=str(rec_name) if rec_name else None,
                    raw_data=dict(item),
                    confidence="confirmed",
                )
                apply_collision_rule(ar)
                results.append(ar)
            detail = f"credential_mode={cred_src} | committee_id={cid} | {len(results)} schedule_b rows"
            return AdapterResponse(
                source_name=self.source_name,
                query=cid,
                results=results,
                found=True,
                result_hash=raw_hash,
                credential_mode=cred_src,
                parse_warning=detail,
            )
        except Exception as e:
            logger.warning(
                "FEC Schedule B request failed (committee_id=%s): %s",
                cid,
                e,
            )
            return self._schedule_b_soft_empty(
                cred_src,
                cid,
                f"Schedule B skipped: {e!s}",
            )
