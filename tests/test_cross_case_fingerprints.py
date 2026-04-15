"""
Phase 9A — cross-case donor fingerprint validation (not Phase 9B patterning).

Second senator subject is Jim Banks (R-IN), a sitting U.S. Senator with distinct
committee context from Todd Young — not former Sen. Braun (now Indiana governor).
"""

from __future__ import annotations

import uuid
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from auth import generate_raw_key, hash_key
from adapters.base import AdapterResponse, AdapterResult
from adapters.congress_votes import CongressVotesAdapter
from adapters.fec import FECAdapter
from adapters.indiana_cf import IndianaCFAdapter
from adapters.usa_spending import USASpendingAdapter
from models import (
    CaseContributor,
    CaseFile,
    DonorFingerprint,
    EvidenceEntry,
    Investigator,
    Signal,
)

from tests.test_fec_congress_adapter_fixtures import _stub_empty


def _fec_shared_pac(committee_id: str) -> AdapterResponse:
    return AdapterResponse(
        source_name="FEC",
        query=committee_id,
        results=[
            AdapterResult(
                source_name="FEC",
                source_url="https://www.fec.gov/data/receipts/",
                entry_type="financial_connection",
                title="FEC Donation: $9,000 to Principal Committee",
                body="Shared PAC donation.",
                date_of_event="2025-05-10",
                amount=9000.0,
                matched_name="SHARED INDUSTRY PAC",
                raw_data={
                    "contribution_receipt_date": "2025-05-10",
                    "committee": {"name": "PRINCIPAL COMMITTEE"},
                },
            )
        ],
        found=True,
        credential_mode="ok",
    )


def _congress_votes_for_official(display_name: str) -> AdapterResponse:
    return AdapterResponse(
        source_name=CongressVotesAdapter.source_name,
        query="bioguide",
        results=[
            AdapterResult(
                source_name=CongressVotesAdapter.source_name,
                source_url="https://www.senate.gov/",
                entry_type="vote_record",
                title="Vote: Yea on S. 200 (119th Congress)",
                body="Vote",
                date_of_event="2025-06-01",
                matched_name=display_name,
                raw_data={"subject_is_sponsor": False},
            )
        ],
        found=True,
        credential_mode="ok",
    )


def _seed_two_cases(test_engine):
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    raw_key = generate_raw_key()
    handle = "phase9a_xcase"
    db.add(
        Investigator(
            handle=handle,
            hashed_api_key=hash_key(raw_key),
            public_key="",
        )
    )
    young = CaseFile(
        slug=f"young-{uuid.uuid4().hex[:10]}",
        title="Todd Young validation",
        subject_name="Todd Young",
        subject_type="public_official",
        jurisdiction="IN",
        status="open",
        created_by=handle,
        summary="",
    )
    banks = CaseFile(
        slug=f"banks-{uuid.uuid4().hex[:10]}",
        title="Jim Banks validation",
        subject_name="Jim Banks",
        subject_type="public_official",
        jurisdiction="IN",
        status="open",
        created_by=handle,
        summary="",
    )
    db.add(young)
    db.add(banks)
    db.flush()
    for c in (young, banks):
        db.add(
            CaseContributor(
                case_file_id=c.id,
                investigator_handle=handle,
                role="field",
            )
        )
    db.commit()
    y_id, b_id = young.id, banks.id
    db.close()
    return {
        "young_id": str(y_id),
        "banks_id": str(b_id),
        "api_key": raw_key,
        "handle": handle,
    }


