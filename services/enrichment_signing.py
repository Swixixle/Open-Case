"""Build deterministic enrichment receipt bodies and pack Ed25519 signed blobs."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from signing import pack_signed_hash, sign_payload

# Structured finding row (each item in ``findings`` passed to signing):
# claim, type ("fact"|"pattern"|"allegation"), allegation_status (optional),
# sources (list of URLs), time_span, confidence ("low"|"medium"|"high"),
# needs_human_review (bool).

STANDARD_DISCLAIMER = (
    "This enrichment documents public records and automated pattern analysis. "
    "Patterns do not prove causation or wrongdoing. "
    "All findings are for further human review and verification. "
    "Allegations are distinguished from official findings throughout."
)


def _queried_at_iso(queried_at: datetime) -> str:
    if queried_at.tzinfo is None:
        dt = queried_at.replace(tzinfo=timezone.utc)
    else:
        dt = queried_at.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def sorted_findings_for_signing(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable order for JCS hashing."""
    return sorted(
        findings,
        key=lambda f: (
            str(f.get("claim") or ""),
            str(f.get("type") or ""),
            ",".join(str(u) for u in (f.get("sources") or []) if u),
        ),
    )


def claims_to_findings(phase_1_claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map Perplexity phase-1 objects into FINDING_SCHEMA rows; enforce source/confidence rules."""
    out: list[dict[str, Any]] = []
    for raw in phase_1_claims:
        if not isinstance(raw, dict):
            continue
        claim = str(raw.get("claim") or "").strip()
        if not claim:
            continue
        src = raw.get("source")
        sources: list[str] = []
        if isinstance(src, str) and src.strip():
            sources = [src.strip()]
        elif isinstance(src, list):
            sources = [str(u).strip() for u in src if str(u).strip()]
        typ = str(raw.get("type") or "fact").lower()
        if typ not in ("fact", "pattern", "allegation"):
            typ = "fact"
        allegation_status: str | None = None
        if typ == "allegation":
            allegation_status = "unknown"
        time_span = str(raw.get("date") or "").strip()[:64]
        if not time_span and raw.get("amount") is not None:
            time_span = str(raw.get("amount"))
        missing_sources = not sources
        confidence = "low" if missing_sources else "medium"
        needs_human_review = missing_sources
        out.append(
            {
                "claim": claim,
                "type": typ,
                "allegation_status": allegation_status,
                "sources": sources,
                "time_span": time_span,
                "confidence": confidence,
                "needs_human_review": needs_human_review,
            }
        )
    return out


def sign_enrichment_receipt(
    subject_name: str,
    bioguide_id: str | None,
    queried_at: datetime,
    findings: list[dict[str, Any]],
    new_findings_count: int,
    *,
    narrative: str = "",
    needs_human_review: bool = False,
    disclaimer: str = STANDARD_DISCLAIMER,
) -> str:
    body: dict[str, Any] = {
        "subject_name": subject_name,
        "bioguide_id": bioguide_id,
        "queried_at": _queried_at_iso(queried_at),
        "disclaimer": disclaimer,
        "findings": sorted_findings_for_signing(findings),
        "narrative": narrative,
        "needs_human_review": bool(needs_human_review),
        "new_findings_count": new_findings_count,
    }
    signed = sign_payload(body)
    return pack_signed_hash(
        signed["content_hash"],
        signed["signature"],
        payload=body,
    )
