"""HTTP client for EthicalAlt `/proportionality` (shared with EthicalAlt; do not duplicate rule logic here)."""

from __future__ import annotations

import os
from typing import Any

import httpx

PROPORTIONALITY_BASE = os.getenv(
    "PROPORTIONALITY_API_URL",
    "https://ethicalalt-api.onrender.com",
)


def _external_proportionality_disabled() -> bool:
    """When set (e.g. in CI), skip HTTP so tests and seals do not call EthicalAlt."""
    return os.getenv("SKIP_EXTERNAL_PROPORTIONALITY", "").lower() in (
        "1",
        "true",
        "yes",
    )


def _params(
    category: str,
    violation_type: str | None = None,
    charge_status: str | None = None,
    amount_involved: float | None = None,
    lat: float | None = None,
    lng: float | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"category": category}
    if violation_type:
        params["violation_type"] = violation_type
    if charge_status:
        params["charge_status"] = charge_status
    if amount_involved is not None:
        params["amount_involved"] = amount_involved
    if lat is not None:
        params["lat"] = lat
    if lng is not None:
        params["lng"] = lng
    return params


async def fetch_proportionality_packet(
    category: str,
    violation_type: str | None = None,
    charge_status: str | None = None,
    amount_involved: float | None = None,
    lat: float | None = None,
    lng: float | None = None,
) -> dict[str, Any] | None:
    if _external_proportionality_disabled():
        return None
    params = _params(
        category,
        violation_type=violation_type,
        charge_status=charge_status,
        amount_involved=amount_involved,
        lat=lat,
        lng=lng,
    )
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(
                f"{PROPORTIONALITY_BASE.rstrip('/')}/proportionality",
                params=params,
            )
            if res.status_code == 200:
                data = res.json()
                pkt = data.get("proportionality")
                return pkt if isinstance(pkt, dict) else None
    except Exception:
        pass
    return None


def fetch_proportionality_packet_sync(
    category: str,
    violation_type: str | None = None,
    charge_status: str | None = None,
    amount_involved: float | None = None,
    lat: float | None = None,
    lng: float | None = None,
) -> dict[str, Any] | None:
    """Sync variant for signing paths and non-async routes."""
    if _external_proportionality_disabled():
        return None
    params = _params(
        category,
        violation_type=violation_type,
        charge_status=charge_status,
        amount_involved=amount_involved,
        lat=lat,
        lng=lng,
    )
    try:
        with httpx.Client(timeout=5.0) as client:
            res = client.get(
                f"{PROPORTIONALITY_BASE.rstrip('/')}/proportionality",
                params=params,
            )
            if res.status_code == 200:
                data = res.json()
                pkt = data.get("proportionality")
                return pkt if isinstance(pkt, dict) else None
    except Exception:
        pass
    return None
