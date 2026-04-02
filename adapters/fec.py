from __future__ import annotations

import hashlib
import json
import logging
import os
import re

import httpx

from adapters.base import AdapterResponse, AdapterResult, BaseAdapter, apply_collision_rule

logger = logging.getLogger(__name__)


def _mask_url(url: str) -> str:
    return re.sub(r"(api_key=)([^&]+)", r"\1***", url, flags=re.IGNORECASE)


class FECAdapter(BaseAdapter):
    source_name = "FEC"
    BASE_URL = "https://api.open.fec.gov/v1"

    async def search(self, query: str, query_type: str = "person") -> AdapterResponse:
        api_key = os.getenv("FEC_API_KEY", "DEMO_KEY")
        key_source = "FEC_API_KEY env" if os.getenv("FEC_API_KEY") else "default DEMO_KEY"
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
                    collision_count=collision_count,
                    collision_set=other,
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
            )

        except Exception as e:
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error=str(e),
            )
