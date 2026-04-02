from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Adapters that may read a key from CREDENTIAL_DATA_DIR (see POST .../credentials/register).
_FILE_FALLBACK_ADAPTERS: frozenset[str] = frozenset(
    {"fec", "congress", "regulations", "govinfo"}
)


class CredentialUnavailable(Exception):
    def __init__(self, adapter_name: str, reason: str):
        self.adapter_name = adapter_name
        self.reason = reason
        super().__init__(f"{adapter_name}: {reason}")


class CredentialRegistry:
    """Single source of truth for adapter/API credential lookup."""

    ADAPTERS: dict[str, dict[str, Any]] = {
        "fec": {
            "env_var": "FEC_API_KEY",
            "required": False,
            "fallback": "DEMO_KEY",
            "rate_limit_per_day": 1000,
            "note": "DEMO_KEY is public but rate-limited. Register free at api.open.fec.gov",
            "file_rotatable": True,
        },
        "congress": {
            "env_var": "CONGRESS_API_KEY",
            "required": False,
            "fallback": None,
            "rate_limit_per_hour": 5000,
            "note": "Free registration at api.congress.gov",
            "file_rotatable": True,
        },
        "regulations": {
            "env_var": "REGULATIONS_GOV_API_KEY",
            "required": False,
            "fallback": None,
            "rate_limit_per_hour": 1000,
            "note": "Free registration at api.data.gov",
            "file_rotatable": True,
        },
        "lda": {
            "env_var": "LDA_API_KEY",
            "required": False,
            "fallback": None,
            "rate_limit_per_hour": None,
            "note": "Senate LDA is public, no key required. Key field reserved for future.",
            "public_api": True,
            "file_rotatable": False,
        },
        "govinfo": {
            "env_var": "GOVINFO_API_KEY",
            "required": False,
            "fallback": None,
            "rate_limit_per_hour": 2000,
            "note": "Free registration at api.govinfo.gov",
            "file_rotatable": True,
        },
        "open_case_signing": {
            "env_var": "OPEN_CASE_PRIVATE_KEY",
            "required": True,
            "fallback": None,
            "note": "Ed25519 private key. Auto-generated on first boot if missing.",
            "file_rotatable": False,
        },
    }

    @classmethod
    def credential_file_path(cls, adapter_name: str) -> Path:
        root = Path(os.environ.get("CREDENTIAL_DATA_DIR", "/data/.credentials"))
        return root / f"{adapter_name}.key"

    @classmethod
    def _file_secret(cls, adapter_name: str) -> str | None:
        if adapter_name not in _FILE_FALLBACK_ADAPTERS:
            return None
        p = cls.credential_file_path(adapter_name)
        if not p.is_file():
            return None
        try:
            return p.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    @classmethod
    def get_credential(cls, adapter_name: str) -> str | None:
        spec = cls.ADAPTERS.get(adapter_name)
        if not spec:
            raise ValueError(f"Unknown credential adapter: {adapter_name!r}")
        env_var = spec["env_var"]
        raw = os.environ.get(env_var, "").strip()
        if raw:
            return raw
        file_secret = cls._file_secret(adapter_name)
        if file_secret:
            return file_secret
        if spec.get("fallback") is not None:
            return str(spec["fallback"])
        if spec.get("required"):
            raise CredentialUnavailable(adapter_name, f"Missing {env_var} and no fallback")
        return None

    @classmethod
    def get_adapter_status(cls, adapter_name: str) -> dict[str, Any]:
        spec = cls.ADAPTERS.get(adapter_name)
        if not spec:
            return {
                "adapter": adapter_name,
                "status": "unknown",
                "key_present": False,
                "note": "Not registered in CredentialRegistry",
                "rotatable_without_redeploy": False,
            }
        env_var = spec["env_var"]
        env_present = bool(os.environ.get(env_var, "").strip())
        file_present = bool(cls._file_secret(adapter_name))
        key_present = env_present or file_present
        note = str(spec.get("note") or "")
        rotatable = bool(spec.get("file_rotatable"))

        if spec.get("public_api"):
            return {
                "adapter": adapter_name,
                "status": "available",
                "key_present": key_present,
                "note": note,
                "rotatable_without_redeploy": rotatable,
            }
        if key_present:
            status = "available"
        elif spec.get("fallback") is not None:
            status = "fallback"
        else:
            status = "unavailable"
        return {
            "adapter": adapter_name,
            "status": status,
            "key_present": key_present,
            "note": note,
            "rotatable_without_redeploy": rotatable,
        }

    @classmethod
    def get_all_statuses(cls) -> list[dict[str, Any]]:
        return [cls.get_adapter_status(name) for name in sorted(cls.ADAPTERS.keys())]

    @classmethod
    def write_credential_file(cls, adapter_name: str, api_key: str) -> Path:
        """Persist API key for adapter under CREDENTIAL_DATA_DIR (mode 0600)."""
        if adapter_name not in _FILE_FALLBACK_ADAPTERS:
            raise ValueError(f"Adapter {adapter_name!r} does not support file credentials")
        key = (api_key or "").strip()
        if not key:
            raise ValueError("api_key is empty")
        p = cls.credential_file_path(adapter_name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(key, encoding="utf-8")
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
        return p
