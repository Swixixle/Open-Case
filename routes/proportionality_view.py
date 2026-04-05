"""Optional facility / proportionality preview with geo (pattern report UI)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from services.proportionality_client import fetch_proportionality_packet_sync

router = APIRouter(prefix="/api/v1", tags=["proportionality"])


@router.get("/proportionality/facility-preview")
def proportionality_facility_preview(
    lat: float = Query(..., description="Latitude (WGS84)"),
    lng: float = Query(..., description="Longitude (WGS84)"),
    category: str = Query("political"),
    amount_involved: float | None = Query(None),
) -> dict:
    """
    Proxies EthicalAlt proportionality with coordinates so the HTML report can
    load federal-facility context after an explicit location opt-in.
    """
    pkt = fetch_proportionality_packet_sync(
        category=category,
        violation_type="campaign contribution",
        amount_involved=amount_involved,
        lat=lat,
        lng=lng,
    )
    return {"proportionality": pkt}
