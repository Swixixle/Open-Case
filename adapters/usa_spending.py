from __future__ import annotations

import hashlib
import json

import httpx

from adapters.base import AdapterResponse, AdapterResult, BaseAdapter, apply_collision_rule


class USASpendingAdapter(BaseAdapter):
    source_name = "USASpending"
    BASE_URL = "https://api.usaspending.gov/api/v2"

    async def search(self, query: str, query_type: str = "entity") -> AdapterResponse:
        try:
            payload = {
                "filters": {
                    "recipient_search_text": [query],
                    "award_type_codes": ["A", "B", "C", "D"],
                },
                "fields": [
                    "Award ID",
                    "Recipient Name",
                    "Award Amount",
                    "Start Date",
                    "End Date",
                    "Awarding Agency",
                    "Description",
                    "Type of Contract Pricing",
                    "Number of Offers Received",
                ],
                "page": 1,
                "limit": 20,
                "sort": "Award Amount",
                "order": "desc",
            }

            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    f"{self.BASE_URL}/search/spending_by_award/",
                    json=payload,
                )
                data = response.json()

            raw_hash = hashlib.sha256(
                json.dumps(data, sort_keys=True).encode()
            ).hexdigest()

            items = data.get("results", [])
            if not items:
                empty = self._make_empty_response(query)
                empty.result_hash = raw_hash
                return empty

            unique_recipients = {
                str(item.get("Recipient Name") or "").lower() for item in items
            }
            unique_recipients.discard("")
            collision_count = max(1, len(unique_recipients))

            results: list[AdapterResult] = []
            for item in items:
                try:
                    amount = float(item.get("Award Amount") or 0)
                except (TypeError, ValueError):
                    amount = 0.0
                agency = item.get("Awarding Agency", "Unknown agency")
                recipient = item.get("Recipient Name", "") or ""
                start_date = item.get("Start Date", "") or ""
                num_offers = item.get("Number of Offers Received")
                try:
                    n_off = int(num_offers) if num_offers is not None else None
                except (TypeError, ValueError):
                    n_off = None
                is_no_bid = n_off is not None and n_off <= 1

                anomaly_flags: list[str] = []
                if is_no_bid:
                    anomaly_flags.append("NO-BID CONTRACT")
                if self._is_just_below_threshold(amount):
                    anomaly_flags.append("VALUE JUST BELOW OVERSIGHT THRESHOLD")

                flag_text = f" ⚑ {', '.join(anomaly_flags)}" if anomaly_flags else ""

                other = sorted(
                    n for n in unique_recipients if n != str(recipient).lower()
                )[:20]

                ar = AdapterResult(
                    source_name=self.source_name,
                    source_url=f"https://www.usaspending.gov/search/?query={query}",
                    entry_type="financial_connection",
                    title=f"Federal Contract: ${amount:,.0f} from {agency}{flag_text}",
                    body=(
                        f"{recipient} received a ${amount:,.0f} contract "
                        f"from {agency} starting {start_date}. "
                        f"Offers received: {n_off if n_off is not None else 'not disclosed'}."
                        f"{flag_text}"
                    ),
                    date_of_event=str(start_date)[:10] if start_date else None,
                    amount=amount,
                    matched_name=str(recipient) or None,
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

    @staticmethod
    def _is_just_below_threshold(amount: float) -> bool:
        thresholds = [25000, 150000, 750000, 10000000]
        for threshold in thresholds:
            if threshold * 0.92 < amount < threshold:
                return True
        return False
