from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any


def _bust_cache_enabled() -> bool:
    return os.getenv("BUST_CACHE", "").strip().lower() in ("1", "true", "yes")

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from adapters.base import AdapterResponse, AdapterResult
from models import AdapterCache


def make_cache_key(adapter_name: str, query_string: str) -> str:
    return hashlib.sha256(f"{adapter_name}:{query_string}".encode()).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def get_cached_raw_json(
    db: Session,
    adapter_name: str,
    query_string: str,
) -> dict[str, Any] | None:
    """Return any JSON object from AdapterCache (not limited to AdapterResponse shape)."""
    if _bust_cache_enabled():
        return None
    cache_key = make_cache_key(adapter_name, query_string)
    now = _utc_now()
    row = db.scalar(
        select(AdapterCache).where(
            AdapterCache.adapter_name == adapter_name,
            AdapterCache.query_hash == cache_key,
            AdapterCache.expires_at > now,
        )
    )
    if not row:
        return None
    try:
        data = json.loads(row.response_json)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def store_cached_raw_json(
    db: Session,
    adapter_name: str,
    query_string: str,
    data: dict[str, Any],
    ttl_hours: int,
) -> None:
    """Store an arbitrary JSON-serializable dict in AdapterCache."""
    cache_key = make_cache_key(adapter_name, query_string)
    now = _utc_now()
    expires_at = now + timedelta(hours=ttl_hours)
    db.execute(
        delete(AdapterCache).where(
            AdapterCache.adapter_name == adapter_name,
            AdapterCache.query_hash == cache_key,
        )
    )
    db.add(
        AdapterCache(
            adapter_name=adapter_name,
            query_hash=cache_key,
            response_json=json.dumps(data, sort_keys=True, default=str),
            created_at=now,
            expires_at=expires_at,
            ttl_hours=ttl_hours,
            query_string=query_string,
        )
    )
    db.flush()


def get_cached_response(
    db: Session,
    adapter_name: str,
    query_string: str,
) -> dict[str, Any] | None:
    if _bust_cache_enabled():
        return None

    cache_key = make_cache_key(adapter_name, query_string)
    now = _utc_now()
    row = db.scalar(
        select(AdapterCache).where(
            AdapterCache.adapter_name == adapter_name,
            AdapterCache.query_hash == cache_key,
            AdapterCache.expires_at > now,
        )
    )
    if not row:
        return None
    try:
        return json.loads(row.response_json)
    except json.JSONDecodeError:
        return None


def response_from_cache_dict(d: dict[str, Any]) -> AdapterResponse:
    results: list[AdapterResult] = []
    for r in d.get("results") or []:
        if not isinstance(r, dict):
            continue
        results.append(
            AdapterResult(
                source_name=r.get("source_name", ""),
                source_url=r.get("source_url", ""),
                entry_type=r.get("entry_type", ""),
                title=r.get("title", ""),
                body=r.get("body", ""),
                date_of_event=r.get("date_of_event"),
                amount=r.get("amount"),
                confidence=r.get("confidence", "confirmed"),
                raw_data=r.get("raw_data") or {},
                matched_name=r.get("matched_name"),
                collision_count=int(r.get("collision_count") or 0),
                collision_set=list(r.get("collision_set") or []),
                is_absence=bool(r.get("is_absence", False)),
            )
        )
    return AdapterResponse(
        source_name=d.get("source_name", ""),
        query=d.get("query", ""),
        results=results,
        found=bool(d.get("found", True)),
        error=d.get("error"),
        retrieved_at=d.get("retrieved_at") or "",
        result_hash=d.get("result_hash") or "",
        parse_warning=d.get("parse_warning"),
        credential_mode=d.get("credential_mode"),
        empty_success=bool(d.get("empty_success", False)),
        error_kind=d.get("error_kind"),
    )


def store_cached_response(
    db: Session,
    adapter_name: str,
    query_string: str,
    response: AdapterResponse,
    ttl_hours: int = 4,
) -> None:
    cache_key = make_cache_key(adapter_name, query_string)
    now = _utc_now()
    expires_at = now + timedelta(hours=ttl_hours)

    try:
        response_dict: dict[str, Any] = {
            "source_name": response.source_name,
            "query": response.query,
            "found": response.found,
            "error": response.error,
            "retrieved_at": response.retrieved_at,
            "result_hash": response.result_hash,
            "parse_warning": response.parse_warning,
            "credential_mode": response.credential_mode,
            "empty_success": response.empty_success,
            "error_kind": response.error_kind,
            "results": [
                {
                    "source_name": r.source_name,
                    "source_url": r.source_url,
                    "entry_type": r.entry_type,
                    "title": r.title,
                    "body": r.body,
                    "date_of_event": r.date_of_event,
                    "amount": r.amount,
                    "confidence": r.confidence,
                    "matched_name": r.matched_name,
                    "collision_count": r.collision_count,
                    "collision_set": r.collision_set,
                    "raw_data": r.raw_data,
                    "is_absence": getattr(r, "is_absence", False),
                }
                for r in response.results
            ],
        }
    except Exception:
        return

    db.execute(
        delete(AdapterCache).where(
            AdapterCache.adapter_name == adapter_name,
            AdapterCache.query_hash == cache_key,
        )
    )
    db.add(
        AdapterCache(
            adapter_name=adapter_name,
            query_hash=cache_key,
            response_json=json.dumps(response_dict, sort_keys=True, default=str),
            created_at=now,
            expires_at=expires_at,
            ttl_hours=ttl_hours,
            query_string=query_string,
        )
    )
    db.flush()


def flush_adapter_cache(
    db: Session,
    adapter_names: list[str] | None = None,
) -> int:
    """Delete cached adapter responses. If adapter_names is None, clears all cache rows."""
    if adapter_names:
        result = db.execute(
            delete(AdapterCache).where(AdapterCache.adapter_name.in_(adapter_names))
        )
    else:
        result = db.execute(delete(AdapterCache))
    db.flush()
    return getattr(result, "rowcount", 0) or 0
