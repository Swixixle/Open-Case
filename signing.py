"""
Ed25519 signing for OPEN CASE — same pattern as Nikodemus / Split dossiers:
JCS canonicalization, SHA-256 digest (hex), sign digest as UTF-8 bytes, base64 signature.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import jcs
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_der_private_key,
    load_der_public_key,
)


def _load_private_key() -> Ed25519PrivateKey | None:
    from core.credentials import CredentialRegistry, CredentialUnavailable

    try:
        raw = CredentialRegistry.get_credential("open_case_signing") or ""
    except CredentialUnavailable:
        raw = ""
    if not raw:
        return None
    return load_der_private_key(base64.b64decode(raw), password=None)


def generate_keypair() -> tuple[str, str]:
    """Generate a new Ed25519 keypair. Returns (private_b64, public_b64) DER PKCS8 / SPKI."""
    private = Ed25519PrivateKey.generate()
    public = private.public_key()
    priv_b64 = base64.b64encode(
        private.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())
    ).decode()
    pub_b64 = base64.b64encode(
        public.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode()
    return priv_b64, pub_b64


def canonical_digest(payload: dict[str, Any]) -> str:
    canonical = jcs.canonicalize(payload)
    return hashlib.sha256(canonical).hexdigest()


def sign_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Return payload plus content_hash (sha256 hex of JCS body), signature (base64),
    and public_key (from env) for verification receipts.
    """
    private = _load_private_key()
    public_raw = os.environ.get("OPEN_CASE_PUBLIC_KEY", "")
    digest_hex = canonical_digest(payload)

    if private:
        sig_bytes = private.sign(digest_hex.encode("utf-8"))
        sig_b64 = base64.b64encode(sig_bytes).decode()
    else:
        sig_b64 = ""

    out = {**payload}
    out["content_hash"] = digest_hex
    out["signature"] = sig_b64
    out["public_key"] = public_raw
    return out


def verify_signed_record(data: dict[str, Any], body_keys: frozenset[str]) -> dict[str, Any]:
    """
    Verify a signed dict that includes content_hash, signature, public_key and semantic fields.
    body_keys: which top-level keys belong to the signed semantic payload (excludes crypto fields).
    """
    reasons: list[str] = []
    pub_raw = data.get("public_key") or data.get("publicKey", "")
    sig_b64 = data.get("signature", "")
    stored_hash = data.get("content_hash") or data.get("contentHash", "")

    body = {k: data[k] for k in body_keys if k in data}
    expected_hash = canonical_digest(body)

    if expected_hash != stored_hash:
        reasons.append("content_hash does not match signed semantic fields (JCS)")

    if pub_raw and sig_b64:
        try:
            pub_key = load_der_public_key(base64.b64decode(pub_raw))
            pub_key.verify(
                base64.b64decode(sig_b64),
                expected_hash.encode("utf-8"),
            )
        except Exception:
            reasons.append("signature verification failed")
    else:
        reasons.append("missing public_key or signature")

    return {"ok": len(reasons) == 0, "reasons": reasons}


def sign_content(payload: dict[str, Any]) -> str:
    """Pack a short ad-hoc dict (e.g. adapter stub) into signed_hash JSON."""
    signed = sign_payload(payload)
    return pack_signed_hash(signed["content_hash"], signed["signature"])


def pack_signed_hash(content_hash: str, signature_b64: str, payload: dict | None = None) -> str:
    """Single DB field: JSON with hash, signature, optional frozen payload for snapshots."""
    obj: dict[str, Any] = {"content_hash": content_hash, "signature": signature_b64}
    if payload is not None:
        obj["payload"] = payload
    return json.dumps(obj, separators=(",", ":"), sort_keys=True)


def unpack_signed_hash(s: str) -> dict[str, Any]:
    return json.loads(s)


def verify_signed_hash_string(
    packed: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """
    Verify signed_hash JSON from pack_signed_hash.
    If payload is None, uses embedded payload (snapshots); else uses supplied dict (case reconstructed from DB).
    """
    data = unpack_signed_hash(packed)
    use_payload = payload if payload is not None else data.get("payload")
    if not isinstance(use_payload, dict):
        return {"ok": False, "reasons": ["missing payload for verification"]}
    digest_hex = canonical_digest(use_payload)
    if digest_hex != data.get("content_hash"):
        return {"ok": False, "reasons": ["content_hash does not match payload (JCS)"]}
    pub_raw = os.environ.get("OPEN_CASE_PUBLIC_KEY", "")
    sig_b64 = data.get("signature", "")
    if not pub_raw or not sig_b64:
        return {"ok": False, "reasons": ["missing public_key env or signature in record"]}
    try:
        pub_key = load_der_public_key(base64.b64decode(pub_raw))
        pub_key.verify(base64.b64decode(sig_b64), digest_hex.encode("utf-8"))
    except Exception:
        return {"ok": False, "reasons": ["signature verification failed"]}
    return {"ok": True, "reasons": []}


def bootstrap_env_keys(project_root: Path | None = None) -> None:
    """If keys are missing, generate a keypair and persist public (and private) key to .env."""
    root = project_root or Path(__file__).resolve().parent
    env_path = root / ".env"
    from dotenv import load_dotenv

    load_dotenv(env_path)
    if os.environ.get("OPEN_CASE_PRIVATE_KEY") and os.environ.get("OPEN_CASE_PUBLIC_KEY"):
        return

    priv, pub = generate_keypair()
    line_priv = f"OPEN_CASE_PRIVATE_KEY={priv}\n"
    line_pub = f"OPEN_CASE_PUBLIC_KEY={pub}\n"

    if env_path.exists():
        text = env_path.read_text()
        lines = [ln for ln in text.splitlines() if not ln.startswith("OPEN_CASE_")]
        lines.append(line_priv.strip())
        lines.append(line_pub.strip())
        env_path.write_text("\n".join(lines) + "\n")
    else:
        env_path.write_text(line_priv + line_pub)

    os.environ["OPEN_CASE_PRIVATE_KEY"] = priv
    os.environ["OPEN_CASE_PUBLIC_KEY"] = pub
