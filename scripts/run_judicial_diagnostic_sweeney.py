#!/usr/bin/env python3
"""
Diagnostic: James R. Sweeney II, U.S. District Court, S.D. Indiana.

Creates CaseFile + SubjectProfile, runs full investigate, logs evidence/patterns/receipt.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger("sweeney_diagnostic")

# FJC History & Biography — https://www.fjc.gov/history/judges/sweeney-james-russell-ii
FJC_SLUG = "sweeney-james-russell-ii"
FJC_URL = f"https://www.fjc.gov/history/judges/{FJC_SLUG}"

HANDLE = "diagnostic_sweeney"
SUBJECT_NAME = "James R. Sweeney II"


def _slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower().strip())
    return (base.strip("-") or "judge")[:200]


async def main() -> int:
    from auth import hash_key
    from database import SessionLocal, init_db
    from engines.pattern_engine import pattern_alerts_for_case, run_pattern_engine
    from models import CaseContributor, CaseFile, EvidenceEntry, Investigator, SubjectProfile
    from payloads import apply_case_file_signature
    from routes.investigate import InvestigateRequest, execute_investigation_for_case
    from sqlalchemy import select
    from starlette.responses import JSONResponse

    init_db()
    db = SessionLocal()
    try:
        inv = db.scalar(select(Investigator).where(Investigator.handle == HANDLE))
        if not inv:
            inv = Investigator(
                handle=HANDLE,
                hashed_api_key=hash_key("open_case_diagnostic_placeholder_not_for_auth"),
                public_key="",
            )
            db.add(inv)
            db.flush()
            db.commit()
            logger.info("Created investigator handle=%s", HANDLE)

        slug = f"judicial-sd-in-{_slug(SUBJECT_NAME)}"
        existing = db.scalar(select(CaseFile).where(CaseFile.slug == slug))
        if existing:
            case = existing
            logger.info("Reusing existing case id=%s slug=%s", case.id, slug)
            prof = db.scalar(
                select(SubjectProfile).where(SubjectProfile.case_file_id == case.id)
            )
            if prof:
                prof.subject_type = "federal_judge_district"
                prof.government_level = "federal"
                prof.branch = "judicial"
                prof.historical_depth = "full"
                prof.updated_by = HANDLE
            else:
                db.add(
                    SubjectProfile(
                        case_file_id=case.id,
                        subject_name=SUBJECT_NAME,
                        subject_type="federal_judge_district",
                        government_level="federal",
                        branch="judicial",
                        historical_depth="full",
                        office="district_judge",
                        updated_by=HANDLE,
                    )
                )
            case.subject_type = "federal_judge_district"
            case.government_level = "federal"
            case.branch = "judicial"
            case.pilot_cohort = "indianapolis"
            db.commit()
        else:
            summary = "\n".join(
                [
                    "Diagnostic judicial subject — S.D. Indiana.",
                    f"FJC biographical: {FJC_URL}",
                    "Nominated by President Trump November 1, 2017; confirmed by Senate August 28, 2018; "
                    "commission September 13, 2018.",
                    "Court: U.S. District Court for the Southern District of Indiana.",
                ]
            )
            case = CaseFile(
                slug=slug,
                title=f"{SUBJECT_NAME} — S.D. Indiana",
                subject_name=SUBJECT_NAME,
                subject_type="federal_judge_district",
                jurisdiction="U.S. District Court, Southern District of Indiana",
                status="open",
                created_by=HANDLE,
                summary=summary,
                government_level="federal",
                branch="judicial",
                pilot_cohort="indianapolis",
            )
            db.add(case)
            db.flush()
            db.add(
                CaseContributor(
                    case_file_id=case.id,
                    investigator_handle=HANDLE,
                    role="originator",
                )
            )
            prof = SubjectProfile(
                case_file_id=case.id,
                subject_name=SUBJECT_NAME,
                subject_type="federal_judge_district",
                government_level="federal",
                branch="judicial",
                historical_depth="full",
                office="district_judge",
                updated_by=HANDLE,
            )
            db.add(prof)
            db.commit()
            db.refresh(case)
            logger.info("Created case_id=%s slug=%s", case.id, slug)

        case_id = case.id

        req = InvestigateRequest(
            subject_name=SUBJECT_NAME,
            investigator_handle=HANDLE,
            address=None,
            bioguide_id=None,
            proximity_days=90,
            fec_committee_id=None,
        )

        logger.info("Starting investigate for case_id=%s ...", case_id)
        result = await execute_investigation_for_case(
            db,
            case_id,
            req,
            background_tasks=None,
            include_unresolved=False,
            debug=True,
        )
        if isinstance(result, JSONResponse):
            body = result.body.decode() if result.body else "{}"
            logger.error("Investigate failed HTTP %s: %s", result.status_code, body[:2000])
            return 3

        logger.info(
            "Investigate OK: evidence_entries_created=%s errors=%s",
            result.get("evidence_entries_created"),
            result.get("errors"),
        )
        planned = [
            s
            for s in (result.get("source_statuses") or [])
            if isinstance(s, dict) and s.get("status") == "planned_stub"
        ]
        logger.info("Planned adapters (stub) logged this run: %s", len(planned))
        for p in planned[:40]:
            logger.info(
                "  planned_stub adapter=%s detail=%s",
                p.get("adapter"),
                (p.get("detail") or "")[:120],
            )
        if len(planned) > 40:
            logger.info("  ... %s more", len(planned) - 40)

        # Post-investigate snapshot: counts by source_name and entry_type
        db.expire_all()
        entries = db.scalars(
            select(EvidenceEntry).where(EvidenceEntry.case_file_id == case_id)
        ).all()
        by_source = Counter((e.source_name or "").strip() or "(empty)" for e in entries)
        by_type = Counter((e.entry_type or "").strip() for e in entries)
        by_adapter = Counter((e.adapter_name or "").strip() or "(none)" for e in entries)

        logger.info("Evidence rows total=%s", len(entries))
        logger.info("By source_name: %s", dict(by_source.most_common(30)))
        logger.info("By entry_type: %s", dict(by_type.most_common()))
        logger.info("By adapter_name: %s", dict(by_adapter.most_common(20)))

        alerts = run_pattern_engine(db)
        case_alerts = pattern_alerts_for_case(case_id, alerts, include_unreviewed=True)
        logger.info("Pattern alerts for this case: %s", len(case_alerts))
        for a in case_alerts[:15]:
            logger.info(
                "  alert rule_id=%s donor=%s epistemic=%s review=%s",
                a.get("rule_id"),
                (a.get("donor_entity") or "")[:60],
                a.get("epistemic_level"),
                a.get("requires_human_review"),
            )

        entries_for_seal = db.scalars(
            select(EvidenceEntry).where(EvidenceEntry.case_file_id == case_id)
        ).all()
        c2 = db.scalar(select(CaseFile).where(CaseFile.id == case_id))
        apply_case_file_signature(c2, entries_for_seal, db=db)
        db.commit()
        db.refresh(c2)

        packed = json.loads(c2.signed_hash or "{}")
        payload = packed.get("payload") if isinstance(packed, dict) else None
        dist = None
        schema_v = None
        if isinstance(payload, dict):
            dist = payload.get("epistemic_distribution")
            schema_v = payload.get("schema_version")
        logger.info("Signed seal schema=%s epistemic_distribution=%s", schema_v, dist)

        print("\n=== DIAGNOSTIC SUMMARY ===")
        print(f"case_id: {case_id}")
        print(f"slug: {c2.slug}")
        print(f"FJC: {FJC_URL}")
        print(f"evidence_rows: {len(entries)}")
        print(f"by_source: {dict(by_source)}")
        print(f"planned_stub_adapters: {[p.get('adapter') for p in planned]}")
        print(f"pattern_alerts_count: {len(case_alerts)}")
        print(f"epistemic_distribution: {dist}")

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
