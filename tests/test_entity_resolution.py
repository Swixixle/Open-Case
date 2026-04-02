"""Phase 10B — entity resolution, canonicalization, cross-case fingerprint keys."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from auth import generate_raw_key, hash_key
from engines.entity_resolution import (
    _cached_aliases_tuple,
    append_alias_entry,
    canonicalize,
    resolve,
    suggest_aliases,
    suggest_aliases_detail,
)
from models import (
    CaseContributor,
    CaseFile,
    DonorFingerprint,
    Investigator,
    Signal,
)
from routes.investigate import (
    _apply_cross_case_baseline_and_fingerprints,
    _fingerprint_donor_key,
)


def test_canonicalize_strips_pac_suffix() -> None:
    assert canonicalize("MORGAN STANLEY PAC") == "MORGAN STANLEY"


def test_canonicalize_strips_political_action_committee() -> None:
    assert (
        canonicalize("MORGAN STANLEY POLITICAL ACTION COMMITTEE") == "MORGAN STANLEY"
    )


def test_canonicalize_does_not_merge_different_entities() -> None:
    assert canonicalize("MASS MUTUAL") != canonicalize("MASS CONSTRUCTION LLC")


def test_canonicalize_bank_not_noise() -> None:
    assert canonicalize("APPLE BANK") != canonicalize("APPLE INC")


def test_alias_table_resolves_known_alias() -> None:
    r = resolve("MORGAN STANLEY SMITH BARNEY LLC PAC")
    assert r.canonical_id == "morgan-stanley"
    assert r.resolution_method == "alias_table"


def test_unresolved_falls_back_to_normalized_slug(tmp_path: Path) -> None:
    p = tmp_path / "entity_aliases.json"
    p.write_text(json.dumps({"aliases": []}), encoding="utf-8")
    r = resolve("ACME WIDGETS LLC", aliases_path=p)
    assert r.resolution_method == "unresolved"
    assert r.canonical_id == "acme-widgets"


def test_suggest_aliases_returns_score() -> None:
    assert (
        suggest_aliases(
            "MORGAN STANLEY PAC", "MORGAN STANLEY POLITICAL ACTION COMMITTEE"
        )
        == 1.0
    )


def test_suggest_requires_shared_non_noise_token() -> None:
    assert suggest_aliases("ALPHA BETA", "GAMMA DELTA") == 0.0
    assert suggest_aliases("AB PAC", "AB LLC") == 0.0


def test_suggest_aliases_detail_shape() -> None:
    d = suggest_aliases_detail("MORGAN STANLEY PAC", "MORGAN STANLEY POLITICAL ACTION COMMITTEE")
    assert d["jaccard_score"] == 1.0
    assert set(d["shared_tokens"]) == {"MORGAN", "STANLEY"}
    assert d["action_required"] == "human_review_before_alias_merge"


def test_cross_case_appearances_fires_after_normalization(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    raw_key = generate_raw_key()
    h = "xres"
    db.add(Investigator(handle=h, hashed_api_key=hash_key(raw_key), public_key=""))
    c1 = CaseFile(
        slug=f"yc-{uuid.uuid4().hex[:8]}",
        title="Young",
        subject_name="T Young",
        subject_type="public_official",
        jurisdiction="US",
        status="open",
        created_by=h,
        summary="",
    )
    c2 = CaseFile(
        slug=f"bc-{uuid.uuid4().hex[:8]}",
        title="Banks",
        subject_name="J Banks",
        subject_type="public_official",
        jurisdiction="US",
        status="open",
        created_by=h,
        summary="",
    )
    db.add_all([c1, c2])
    db.flush()
    s1 = Signal(
        case_file_id=c1.id,
        signal_identity_hash="a" * 64,
        signal_type="temporal_proximity",
        weight=0.7,
        description="d",
        evidence_ids="[]",
        exposure_state="internal",
        actor_a="MORGAN STANLEY PAC",
        actor_b="T Young",
        weight_breakdown=json.dumps(
            {
                "kind": "donor_cluster",
                "donor": "MORGAN STANLEY PAC",
                "official": "T Young",
            }
        ),
    )
    db.add(s1)
    db.flush()
    db.add(
        DonorFingerprint(
            normalized_donor_key="morgan stanley pac",
            canonical_id="morgan-stanley",
            resolution_method="exact",
            normalized_name="MORGAN STANLEY",
            case_file_id=c1.id,
            signal_id=s1.id,
            weight=0.7,
            official_name="T Young",
            bioguide_id="Y000064",
        )
    )
    s2 = Signal(
        case_file_id=c2.id,
        signal_identity_hash="b" * 64,
        signal_type="temporal_proximity",
        weight=0.65,
        description="d2",
        evidence_ids="[]",
        exposure_state="internal",
        actor_a="MORGAN STANLEY POLITICAL ACTION COMMITTEE",
        actor_b="J Banks",
        weight_breakdown=json.dumps(
            {
                "kind": "donor_cluster",
                "donor": "MORGAN STANLEY POLITICAL ACTION COMMITTEE",
                "official": "J Banks",
            }
        ),
    )
    db.add(s2)
    db.flush()
    db.commit()

    _apply_cross_case_baseline_and_fingerprints(db, c2.id, [s2], [], "B001306")
    db.commit()
    db.refresh(s2)
    assert s2.cross_case_appearances >= 1
    assert _fingerprint_donor_key(s2) == "morgan-stanley"
    db.close()


def test_suggest_endpoint(client) -> None:
    r = client.get(
        "/api/v1/entity-resolution/suggest",
        params={
            "name_a": "MORGAN STANLEY PAC",
            "name_b": "MORGAN STANLEY POLITICAL ACTION COMMITTEE",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["jaccard_score"] == 1.0


def test_append_alias_persists(
    client,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMIN_SECRET", "sekrit")
    p = tmp_path / "entity_aliases.json"
    p.write_text(json.dumps({"aliases": []}), encoding="utf-8")
    monkeypatch.setattr("engines.entity_resolution._DEFAULT_ALIASES_PATH", p)
    _cached_aliases_tuple.cache_clear()
    r = client.post(
        "/api/v1/entity-resolution/aliases",
        json={
            "canonical_id": "acme-corp",
            "canonical_name": "ACME",
            "aliases": ["ACME INC"],
            "added_by": "test",
            "added_at": "2026-04-01",
        },
        headers={"X-Admin-Secret": "sekrit"},
    )
    assert r.status_code == 200
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert len(doc["aliases"]) == 1
    _cached_aliases_tuple.cache_clear()
    assert resolve("ACME INC", aliases_path=p).canonical_id == "acme-corp"


def test_batch_open_creates_cases(client, test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    raw_key = generate_raw_key()
    h = "batch_open_u"
    db.add(
        Investigator(
            handle=h,
            hashed_api_key=hash_key(raw_key),
            public_key="",
        )
    )
    db.commit()
    db.close()
    r = client.post(
        "/api/v1/cases/batch-open",
        json={
            "subjects": [
                {
                    "subject_name": "Sample Senator",
                    "bioguide_id": "S999999",
                    "fec_committee_id": "C00123456",
                    "committee_focus": "Test",
                }
            ],
            "created_by": h,
            "description": "committee sweep",
        },
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data["case_ids"]) == 1
