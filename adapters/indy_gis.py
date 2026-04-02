from __future__ import annotations

import hashlib
import json

import httpx

from adapters.base import AdapterResponse, AdapterResult, BaseAdapter, apply_collision_rule


class IndyGISAdapter(BaseAdapter):
    source_name = "IndyGIS / MapIndy"
    BASE_URL = "https://maps.indy.gov/arcgis/rest/services/Parcel/MapServer/0/query"

    async def search(self, query: str, query_type: str = "address") -> AdapterResponse:
        try:
            safe = query.replace("'", "''").upper()
            params = {
                "where": f"ADDRESS LIKE '%{safe}%'",
                "outFields": "PARCEL_NUM,ADDRESS,OWNER_NAME,ASSESSED_VALUE,CITY,STATE",
                "returnGeometry": "false",
                "f": "json",
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(self.BASE_URL, params=params)
                data = response.json()

            features = data.get("features", [])
            raw_hash = hashlib.sha256(
                json.dumps(data, sort_keys=True).encode()
            ).hexdigest()

            if not features:
                empty = self._make_empty_response(query)
                empty.result_hash = raw_hash
                return empty

            collision = len(features)
            owners = []
            for f in features:
                nm = (f.get("attributes") or {}).get("OWNER_NAME")
                if nm and str(nm) not in owners:
                    owners.append(str(nm))

            results: list[AdapterResult] = []
            for feature in features[:5]:
                attrs = feature.get("attributes") or {}
                other_owners = [o for o in owners if o != str(attrs.get("OWNER_NAME") or "")]
                val = attrs.get("ASSESSED_VALUE") or 0
                try:
                    val_f = float(val)
                except (TypeError, ValueError):
                    val_f = 0.0
                ar = AdapterResult(
                    source_name=self.source_name,
                    source_url="https://www.indy.gov/activity/access-property-records",
                    entry_type="property_record",
                    title=f"Property: {attrs.get('ADDRESS', 'Unknown')}",
                    body=(
                        f"Parcel {attrs.get('PARCEL_NUM')} at "
                        f"{attrs.get('ADDRESS')}. "
                        f"Owner: {attrs.get('OWNER_NAME')}. "
                        f"Assessed value: ${val_f:,.0f}."
                    ),
                    matched_name=str(attrs.get("OWNER_NAME") or "") or None,
                    collision_count=collision,
                    collision_set=other_owners[:20],
                    raw_data=dict(attrs),
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
