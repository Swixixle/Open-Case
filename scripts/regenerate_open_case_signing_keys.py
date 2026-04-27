#!/usr/bin/env python3
"""
Write a fresh Ed25519 PKCS8/SPKI (base64 DER) pair to .env as OPEN_CASE_PRIVATE_KEY / OPEN_CASE_PUBLIC_KEY.

Use when keys are corrupted (e.g. invalid base64 from a truncated copy-paste) or for intentional rotation.
Regenerating invalidates verification of receipts sealed with the previous public key.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from signing import regenerate_signing_keys_in_dotenv


def main() -> int:
    regenerate_signing_keys_in_dotenv(_ROOT)
    env_path = _ROOT / ".env"
    print(f"Updated signing keys in {env_path} (OPEN_CASE_PRIVATE_KEY / OPEN_CASE_PUBLIC_KEY).")
    print("Restart the API process. Prior sealed receipts will verify only with the old public key.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
