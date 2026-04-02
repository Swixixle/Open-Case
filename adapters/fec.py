from __future__ import annotations

import hashlib
import json
import logging
import re

import httpx

from adapters.base import AdapterResponse, AdapterResult, BaseAdapter, apply_collision_rule
from core.credentials import CredentialRegistry, CredentialUnavailable

logger = logging.getLogger(__name__)

# Donor shapes that are very unlikely to be accidental multi-entity personal-name matches.
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


def _mask_url(url: str) -> str:
    return re.sub(r"(api_key=)([^&]+)", r"\1***", url, flags=re.IGNORECASE)


class FECAdapter(BaseAdapter):
    source_name = "FEC"
    BASE_URL = "https://api.open.fec.gov/v1"

    async def search(self, query: str, query_type: str = "person") -> AdapterResponse:
        key_from_env = bool(CredentialRegistry.get_adapter_status("fec")["key_present"])
        try:
            api_key = CredentialRegistry.get_credential("fec") or "DEMO_KEY"
        except CredentialUnavailable:
            api_key = "DEMO_KEY"
        cred_mode = "ok" if key_from_env else "fallback"
        key_source = "FEC_API_KEY env" if key_from_env else "registry_fallback"
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

            try:
                data = response.json()
            except Exception as e:
                text = (response.text[:800] + "…") if len(response.text) > 800 else response.text
                logger.warning(
                    "[FECAdapter DEBUG] JSON parse failed: %s body_preview=%r",
                    e,
                    text,
                )
                raise

            raw_hash = hashlib.sha256(
                json.dumps(data, sort_keys=True).encode()
            ).hexdigest()

            items = data.get("results", [])
            pagination = data.get("pagination", {}) if isinstance(data, dict) else {}
            api_msg = None
            if isinstance(data, dict):
                errs = data.get("errors")
                if isinstance(errs, list) and errs:
                    api_msg = errs[0] if isinstance(errs[0], str) else str(errs[0])
            logger.warning(
                "[FECAdapter DEBUG] results_count=%s pagination=%r api_errors_hint=%r",
                len(items) if isinstance(items, list) else "n/a",
                pagination,
                api_msg,
            )
            if not items:
                empty = self._make_empty_response(query)
                empty.result_hash = raw_hash
                empty.credential_mode = cred_mode
                return empty

            unique_names = {
                str(item.get("contributor_name") or "").lower() for item in items
            }
            unique_names.discard("")
            collision_count = max(1, len(unique_names))

            results: list[AdapterResult] = []
            for item in items:
                amount = item.get("contribution_receipt_amount") or 0
                committee = item.get("committee") or {}
                recipient = (
                    committee.get("name", "Unknown committee")
                    if isinstance(committee, dict)
                    else "Unknown committee"
                )
                date = item.get("contribution_receipt_date") or ""
                contributor_name = item.get("contributor_name") or ""
                try:
                    amt_f = float(amount)
                except (TypeError, ValueError):
                    amt_f = 0.0

                other = sorted(
                    n for n in unique_names if n != str(contributor_name).lower()
                )[:20]

                # Committee_id queries return many unrelated donors on one page; that is not
                # per-row ambiguity. Corporate/PAC-style names are treated as unambiguous.
                if query_type == "committee" or _is_likely_unambiguous(contributor_name):
                    row_collision_count = 1
                    row_collision_set: list[str] = []
                else:
                    row_collision_count = collision_count
                    row_collision_set = other

                ar = AdapterResult(
                    source_name=self.source_name,
                    source_url=source_url,
                    entry_type="financial_connection",
                    title=f"FEC Donation: ${amt_f:,.0f} to {recipient}",
                    body=(
                        f"{contributor_name} donated ${amt_f:,.0f} "
                        f"to {recipient} on {date}."
                    ),
                    date_of_event=str(date)[:10] if date else None,
                    amount=amt_f,
                    matched_name=str(contributor_name) or None,
                    collision_count=row_collision_count,
                    collision_set=row_collision_set,
                    raw_data=dict(item),
                )
                apply_collision_rule(ar)
                results.append(ar)

            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=results,
                found=True,
                result_hash=raw_hash,
                credential_mode=cred_mode,
            )

        except Exception as e:
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error=str(e),
                credential_mode=cred_mode,
            )