def test_young_then_banks_cross_case_without_mutating_young(
    client, test_engine, monkeypatch
) -> None:
    monkeypatch.setenv("BUST_CACHE", "1")
    ctx = _seed_two_cases(test_engine)
    headers = {"Authorization": f"Bearer {ctx['api_key']}"}

    async def resolve_principal(subject_name: str, jurisdiction: str, **_kwargs):
        _ = jurisdiction
        if "Young" in (subject_name or ""):
            return "C00459255"
        if "Banks" in (subject_name or ""):
            return "C00728156"
        return None

    async def fec_search(query: str, query_type: str = "person", **_kw):
        assert query_type == "committee"
        return _fec_shared_pac(query)

    async def congress_search_mock(query: str, query_type: str = "bioguide_id"):
        """Patched bound method is invoked as (query, query_type) — no implicit self."""
        del query_type
        bg = (query or "").strip().upper()
        if bg == "Y000064":
            return _congress_votes_for_official("Todd Young")
        if bg == "B001306":
            return _congress_votes_for_official("Jim Banks")
        return _congress_votes_for_official("Unknown")

    patches = (
        patch(
            "routes.investigate.resolve_principal_committee_id_for_official",
            new_callable=AsyncMock,
            side_effect=resolve_principal,
        ),
        patch.object(FECAdapter, "search", new_callable=AsyncMock, side_effect=fec_search),
        patch.object(
            USASpendingAdapter,
            "search",
            new_callable=AsyncMock,
            return_value=_stub_empty("USASpending"),
        ),
        patch.object(
            IndianaCFAdapter,
            "search",
            new_callable=AsyncMock,
            return_value=_stub_empty("Indiana Campaign Finance"),
        ),
        patch.object(
            CongressVotesAdapter,
            "search",
            new=AsyncMock(side_effect=congress_search_mock),
        ),
    )

    def _counts(case_uuid: str) -> tuple[int, int, int]:
        Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
        dbs = Session()
        try:
            cid = uuid.UUID(case_uuid)
            ev = (
                dbs.scalar(
                    select(func.count()).select_from(EvidenceEntry).where(
                        EvidenceEntry.case_file_id == cid
                    )
                )
                or 0
            )
            fp = (
                dbs.scalar(
                    select(func.count()).select_from(DonorFingerprint).where(
                        DonorFingerprint.case_file_id == cid
                    )
                )
                or 0
            )
            sig = (
                dbs.scalar(
                    select(func.count()).select_from(Signal).where(
                        Signal.case_file_id == cid
                    )
                )
                or 0
            )
            return int(ev), int(sig), int(fp)
        finally:
            dbs.close()

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        r1 = client.post(
            f"/api/v1/cases/{ctx['young_id']}/investigate",
            json={
                "subject_name": "Todd Young",
                "investigator_handle": ctx["handle"],
                "bioguide_id": "Y000064",
            },
            headers=headers,
        )
    assert r1.status_code == 200, r1.text
    ev1, sig1, fp1 = _counts(ctx["young_id"])
    assert ev1 > 0 and sig1 > 0

    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    dq = Session()
    try:
        evotes = (
            dq.scalars(
                select(EvidenceEntry).where(
                    EvidenceEntry.case_file_id == uuid.UUID(ctx["young_id"]),
                    EvidenceEntry.entry_type == "vote_record",
                )
            )
            .all()
        )
        assert any(
            "young" in (e.matched_name or "").lower() for e in evotes
        ), (
            "vote matched_name unexpected: "
            f"{[(e.matched_name, e.adapter_name, e.title[:40]) for e in evotes]}"
        )
    finally:
        dq.close()

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        r2 = client.post(
            f"/api/v1/cases/{ctx['banks_id']}/investigate",
            json={
                "subject_name": "Jim Banks",
                "investigator_handle": ctx["handle"],
                "bioguide_id": "B001306",
            },
            headers=headers,
        )
    assert r2.status_code == 200, r2.text
    data = r2.json()
    hits = [
        s
        for s in data.get("signals") or []
        if int(s.get("cross_case_appearances") or 0) >= 1
    ]
    assert hits, "expected at least one Banks signal with cross_case_appearances >= 1"
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    dbs = Session()
    try:
        yfps = (
            dbs.scalars(
                select(DonorFingerprint).where(
                    DonorFingerprint.case_file_id == uuid.UUID(ctx["young_id"])
                )
            )
            .all()
        )
        assert yfps, "Young case should persist donor fingerprint rows"
        assert any("young" in (f.official_name or "").lower() for f in yfps)
        b_sig = (
            dbs.scalars(
                select(Signal).where(Signal.case_file_id == uuid.UUID(ctx["banks_id"]))
            )
            .first()
        )
        assert b_sig is not None
        assert (b_sig.cross_case_appearances or 0) >= 1
    finally:
        dbs.close()

    ev1_after, sig1_after, fp1_after = _counts(ctx["young_id"])
    assert ev1_after == ev1
    assert sig1_after == sig1
    assert fp1_after == fp1
