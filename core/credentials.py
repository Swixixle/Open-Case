from __future__ import annotations

import os
from typing import Any


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
        },
        "congress": {
            "env_var": "CONGRESS_API_KEY",
            "required": False,
            "fallback": None,
            "rate_limit_per_hour": 5000,
            "note": "Free registration at api.congress.gov",
        },
        "regulations": {
            "env_var": "REGULATIONS_GOV_API_KEY",
            "required": False,
            "fallback": None,
            "rate_limit_per_hour": 1000,
            "note": "Free registration at api.data.gov",
        },
        "lda": {
            "env_var": "LDA_API_KEY",
            "required": False,
            "fallback": None,
            "rate_limit_per_hour": None,
            "note": "Senate LDA is public, no key required. Key field reserved for future.",
            "public_api": True,
        },
        "govinfo": {
            "env_var": "GOVINFO_API_KEY",
            "required": False,
            "fallback": None,
            "rate_limit_per_hour": 2000,
            "note": "Free registration at api.govinfo.gov",
        },
        "open_case_signing": {
            "env_var": "OPEN_CASE_PRIVATE_KEY",
            "required": True,
            "fallback": None,
            "note": "Ed25519 private key. Auto-generated on first boot if missing.",
        },
    }

    @classmethod
    def get_credential(cls, adapter_name: str) -> str | None:
        spec = cls.ADAPTERS.get(adapter_name)
        if not spec:
            raise ValueError(f"Unknown credential adapter: {adapter_name!r}")
        env_var = spec["env_var"]
        raw = os.environ.get(env_var, "").strip()
        if raw:
            return raw
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
            }
        env_var = spec["env_var"]
        key_present = bool(os.environ.get(env_var, "").strip())
        note = str(spec.get("note") or "")

        if spec.get("public_api"):
            return {
                "adapter": adapter_name,
                "status": "available",
                "key_present": key_present,
                "note": note,
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
        }

    @classmethod
    def get_all_statuses(cls) -> list[dict[str, Any]]:
        return [cls.get_adapter_status(name) for name in sorted(cls.ADAPTERS.keys())]
