"""Adversarial tamper-rejection tests for the seal layer (issue #4).

This is the regression net for the P0 seal-integrity work. It exercises the
WIRED verification path — `verify_case_file_seal` / `verify_signed_hash_string`,
which pin the verifying key from `OPEN_CASE_PUBLIC_KEY` (env), not from a key
embedded in the record.

Honest scope note. The canonical case seal (`open-case-full-4`) embeds the
signed payload snapshot, and `verify_case_file_seal` returns on that
embedded-payload branch WITHOUT comparing to the live DB rows. So these tests
prove you cannot alter the *sealed record* (the `signed_hash` blob) undetected.
They also DOCUMENT, in `test_live_db_drift_is_not_detected_by_embedded_path`,
that mutating live evidence rows is not caught by the embedded-payload branch —
a real gap surfaced by this work, asserted as current behavior (not a passing
protection) so the suite is honest about what it does and does not prove.

What proves what: each test targets exactly one protection. If that protection
were removed, the corresponding assertion would flip.
"""

from __future__ import annotations

import json
import uuid

import pytest

from models import CaseFile, EvidenceEntry
from payloads import apply_case_file_signature, verify_case_file_seal
from signing import (
    generate_keypair,
    unpack_signed_hash,
    verify_signed_hash_string,
)


@pytest.fixture
def keys(monkeypatch):
    """A known signing keypair wired the way production reads it: private key
    via OPEN_CASE_PRIVATE_KEY (CredentialRegistry), public via OPEN_CASE_PUBLIC_KEY."""
    priv, pub = generate_keypair()
    monkeypatch.setenv("OPEN_CASE_PRIVATE_KEY", priv)
    monkeypatch.setenv("OPEN_CASE_PUBLIC_KEY", pub)
    return priv, pub


@pytest.fixture
def sealed(db_session, keys):
    """A case with two evidence rows, sealed with the canonical v4 bundle."""
    case = CaseFile(
        slug=f"slug-{uuid.uuid4().hex[:12]}",
        title="Tamper test case",
        subject_name="Test Subject",
        subject_type="public_official",
        jurisdiction="US",
        status="open",
        created_by="tester",
        summary="",
    )
    db_session.add(case)
    db_session.flush()
    entries = []
    for i in range(2):
        e = EvidenceEntry(
            case_file_id=case.id,
            entry_type="financial_connection",
            title=f"Evidence {i}",
            body="body",
            source_name="FEC",
            source_url=f"https://example.test/{i}",
            entered_by="tester",
            confidence="confirmed",
            is_absence=False,
        )
        db_session.add(e)
        db_session.flush()
        entries.append(e)
    apply_case_file_signature(case, entries, db=None)
    db_session.flush()
    return case, entries


def _repack(packed: str, mutate) -> str:
    """Unpack a signed_hash blob, apply `mutate(data)`, re-serialize the way
    pack_signed_hash does (sorted keys, compact separators)."""
    data = unpack_signed_hash(packed)
    mutate(data)
    return json.dumps(data, separators=(",", ":"), sort_keys=True)


# --- baseline: the untampered seal must verify (else every test below is vacuous)

def test_untampered_seal_verifies(sealed):
    case, entries = sealed
    result = verify_case_file_seal(case, entries, None)
    assert result["ok"], result


# --- tampering with the sealed record (the wired attack surface) --------------

def test_mutated_evidence_field_in_seal_fails(sealed):
    """Alter one evidence field inside the sealed snapshot -> JCS digest no
    longer matches content_hash -> reject."""
    case, entries = sealed

    def mutate(d):
        d["payload"]["evidence"][0]["title"] = "TAMPERED"

    case.signed_hash = _repack(case.signed_hash, mutate)
    result = verify_case_file_seal(case, entries, None)
    assert not result["ok"], "mutated evidence field must not verify"


def test_wrong_public_key_fails(sealed, monkeypatch):
    """Sealed with key A; verify against a different env key B -> signature
    verification must fail (proves the key is pinned, not trusted from the blob)."""
    case, entries = sealed
    _other_priv, other_pub = generate_keypair()
    monkeypatch.setenv("OPEN_CASE_PUBLIC_KEY", other_pub)
    result = verify_case_file_seal(case, entries, None)
    assert not result["ok"], "signature must not verify under a different public key"


def test_flipped_signature_byte_fails(sealed):
    case, entries = sealed

    def mutate(d):
        sig = d["signature"]
        # flip the first base64 char to a definitely-different one
        first = "B" if sig[0] != "B" else "C"
        d["signature"] = first + sig[1:]

    case.signed_hash = _repack(case.signed_hash, mutate)
    result = verify_case_file_seal(case, entries, None)
    assert not result["ok"], "a flipped signature byte must not verify"


def test_altered_content_hash_keeping_signature_fails(sealed):
    """Change content_hash but leave the signature -> the recomputed digest of
    the payload no longer equals the stored hash -> reject before signature check."""
    case, entries = sealed

    def mutate(d):
        h = d["content_hash"]
        d["content_hash"] = ("0" if h[0] != "0" else "1") + h[1:]

    case.signed_hash = _repack(case.signed_hash, mutate)
    result = verify_case_file_seal(case, entries, None)
    assert not result["ok"], "an altered content_hash must not verify"


@pytest.mark.parametrize(
    "mutate,label",
    [
        (lambda d: d["payload"]["evidence"].reverse(), "reorder"),
        (lambda d: d["payload"]["evidence"].pop(), "drop"),
        (
            lambda d: d["payload"]["evidence"].append(
                dict(d["payload"]["evidence"][0], id="00000000-injected")
            ),
            "add",
        ),
    ],
)
def test_reorder_add_drop_evidence_in_seal_fails(sealed, mutate, label):
    """Reordering, dropping, or adding an evidence entry inside the sealed
    snapshot changes the JCS digest -> reject."""
    case, entries = sealed
    case.signed_hash = _repack(case.signed_hash, mutate)
    result = verify_case_file_seal(case, entries, None)
    assert not result["ok"], f"evidence {label} in seal must not verify"


# --- documented GAP (not a passing protection) --------------------------------

def test_live_db_drift_is_not_detected_by_embedded_path(sealed):
    """GAP, reported in the session summary: the canonical seal embeds a payload
    snapshot, and verify_case_file_seal verifies THAT snapshot, ignoring the live
    `entries` argument. So mutating a live evidence row (without touching
    signed_hash) is NOT detected. This asserts the current (weaker-than-assumed)
    behavior so the gap is visible and tracked, not silently passing.
    """
    case, entries = sealed
    # Mutate a live row and even drop one from the argument list.
    entries[0].title = "LIVE-MUTATED-AFTER-SEAL"
    drifted = list(reversed(entries[:1]))  # only one entry, reordered/dropped
    result = verify_case_file_seal(case, drifted, None)
    # Current behavior: still "ok" because only the embedded snapshot is checked.
    assert result["ok"], (
        "unexpected: embedded-path verification now inspects live entries — "
        "if this fails, the gap was closed; update this test and the summary"
    )


# NOTE: the embedded-attacker-key forgery test (issue #4's final bullet) lands
# with the issue #1 fix, since verify_signed_record — the only key-from-blob
# path — is currently dead code. Added there against the hardened primitive.
