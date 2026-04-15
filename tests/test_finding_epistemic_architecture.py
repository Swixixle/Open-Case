"""Epistemic finding policy, public render gates, render copy, disputes, and audit trail."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from auth import generate_raw_key, hash_key
from models import CaseFile, DisputeRecord, EvidenceEntry, FindingAuditLog, Investigator, SubjectProfile
from payloads import sign_evidence_entry
from routes.evidence import evidence_to_response
from services.epistemic_classifier import ALLEGED, CONTEXTUAL, REPORTED, VERIFIED
from services.evidence_epistemic import apply_epistemic_metadata_to_entry
from services.finding_policy import (
    build_rendered_claim_text,
    compute_is_publicly_renderable,
    epistemic_level_for_basis,
    infer_classification_basis,
    valid_http_url,
)


def test_infer_classification_basis_maps_to_rule_buckets() -> None:
    assert (
        infer_classification_basis(
            source_type="court_document",
            entry_type="court_record",
            title="Motion",
            body="Motion to recuse Judge Smith for ex parte contact.",
        )
        == "motion_to_recuse"
    )
    b = infer_classification_basis(
        source_type="news",
        entry_type="timeline_event",
        title="Tribune",
        body="Reported conduct.",
    )
    assert epistemic_level_for_basis(b) == REPORTED
    b2 = infer_classification_basis(
        source_type="forum",
        entry_type="timeline_event",
        title="Thread",
        body="Anonymous claims.",
    )
    assert epistemic_level_for_basis(b2) == CONTEXTUAL


def test_compute_is_publicly_renderable_requires_url_claim_receipt_review() -> None:
    case = CaseFile(
        slug="s",
        title="t",
        subject_name="n",
        subject_type="senator",
        jurisdiction="US",
        status="open",
        created_by="x",
        summary="",
    )
    ent = EvidenceEntry(
        case_file_id=case.id,
        entry_type="timeline_event",
        title="x",
        body="claim body",
        source_url="https://example.com/doc",
        source_name="Example News",
        entered_by="x",
        confidence="probable",
        epistemic_level=REPORTED,
        review_status="pending",
        display_label="REPORTED",
        claim_text="",
    )
    # No signed_hash / receipt yet
    assert compute_is_publicly_renderable(ent) is False
    sign_evidence_entry(ent)
    ent.receipt_id = (ent.signed_hash or "")[:512]
    ent.display_label = "REPORTED"
    assert compute_is_publicly_renderable(ent) is True
    ent.review_status = "rejected"
    assert compute_is_publicly_renderable(ent) is False
    ent.review_status = "approved"
    ent.source_url = ""
    assert compute_is_publicly_renderable(ent) is False


def test_contextual_not_publicly_renderable_without_override() -> None:
    ent = EvidenceEntry(
        case_file_id=uuid.uuid4(),
        entry_type="timeline_event",
        title="t",
        body="b",
        source_url="https://reddit.com/r/x",
        source_name="reddit",
        entered_by="x",
        confidence="probable",
        epistemic_level=CONTEXTUAL,
        review_status="approved",
        display_label="CONTEXTUAL — unverified public record",
        claim_text="c",
        signed_hash='{"payload":{}}',
    )
    ent.receipt_id = "r1"
    assert compute_is_publicly_renderable(ent) is False
    assert compute_is_publicly_renderable(ent, admin_contextual_override=True) is True


def test_build_rendered_claim_text_alleged_is_source_framed() -> None:
    t = build_rendered_claim_text(
        epistemic_level=ALLEGED,
        claim_text="Judge X had ex parte contact.",
        source_publisher="CourtListener",
        source_type="court_document",
        document_type_label="court filing",
    )
    assert "alleged that" in t.lower()
    assert not t.lower().startswith("judge x")
    r = build_rendered_claim_text(
        epistemic_level=VERIFIED,
        claim_text="Final order entered dismissal.",
        source_publisher="Court",
        source_type="court_document",
    )
    assert r == "Final order entered dismissal."


def test_contradiction_count_forces_disputed_in_policy(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    try:
        raw = generate_raw_key()
        inv = Investigator(handle="h1", hashed_api_key=hash_key(raw))
        db.add(inv)
        case = CaseFile(
            slug=f"slug-{uuid.uuid4().hex[:10]}",
            title="C",
            subject_name="Judge J",
            subject_type="federal_judge_district",
            jurisdiction="US",
            status="open",
            created_by="h1",
            summary="",
        )
        db.add(case)
        db.flush()
        db.add(
            SubjectProfile(
                case_file_id=case.id,
                subject_name="Judge J",
                subject_type="federal_judge_district",
            )
        )
        e = EvidenceEntry(
            case_file_id=case.id,
            entry_type="timeline_event",
            title="Filing",
            body="Allegation in complaint.",
            source_url="https://example.com/filing",
            source_name="Ex",
            entered_by="h1",
            confidence="probable",
            contradiction_count=1,
        )
        db.add(e)
        db.commit()
        db.refresh(e)
        apply_epistemic_metadata_to_entry(e, case_subject_type=case.subject_type, case=case, db=db)
        db.commit()
        db.refresh(e)
        from services.epistemic_classifier import DISPUTED

        assert e.epistemic_level == DISPUTED
    finally:
        db.close()


def test_dispute_workflow_and_audit(client, test_engine) -> None:
    import database
    import main

    database.engine = test_engine
    database.SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    raw = generate_raw_key()
    handle = "disp-inv"
    db = database.SessionLocal()
    try:
        db.add(Investigator(handle=handle, hashed_api_key=hash_key(raw)))
        case = CaseFile(
            slug=f"slug-{uuid.uuid4().hex[:10]}",
            title="Case",
            subject_name="Judge Q",
            subject_type="federal_judge_district",
            jurisdiction="US",
            status="open",
            created_by=handle,
            summary="",
        )
        db.add(case)
        db.flush()
        db.add(
            SubjectProfile(
                case_file_id=case.id,
                subject_name="Judge Q",
                subject_type="federal_judge_district",
            )
        )
        ent = EvidenceEntry(
            case_file_id=case.id,
            entry_type="vote_record",
            title="Record",
            body="Claim about conduct.",
            source_url="https://www.courtlistener.com/x/",
            source_name="CL",
            entered_by=handle,
            confidence="probable",
        )
        db.add(ent)
        db.commit()
        db.refresh(ent)
        apply_epistemic_metadata_to_entry(
            ent, case_subject_type=case.subject_type, case=case, db=db
        )
        sign_evidence_entry(ent)
        from services.finding_policy import finalize_finding_after_sign

        finalize_finding_after_sign(ent, case)
        db.commit()
        fid = str(ent.id)
        cid = str(case.id)
    finally:
        db.close()

    hdr = {"Authorization": f"Bearer {raw}"}
    with patch.object(main, "init_db", lambda: None):
        with patch.dict("os.environ", {"ADMIN_SECRET": "adm-sec"}):
            r_d = client.post(
                f"/api/v1/findings/{fid}/dispute",
                json={
                    "submitted_by": "judge",
                    "dispute_type": "rebuttal",
                    "dispute_text": "This misstates the record.",
                    "supporting_source_url": "https://example.com/rebuttal",
                    "investigator_handle": handle,
                },
                headers=hdr,
            )
    assert r_d.status_code == 200
    dispute_id = r_d.json()["dispute_id"]

    with patch.object(main, "init_db", lambda: None):
        r_list = client.get(f"/api/v1/findings/{fid}/disputes", headers=hdr)
    assert r_list.status_code == 200
    assert len(r_list.json()["disputes"]) == 1

    with patch.object(main, "init_db", lambda: None):
        with patch.dict("os.environ", {"ADMIN_SECRET": "adm-sec"}):
            r_bad = client.patch(
                f"/api/v1/findings/disputes/{dispute_id}",
                json={
                    "resolution_status": "accepted",
                    "resolution_notes": "Supersede original framing.",
                    "investigator_handle": handle,
                },
                headers=hdr,
            )
    assert r_bad.status_code == 403

    with patch.object(main, "init_db", lambda: None):
        with patch.dict("os.environ", {"ADMIN_SECRET": "adm-sec"}):
            r_ok = client.patch(
                f"/api/v1/findings/disputes/{dispute_id}",
                json={
                    "resolution_status": "accepted",
                    "resolution_notes": "Supersede original framing.",
                    "investigator_handle": handle,
                },
                headers={**hdr, "X-Admin-Secret": "adm-sec"},
            )
    assert r_ok.status_code == 200

    db2 = database.SessionLocal()
    try:
        ent2 = db2.get(EvidenceEntry, uuid.UUID(fid))
        assert ent2 is not None
        assert ent2.claim_status == "superseded"
        assert int(ent2.contradiction_count or 0) >= 1
        audits = db2.scalars(
            select(FindingAuditLog).where(FindingAuditLog.finding_id == ent2.id)
        ).all()
        types = {a.event_type for a in audits}
        assert "dispute_opened" in types
        assert "dispute_accepted" in types
        row = db2.get(DisputeRecord, uuid.UUID(dispute_id))
        assert row is not None
        assert row.resolution_status == "accepted"
    finally:
        db2.close()


def test_evidence_response_includes_rendered_claim_and_finding_fields() -> None:
    e = EvidenceEntry(
        case_file_id=uuid.uuid4(),
        entry_type="timeline_event",
        title="T",
        body="Body claim.",
        source_url="https://chicagotribune.com/a",
        source_name="Chicago Tribune",
        entered_by="x",
        confidence="probable",
        epistemic_level=REPORTED,
        source_type="news",
        review_status="approved",
        display_label="REPORTED",
        claim_text="former clerks described issues",
    )
    sign_evidence_entry(e)
    e.receipt_id = (e.signed_hash or "")[:200]
    out = evidence_to_response(e)
    assert out["rendered_claim_text"]
    assert "reported that" in out["rendered_claim_text"].lower()
    assert out["epistemic_level"] == REPORTED
    assert valid_http_url(out["source_url"])


def test_manual_evidence_rejects_sourceless(client, test_engine) -> None:
    import database
    import main

    database.engine = test_engine
    database.SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    raw = generate_raw_key()
    handle = "ev-inv"
    db = database.SessionLocal()
    try:
        db.add(Investigator(handle=handle, hashed_api_key=hash_key(raw)))
        case = CaseFile(
            slug=f"slug-{uuid.uuid4().hex[:10]}",
            title="Case",
            subject_name="S",
            subject_type="senator",
            jurisdiction="US",
            status="open",
            created_by=handle,
            summary="",
        )
        db.add(case)
        db.commit()
        cid = str(case.id)
    finally:
        db.close()

    hdr = {"Authorization": f"Bearer {raw}"}
    with patch.object(main, "init_db", lambda: None):
        r = client.post(
            f"/cases/{cid}/evidence",
            json={
                "entry_type": "timeline_event",
                "title": "No URL",
                "body": "text",
                "source_url": "",
                "entered_by": handle,
                "confidence": "probable",
                "is_absence": False,
            },
            headers=hdr,
        )
    assert r.status_code == 400
