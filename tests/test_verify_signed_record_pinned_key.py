"""Issue #1: verify_signed_record must pin an out-of-band trusted key, never
the public_key embedded in the record.

The embedded public_key is NOT covered by the signature (the signature covers
only content_hash = JCS digest of the body), so trusting it is a forgery path.
These tests prove the rewritten primitive verifies only against an explicit
`trusted_public_key`.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives.serialization import load_der_private_key

from signing import canonical_digest, generate_keypair, verify_signed_record

BODY_KEYS = frozenset({"claim", "n"})


def _sign_record(body: dict, priv_b64: str, embedded_pub_b64: str) -> dict:
    """Build a signed record the way sign_payload does, but with an arbitrary
    embedded public_key (to simulate forgery / tampering of the display field)."""
    digest = canonical_digest(body)
    sk = load_der_private_key(base64.b64decode(priv_b64), password=None)
    sig = base64.b64encode(sk.sign(digest.encode("utf-8"))).decode()
    return {**body, "content_hash": digest, "signature": sig, "public_key": embedded_pub_b64}


def test_valid_record_verifies_against_pinned_key():
    priv, pub = generate_keypair()
    rec = _sign_record({"claim": "x", "n": 1}, priv, embedded_pub_b64=pub)
    r = verify_signed_record(rec, BODY_KEYS, trusted_public_key=pub)
    assert r["ok"], r


def test_attacker_key_with_matching_embedded_key_is_rejected():
    """THE forgery path (issue #1 / issue #4 deferred bullet): attacker signs
    with their own keypair AND embeds their own public key. A verifier that
    pins the real key must reject it."""
    _real_priv, real_pub = generate_keypair()
    atk_priv, atk_pub = generate_keypair()
    forged = _sign_record({"claim": "bribe", "n": 9}, atk_priv, embedded_pub_b64=atk_pub)
    r = verify_signed_record(forged, BODY_KEYS, trusted_public_key=real_pub)
    assert not r["ok"], "forged record must not verify against the pinned real key"
    assert any("trusted_public_key" in reason for reason in r["reasons"]), r


def test_absent_embedded_key_still_verifies_under_pinned_key():
    """Embedded key is display-only; its absence must not change the verdict."""
    priv, pub = generate_keypair()
    rec = _sign_record({"claim": "x", "n": 1}, priv, embedded_pub_b64="")
    r = verify_signed_record(rec, BODY_KEYS, trusted_public_key=pub)
    assert r["ok"], r


def test_present_but_mismatched_embedded_key_is_rejected():
    """A validly-signed record whose *present* embedded key disagrees with the
    pinned key is rejected as a tamper signal (matches issue #1's 'reject if
    embedded != trusted'). Note: an absent embedded key is fine (test above)."""
    priv, pub = generate_keypair()
    _atk_priv, atk_pub = generate_keypair()
    rec = _sign_record({"claim": "x", "n": 1}, priv, embedded_pub_b64=atk_pub)
    r = verify_signed_record(rec, BODY_KEYS, trusted_public_key=pub)
    assert not r["ok"], r
    assert any("embedded public_key" in reason for reason in r["reasons"]), r


def test_missing_trusted_key_fails_closed():
    priv, pub = generate_keypair()
    rec = _sign_record({"claim": "x", "n": 1}, priv, embedded_pub_b64=pub)
    r = verify_signed_record(rec, BODY_KEYS, trusted_public_key="")
    assert not r["ok"]
    assert any("fail closed" in reason for reason in r["reasons"]), r


def test_tampered_body_fails_even_with_matching_key():
    priv, pub = generate_keypair()
    rec = _sign_record({"claim": "x", "n": 1}, priv, embedded_pub_b64=pub)
    rec["claim"] = "TAMPERED"  # mutate body after signing
    r = verify_signed_record(rec, BODY_KEYS, trusted_public_key=pub)
    assert not r["ok"], r
