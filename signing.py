"""
Ed25519 signing for OPEN CASE — same pattern as Nikodemus / Split dossiers:
JCS canonicalization, SHA-256 digest (hex), sign digest as UTF-8 bytes, base64 signature.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
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

logger = logging.getLogger(__name__)


def _decode_private_key_b64(priv_b64: str) -> Ed25519PrivateKey | None:
    """Return PKCS8 DER Ed25519 private key, or None if base64/DER is invalid."""
    try:
        raw = base64.b64decode(priv_b64.strip(), validate=False)
        return load_der_private_key(raw, password=None)
    except (ValueError, binascii.Error):
        return None


def _public_der_matches_private(sk: Ed25519PrivateKey, pub_b64: str) -> bool:
    try:
        want = sk.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        got = base64.b64decode(pub_b64.strip(), validate=False)
        return want == got
    except (ValueError, binascii.Error):
        return False


def _persist_signing_keys_env(env_path: Path, priv_b64: str, pub_b64: str) -> None:
    line_priv = f"OPEN_CASE_PRIVATE_KEY={priv_b64}"
    line_pub = f"OPEN_CASE_PUBLIC_KEY={pub_b64}"
    if env_path.exists():
        text = env_path.read_text()
        lines = [
            ln
            for ln in text.splitlines()
            if not (
                ln.strip().startswith("OPEN_CASE_PRIVATE_KEY=")
                or ln.strip().startswith("OPEN_CASE_PUBLIC_KEY=")
            )
        ]
        lines.extend([line_priv, line_pub])
        env_path.write_text("\n".join(lines) + "\n")
    else:
        env_path.write_text(line_priv + "\n" + line_pub + "\n")


def _load_private_key() -> Ed25519PrivateKey | None:
    from core.credentials import CredentialRegistry, CredentialUnavailable

    try:
        raw = CredentialRegistry.get_credential("open_case_signing") or ""
    except CredentialUnavailable:
        raw = ""
    if not raw:
        return None
    try:
        return load_der_private_key(base64.b64decode(raw.strip(), validate=False), password=None)
    except binascii.Error as e:
        raise ValueError(
            "OPEN_CASE_PRIVATE_KEY is not valid base64 (often truncated when copy-pasting). "
            "Fix or remove it in .env, or run: python3 scripts/regenerate_open_case_signing_keys.py"
        ) from e


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
    """
    Ensure .env has a usable Ed25519 PKCS8 DER keypair.

    - Missing or invalid private key → generate new pair (prior seals will not verify with old keys).
    - Valid private, missing/mismatched public → derive public from private and save.
    """
    root = project_root or Path(__file__).resolve().parent
    env_path = root / ".env"
    from dotenv import load_dotenv

    load_dotenv(env_path)
    priv_s = (os.environ.get("OPEN_CASE_PRIVATE_KEY") or "").strip()
    pub_s = (os.environ.get("OPEN_CASE_PUBLIC_KEY") or "").strip()

    sk = _decode_private_key_b64(priv_s) if priv_s else None
    if sk is not None:
        if not pub_s:
            new_pub = base64.b64encode(
                sk.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
            ).decode()
            logger.info("OPEN_CASE_PUBLIC_KEY was missing; derived from private and wrote to .env.")
            _persist_signing_keys_env(env_path, priv_s, new_pub)
            os.environ["OPEN_CASE_PUBLIC_KEY"] = new_pub
            return
        if _public_der_matches_private(sk, pub_s):
            return
        logger.warning(
            "OPEN_CASE_PUBLIC_KEY did not match OPEN_CASE_PRIVATE_KEY; replacing public key in .env."
        )
        new_pub = base64.b64encode(
            sk.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        ).decode()
        _persist_signing_keys_env(env_path, priv_s, new_pub)
        os.environ["OPEN_CASE_PUBLIC_KEY"] = new_pub
        return

    if priv_s:
        logger.warning(
            "OPEN_CASE_PRIVATE_KEY is missing or not valid base64/DER Ed25519 (common: truncated paste). "
            "Generating a new keypair; existing seals will not verify against old keys."
        )
    else:
        logger.info("OPEN_CASE signing keys not set; generating new Ed25519 keypair for .env.")

    new_priv, new_pub = generate_keypair()
    _persist_signing_keys_env(env_path, new_priv, new_pub)
    os.environ["OPEN_CASE_PRIVATE_KEY"] = new_priv
    os.environ["OPEN_CASE_PUBLIC_KEY"] = new_pub


def regenerate_signing_keys_in_dotenv(project_root: Path | None = None) -> None:
    """
    Force a new keypair into .env and os.environ. Use when rotating keys or fixing corruption.
    Invalidates prior receipt seals for verification with the old public key.
    """
    root = project_root or Path(__file__).resolve().parent
    env_path = root / ".env"
    new_priv, new_pub = generate_keypair()
    _persist_signing_keys_env(env_path, new_priv, new_pub)
    os.environ["OPEN_CASE_PRIVATE_KEY"] = new_priv
    os.environ["OPEN_CASE_PUBLIC_KEY"] = new_pub
    logger.warning("Regenerated OPEN_CASE signing keys in %s", env_path)
