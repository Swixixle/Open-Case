#!/usr/bin/env python3
"""Seed Joe Hogsett local POC case, run investigate + pattern engine, print alerts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
import uuid
from datetime import date
from pathlib import Path

from fastapi import BackgroundTasks

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import select

from database import SessionLocal
from engines.pattern_engine import (
    LOCAL_RELATED_ENTITY_DONOR_DIAGNOSTICS,
    PATTERN_ENGINE_VERSION,
    RULE_LOCAL_CONTRACTOR_DONOR_LOOP,
    RULE_LOCAL_CONTRACT_DONATION_TIMING,
    RULE_LOCAL_RELATED_ENTITY_DONOR,
    RULE_LOCAL_VENDOR_CONCENTRATION,
    _idis_donor_label,
    _local_contract_event_type,
    _local_procurement_vendor_variants,
    run_pattern_engine,
)
from utils.local_entity_matching import (
    MATCH_ALIAS,
    MATCH_DIRECT,
    MATCH_NONE,
    MATCH_RELATED_ENTITY,
    _local_match_type,
    local_jurisdiction_alias_key,
    local_match_eligible_for_loop_and_timing,
)
from models import CaseContributor, CaseFile, EvidenceEntry, Investigator, SubjectProfile
from payloads import apply_case_file_signature
from routes.investigate import InvestigateRequest, execute_investigation_for_case

FIXTURE_PATH = _ROOT / "data" / "fixtures" / "hogsett_procurement_sample.json"
DONOR_FIXTURE_PATH = _ROOT / "data" / "fixtures" / "hogsett_donors_sample.json"


def _fixture_row_hash(row: dict) -> str:
    key = (row.get("fixture_key") or "").strip()
    if not key:
        payload = json.dumps(row, sort_keys=True, default=str)
        key = hashlib.sha256(payload.encode()).hexdigest()
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def ingest_procurement_fixture(case_id: uuid.UUID, db, entered_by: str) -> int:
    """Insert hand-built INDY_PROCUREMENT rows from JSON fixture; idempotent per fixture_key."""
    if not FIXTURE_PATH.is_file():
        return 0
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    rows: list[dict] = list(payload.get("contracts") or [])
    if not rows:
        return 0

    existing = set(
        db.scalars(
            select(EvidenceEntry.evidence_hash).where(
                EvidenceEntry.case_file_id == case_id,
                EvidenceEntry.adapter_name == "INDY_PROCUREMENT",
                EvidenceEntry.evidence_hash.isnot(None),
            )
        ).all()
    )
    existing_entries = db.scalars(
        select(EvidenceEntry).where(
            EvidenceEntry.case_file_id == case_id,
            EvidenceEntry.adapter_name == "INDY_PROCUREMENT",
        )
    ).all()
    existing_keys: set[str] = set()
    for ent in existing_entries:
        try:
            raw = json.loads(ent.raw_data_json or "{}")
        except json.JSONDecodeError:
            raw = {}
        fk = str(raw.get("fixture_key") or "").strip()
        if fk:
            existing_keys.add(fk)

    created = 0
    for row in rows:
        fk = str(row.get("fixture_key") or "").strip()
        eh = _fixture_row_hash(row)
        if eh in existing or (fk and fk in existing_keys):
            continue
        award_date = date.fromisoformat(str(row["award_date"]))
        raw_out = dict(row)
        raw_out["award_amount"] = float(row["award_amount"])
        db.add(
            EvidenceEntry(
                case_file_id=case_id,
                entry_type="government_record",
                title=str(row.get("contract_description") or "Awarded contract")[:1024],
                body=(
                    f"Fixture ingest: {row.get('contract_reference') or fk}. "
                    f"{row.get('contract_description') or ''}"
                )[:65535],
                source_url=str(row.get("source_url") or ""),
                source_name="INDY_PROCUREMENT",
                adapter_name="INDY_PROCUREMENT",
                entered_by=entered_by,
                confidence="confirmed",
                is_absence=False,
                amount=float(row["award_amount"]),
                matched_name=str(row.get("vendor_name_raw") or "")[:512] or None,
                date_of_event=award_date,
                raw_data_json=json.dumps(raw_out, sort_keys=True, default=str),
                epistemic_level="VERIFIED",
                evidence_hash=eh,
                ingest_method="procurement_fixture",
            )
        )
        existing.add(eh)
        if fk:
            existing_keys.add(fk)
        created += 1
    if created:
        db.commit()
    return created


def ingest_donor_fixture(case_id: uuid.UUID, db, entered_by: str) -> int:
    """Insert hand-built IDIS financial_connection rows from JSON; idempotent per fixture_key."""
    if not DONOR_FIXTURE_PATH.is_file():
        return 0
    payload = json.loads(DONOR_FIXTURE_PATH.read_text(encoding="utf-8"))
    rows: list[dict] = list(payload.get("contributions") or [])
    if not rows:
        return 0

    existing = set(
        db.scalars(
            select(EvidenceEntry.evidence_hash).where(
                EvidenceEntry.case_file_id == case_id,
                EvidenceEntry.adapter_name == "IDIS",
                EvidenceEntry.evidence_hash.isnot(None),
            )
        ).all()
    )
    existing_entries = db.scalars(
        select(EvidenceEntry).where(
            EvidenceEntry.case_file_id == case_id,
            EvidenceEntry.adapter_name == "IDIS",
        )
    ).all()
    existing_keys: set[str] = set()
    for ent in existing_entries:
        try:
            raw = json.loads(ent.raw_data_json or "{}")
        except json.JSONDecodeError:
            raw = {}
        fk = str(raw.get("fixture_key") or "").strip()
        if fk:
            existing_keys.add(fk)

    created = 0
    for row in rows:
        fk = str(row.get("fixture_key") or "").strip()
        eh = _fixture_row_hash(row)
        if eh in existing or (fk and fk in existing_keys):
            continue
        cdate = date.fromisoformat(str(row["contribution_date"]))
        raw_out = dict(row)
        raw_out["contribution_amount"] = float(row["contribution_amount"])
        raw_out["contributor_name"] = str(row.get("contributor_name_raw") or "").strip()
        db.add(
            EvidenceEntry(
                case_file_id=case_id,
                entry_type="financial_connection",
                title=(
                    f"Fixture: {raw_out['contributor_name'][:80]} → "
                    f"{(row.get('committee_name') or '')[:80]}"
                )[:1024],
                body=(
                    f"IDIS fixture ingest (bulk CSV {row.get('idis_year')}, "
                    f"file {row.get('idis_file_number')}). Committee: "
                    f"{row.get('committee_name') or ''}."
                )[:65535],
                source_url=str(row.get("source_url") or ""),
                source_name="IDIS",
                adapter_name="IDIS",
                entered_by=entered_by,
                confidence="confirmed",
                is_absence=False,
                amount=float(row["contribution_amount"]),
                matched_name=str(row.get("committee_name") or "")[:512] or None,
                date_of_event=cdate,
                raw_data_json=json.dumps(raw_out, sort_keys=True, default=str),
                epistemic_level="REPORTED",
                evidence_hash=eh,
                ingest_method="donor_fixture",
            )
        )
        existing.add(eh)
        if fk:
            existing_keys.add(fk)
        created += 1
    if created:
        db.commit()
    return created


def print_procurement_diagnostics(db, case_id: uuid.UUID) -> None:
    case = db.get(CaseFile, case_id)
    jkey = local_jurisdiction_alias_key((case.jurisdiction if case else "") or "")
    entries = db.scalars(
        select(EvidenceEntry).where(EvidenceEntry.case_file_id == case_id)
    ).all()
    proc = [e for e in entries if (e.adapter_name or "") == "INDY_PROCUREMENT"]
    proc_core = [
        e
        for e in proc
        if not e.is_absence and (e.entry_type or "") == "government_record"
    ]
    idis = [e for e in entries if (e.adapter_name or "") == "IDIS"]

    def _raw(e: EvidenceEntry) -> dict:
        try:
            return json.loads(e.raw_data_json or "{}")
        except json.JSONDecodeError:
            return {}

    canon_ok = 0
    amt_ok = 0
    date_ok = 0
    event_type_ok = 0
    eligible_legacy = 0
    eligible_loop = 0
    eligible_timing = 0
    excluded_timing_non_award = 0
    missing_event_type = 0

    for e in proc_core:
        raw = _raw(e)
        vc = str(raw.get("vendor_canonical") or "").strip()
        cet = _local_contract_event_type(e)
        if vc and len(vc) >= 3:
            canon_ok += 1
        if e.amount is not None and float(e.amount) > 0:
            amt_ok += 1
        if e.date_of_event is not None:
            date_ok += 1
        if cet:
            event_type_ok += 1
        else:
            missing_event_type += 1
        base_ok = (
            vc
            and len(vc) >= 3
            and e.amount is not None
            and float(e.amount) > 0
            and e.date_of_event is not None
        )
        if base_ok:
            eligible_legacy += 1
        if base_ok and cet not in ("amendment", "change_order", "closeout"):
            if cet is None or cet in ("award", "supply_purchase", "final_acceptance"):
                eligible_loop += 1
        if base_ok and cet == "award":
            eligible_timing += 1
        if base_ok and cet is not None and cet != "award":
            excluded_timing_non_award += 1

    idis_n = len(idis)
    idis_gap_no_match = 0
    idis_gap_parse = 0
    idis_missing_contributor = 0
    idis_missing_amt_date = 0
    idis_usable = 0
    idis_no_vendor_overlap = 0
    donor_direct = 0
    donor_alias = 0
    donor_related = 0
    donor_none = 0
    overlap_candidates = 0
    overlap_by_type: dict[str, set[str]] = {
        MATCH_DIRECT: set(),
        MATCH_ALIAS: set(),
        MATCH_RELATED_ENTITY: set(),
    }

    for e in idis:
        tl = (e.title or "").lower()
        bl = (e.body or "").lower()
        if e.is_absence and (e.entry_type or "") == "gap_documented":
            if "no committee" in tl or "no committee" in bl or "no matching records" in bl:
                idis_gap_no_match += 1
            elif (
                "parse warning" in tl
                or "parse warning" in bl
                or "unreachable" in tl
                or "unreachable" in bl
            ):
                idis_gap_parse += 1
            else:
                idis_gap_no_match += 1
            continue
        dl = _idis_donor_label(e)
        if not dl:
            idis_missing_contributor += 1
            continue
        bad_amt = e.amount is None or float(e.amount) <= 0
        bad_dt = e.date_of_event is None
        if bad_amt or bad_dt:
            idis_missing_amt_date += 1
            continue
        idis_usable += 1
        best = MATCH_NONE
        matched_v = False
        for v in proc_core:
            for vl in _local_procurement_vendor_variants(v):
                mt, _, _ = _local_match_type(vl, dl, jkey, db)
                if mt == MATCH_DIRECT:
                    best = MATCH_DIRECT
                elif mt == MATCH_ALIAS and best not in (MATCH_DIRECT,):
                    best = MATCH_ALIAS
                elif mt == MATCH_RELATED_ENTITY and best == MATCH_NONE:
                    best = MATCH_RELATED_ENTITY
                ok, _, _, _ = local_match_eligible_for_loop_and_timing(vl, dl, jkey, db)
                if ok:
                    matched_v = True
            if best == MATCH_DIRECT:
                break
        if not matched_v:
            idis_no_vendor_overlap += 1
        if best == MATCH_DIRECT:
            donor_direct += 1
        elif best == MATCH_ALIAS:
            donor_alias += 1
        elif best == MATCH_RELATED_ENTITY:
            donor_related += 1
        else:
            donor_none += 1

    for v in proc_core:
        vvars = _local_procurement_vendor_variants(v)
        if not vvars:
            continue
        for d in idis:
            if d.is_absence and (d.entry_type or "") == "gap_documented":
                continue
            dlabel = _idis_donor_label(d)
            if not dlabel:
                continue
            pair_hit = False
            for vl in vvars:
                mt, _, _ = _local_match_type(vl, dlabel, jkey, db)
                if mt != MATCH_NONE:
                    pair_hit = True
                    overlap_by_type.setdefault(mt, set()).add(f"{vl} | {dlabel}")
            if pair_hit:
                overlap_candidates += 1

    print("\n--- PROCUREMENT DIAGNOSTIC ---")
    print(f"INDY_PROCUREMENT rows seen:              {len(proc)}")
    print(f"  with usable vendor_canonical:          {canon_ok}")
    print(f"  with usable award_amount:              {amt_ok}")
    print(f"  with usable award_date:                {date_ok}")
    print(f"  with usable contract_event_type:       {event_type_ok}")
    print(f"  eligible (legacy core row):            {eligible_legacy}")
    print(f"  eligible for loop rule:                {eligible_loop}")
    print(f"  eligible for timing rule (award only): {eligible_timing}")
    print(f"  excluded timing (non-award type):      {excluded_timing_non_award}")
    print(f"  excluded timing (missing event type):  {missing_event_type}")
    print(f"IDIS donor rows seen:                    {idis_n}")
    print(f"  no committee/candidate match:          {idis_gap_no_match}")
    print(f"  parse failure:                         {idis_gap_parse}")
    print(f"  row found, contributor_name missing:   {idis_missing_contributor}")
    print(f"  row found, amount or date missing:     {idis_missing_amt_date}")
    print(f"  usable donor rows:                     {idis_usable}")
    print(f"  direct vendor match (best):            {donor_direct}")
    print(f"  alias match (best):                    {donor_alias}")
    print(f"  related-entity match (best):           {donor_related}")
    print(f"  no structured match (best):            {donor_none}")
    print(f"  contributor present, no loop overlap:  {idis_no_vendor_overlap}")
    print(f"Pair overlap candidates (all types):     {overlap_candidates}")
    print("  Unique entity-pair tokens by match type:")
    for mt, label in (
        (MATCH_DIRECT, "direct"),
        (MATCH_ALIAS, "alias"),
        (MATCH_RELATED_ENTITY, "related_entity"),
    ):
        n = len(overlap_by_type.get(mt, set()))
        print(f"    {label}: {n}")
    print("------------------------------")


def _slug_unique(db, base: str) -> str:
    slug = base[:200]
    if not db.scalar(select(CaseFile.id).where(CaseFile.slug == slug)):
        return slug
    return f"{slug}-{uuid.uuid4().hex[:8]}"


async def main() -> None:
    handle = "hogsett-poc-bot"
    db = SessionLocal()
    try:
        inv = db.scalar(select(Investigator).where(Investigator.handle == handle))
        if not inv:
            db.add(Investigator(handle=handle, public_key=""))
            db.flush()
            print(f"Created investigator {handle!r} (no API key; script uses DB directly).")

        subject = "Joe Hogsett"
        slug_base = re.sub(r"[^a-z0-9]+", "-", f"{subject}-indianapolis-mayor-local".lower()).strip(
            "-"
        )
        existing = db.scalar(
            select(CaseFile).where(
                CaseFile.subject_name == subject,
                CaseFile.jurisdiction == "Indianapolis / Marion County, IN",
            )
        )
        if existing:
            case = existing
            print(f"Using existing case {case.id} slug={case.slug}")
        else:
            case = CaseFile(
                slug=_slug_unique(db, slug_base),
                title=f"{subject} — Indianapolis mayor (local POC)",
                subject_name=subject,
                subject_type="official",
                jurisdiction="Indianapolis / Marion County, IN",
                status="open",
                created_by=handle,
                summary="Local government POC: IDIS + Indianapolis open data.",
                government_level="local",
                branch="executive",
            )
            db.add(case)
            db.flush()
            db.add(
                CaseContributor(
                    case_file_id=case.id,
                    investigator_handle=handle,
                    role="originator",
                )
            )
            db.add(
                SubjectProfile(
                    case_file_id=case.id,
                    subject_name=subject,
                    subject_type="official",
                    government_level="local",
                    branch="executive",
                    updated_by=handle,
                )
            )
            apply_case_file_signature(case, [], db=db)
            db.commit()
            print(f"Created case_id={case.id} slug={case.slug}")

        req = InvestigateRequest(
            subject_name=subject,
            investigator_handle=handle,
        )
        out = await execute_investigation_for_case(
            db,
            case.id,
            req,
            BackgroundTasks(),
            debug=True,
        )
        if hasattr(out, "body"):
            print("Investigation returned error response:", getattr(out, "body", out))
            return
        print(
            "Investigation:",
            {k: out[k] for k in out if k in ("evidence_entries_created", "signals_detected", "errors")},
        )

        # Investigation replaces all case evidence each run; re-apply fixture afterward.
        fix_n = ingest_procurement_fixture(case.id, db, handle)
        if fix_n:
            print(f"Procurement fixture: inserted {fix_n} new INDY_PROCUREMENT row(s).")
        dfix_n = ingest_donor_fixture(case.id, db, handle)
        if dfix_n:
            print(f"Donor fixture: inserted {dfix_n} new IDIS financial_connection row(s).")
        print_procurement_diagnostics(db, case.id)

        alerts = run_pattern_engine(db)
        cid = str(case.id)
        hits = [a for a in alerts if cid in a.matched_case_ids]
        print(
            f"\nPattern engine v{PATTERN_ENGINE_VERSION}: "
            f"{len(hits)} alert(s) for this case ({len(alerts)} globally)."
        )
        sk_rel = LOCAL_RELATED_ENTITY_DONOR_DIAGNOSTICS.get(
            "skipped_missing_contract_event_type", 0
        )
        print(
            f"LOCAL_RELATED_ENTITY_DONOR_V1 diagnostics: "
            f"skipped_missing_contract_event_type={sk_rel}"
        )
        related_hits = [a for a in hits if a.rule_id == RULE_LOCAL_RELATED_ENTITY_DONOR]
        if related_hits:
            print(f"\n--- LOCAL_RELATED_ENTITY_DONOR_V1 ({len(related_hits)} alert(s)) ---")
            for a in related_hits:
                pe = getattr(a, "payload_extra", None) or {}
                print(
                    f"  relationship_type={pe.get('relationship_type')!r} "
                    f"indirect_match_label={pe.get('indirect_match_label')!r}"
                )
                print(
                    f"  vendor_label={pe.get('vendor_label')!r} "
                    f"donor_label={pe.get('donor_label')!r}"
                )
                print(
                    f"  contract_event_type={pe.get('contract_event_type')!r} "
                    f"contract_amount={pe.get('contract_amount')} "
                    f"donation_amount={pe.get('donation_amount')}"
                )
            print("--- end related-entity alerts ---\n")
        local_rules = {
            RULE_LOCAL_CONTRACTOR_DONOR_LOOP,
            RULE_LOCAL_CONTRACT_DONATION_TIMING,
            RULE_LOCAL_VENDOR_CONCENTRATION,
            RULE_LOCAL_RELATED_ENTITY_DONOR,
        }
        for a in hits:
            pe = getattr(a, "payload_extra", None) or {}
            extra = f" {pe}" if pe else ""
            print(f"  - {a.rule_id} score={getattr(a, 'suspicion_score', None)}{extra}")
            if a.rule_id in local_rules and pe:
                if a.rule_id == RULE_LOCAL_VENDOR_CONCENTRATION:
                    print(
                        f"      LOCAL concentration: overlap_count={pe.get('overlap_count')} "
                        f"award_total={pe.get('procurement_total_award_amount')} "
                        f"other_events_total={pe.get('procurement_total_other_event_types_amount')}"
                    )
                    for ent in pe.get("overlapping_entities") or []:
                        print(
                            f"        · key={ent.get('entity_key')} match_type={ent.get('match_type')} "
                            f"rel={ent.get('relationship_type')} "
                            f"v={ent.get('vendor_total')} d={ent.get('donor_total')}"
                        )
                elif a.rule_id == RULE_LOCAL_RELATED_ENTITY_DONOR:
                    print(
                        f"      rule={a.rule_id} vendor={pe.get('vendor_label')!r} "
                        f"donor={pe.get('donor_label')!r} "
                        f"relationship_type={pe.get('relationship_type')!r} "
                        f"indirect_match_label={pe.get('indirect_match_label')!r}"
                    )
                    print(
                        f"      contract_event_type={pe.get('contract_event_type')} "
                        f"contract_amount={pe.get('contract_amount')} "
                        f"donation_amount={pe.get('donation_amount')}"
                    )
                elif a.rule_id in (
                    RULE_LOCAL_CONTRACTOR_DONOR_LOOP,
                    RULE_LOCAL_CONTRACT_DONATION_TIMING,
                ):
                    print(
                        f"      rule={a.rule_id} vendor={pe.get('vendor_label')!r} "
                        f"donor={pe.get('donor_label')!r} "
                        f"match_type={pe.get('match_type')} "
                        f"relationship_type={pe.get('relationship_type')} "
                        f"relationship_source_note={pe.get('relationship_source_note')!r}"
                    )
                    print(
                        f"      contract_event_type={pe.get('contract_event_type')} "
                        f"contract_amount={pe.get('contract_amount')} "
                        f"donation_amount={pe.get('donation_amount')} "
                        f"vendor_canonical={pe.get('vendor_canonical')!r} "
                        f"donor_canonical={pe.get('donor_canonical')!r}"
                    )
                    if a.rule_id == RULE_LOCAL_CONTRACT_DONATION_TIMING:
                        print(
                            f"      days_delta={pe.get('days_donation_minus_award')} "
                            f"direction={pe.get('timing_direction')} "
                            f"award_date={pe.get('award_date')} "
                            f"donation_date={pe.get('donation_date')}"
                        )
                    elif a.rule_id == RULE_LOCAL_CONTRACTOR_DONOR_LOOP:
                        print(
                            f"      event_type_used={pe.get('event_type_used')} "
                            f"event_type_warning={pe.get('event_type_warning')!r}"
                        )
        if not hits:
            print(
                "  No alerts for this case. Federal rules need fingerprints/votes. "
                "LOCAL_CONTRACTOR_DONOR_LOOP_V1 / LOCAL_CONTRACT_DONATION_TIMING_V1 require "
                "at least one procurement vendor label entity-matched to an IDIS contributor "
                "(canonical/alias resolution); timing also needs donation and award dates within "
                "180 days. LOCAL_VENDOR_CONCENTRATION_V1 needs at least two entities in the "
                "overlap of top-5 vendor totals and top-5 donor totals. INDY_TAX_ABATEMENT "
                "never feeds LOCAL_* rules."
            )
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
