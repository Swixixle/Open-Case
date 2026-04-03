from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any

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


async def resolve_principal_committee_id_for_official(
    subject_name: str,
    jurisdiction: str,
) -> str | None:
    """
    OpenFEC principal campaign committee for a named federal candidate.

    Used when investigating a public official without an explicit fec_committee_id:
    schedule_a by committee_id returns receipts *to* that committee (donors to pair
    with votes). schedule_a by contributor_name searches the wrong economic direction
    for this use case.
    """
    try:
        api_key = CredentialRegistry.get_credential("fec") or "DEMO_KEY"
    except CredentialUnavailable:
        api_key = "DEMO_KEY"

    search_name = (subject_name or "").strip()
    if not search_name:
        return None

    state_filter: str | None = None
    j = (jurisdiction or "").strip().upper()
    if len(j) == 2 and j.isalpha():
        state_filter = j

    cred_src = _fec_key_source_label()
    api = f"{FECAdapter.BASE_URL}{_CANDIDATE_SEARCH_PATH}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        for office in ("S", "H"):
            params: dict[str, str | int] = {
                "api_key": api_key,
                "name": search_name,
                "office": office,
                "per_page": 20,
                "sort": "-last_file_date",
            }
            try:
                r = await client.get(api, params=params)
            except httpx.HTTPError:
                continue
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
            for cand in data.get("results") or []:
                if not isinstance(cand, dict):
                    continue
                if state_filter and str(cand.get("state") or "").upper() != state_filter:
                    continue
                if cand.get("candidate_inactive") is True:
                    continue
                committees = cand.get("principal_committees") or []
                for pc in committees:
                    if not isinstance(pc, dict):
                        continue
                    cid = pc.get("committee_id")
                    if not cid:
                        continue
                    des_full = str(pc.get("designation_full") or "")
                    if "Principal" in des_full or pc.get("designation") == "P":
                        return str(cid).strip().upper()
                for pc in committees:
                    if isinstance(pc, dict) and pc.get("committee_id"):
                        return str(pc["committee_id"]).strip().upper()
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

    async def search(self, query: str, query_type: str = "person") -> AdapterResponse:
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
                source_url = (
                    f"https://www.fec.gov/data/receipts/?committee_id={query}"
                )
            else:
                params = {
                    "contributor_name": query,
                    "sort_hide_null": True,
                    "per_page": 20,
                    "api_key": api_key,
                }
                source_url = (
                    "https://www.fec.gov/data/receipts/"
                    f"?contributor_name={query.replace(' ', '+')}"
                )

            api_path = f"{self.BASE_URL}/schedules/schedule_a/"
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(api_path, params=params)

            req_url = _mask_url(str(response.request.url))
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
                ar = AdapterResult(
                    source_name=self.source_name,
                    source_url=source_url,
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
                    raw_data=dict(item),
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
