from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utc_iso_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class AdapterResult:
    """Single result from any adapter."""

    source_name: str
    source_url: str
    entry_type: str
    title: str
    body: str
    date_of_event: str | None = None
    amount: float | None = None
    confidence: str = "confirmed"
    raw_data: dict[str, Any] = field(default_factory=dict)
    matched_name: str | None = None
    collision_count: int = 0
    collision_set: list[str] = field(default_factory=list)
    is_absence: bool = False


@dataclass
class AdapterResponse:
    """Full response from any adapter."""

    source_name: str
    query: str
    results: list[AdapterResult]
    found: bool
    error: str | None = None
    retrieved_at: str = field(default_factory=_utc_iso_z)
    result_hash: str = ""
    parse_warning: str | None = None
    # Credential pipeline: ok | fallback | credential_unavailable | skipped
    credential_mode: str | None = None
    # Fetch completed without transport error but zero actionable rows (honest empty).
    empty_success: bool = False
    # When error is set: network | processing | credential | rate_limited (source_statuses)
    error_kind: str | None = None


class BaseAdapter:
    """All adapters inherit from this."""

    source_name: str = ""

    async def search(self, query: str, query_type: str = "person") -> AdapterResponse:
        raise NotImplementedError

    def _make_empty_response(
        self,
        query: str,
        error: str | None = None,
        parse_warning: str | None = None,
    ) -> AdapterResponse:
        return AdapterResponse(
            source_name=self.source_name,
            query=query,
            results=[],
            found=True,
            error=error,
            parse_warning=parse_warning,
        )


def apply_collision_rule(r: AdapterResult) -> None:
    """If multiple entities matched the name search, force unverified (defamation guard)."""
    if r.collision_count > 1:
        r.confidence = "unverified"
