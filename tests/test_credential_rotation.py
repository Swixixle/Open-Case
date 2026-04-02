"""Phase 9A — admin credential registration (Render / file-backed keys)."""

from __future__ import annotations

import pytest

from core.credentials import CredentialRegistry


@pytest.fixture
def cred_dir_tmp(monkeypatch, tmp_path):
    root = tmp_path / "creds"
    root.mkdir()
    monkeypatch.setenv("CREDENTIAL_DATA_DIR", str(root))
    return root


def test_register_credential_requires_admin_secret(client, monkeypatch, cred_dir_tmp) -> None:
    monkeypatch.delenv("ADMIN_SECRET", raising=False)
    r = client.post(
        "/api/v1/system/credentials/register",
        json={"adapter_name": "regulations", "api_key": "k"},
    )
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"].lower()

    monkeypatch.setenv("ADMIN_SECRET", "sekrit")
    r2 = client.post(
        "/api/v1/system/credentials/register",
        json={"adapter_name": "regulations", "api_key": "k"},
    )
    assert r2.status_code == 403


def test_register_unknown_adapter_rejected(client, monkeypatch, cred_dir_tmp) -> None:
    monkeypatch.setenv("ADMIN_SECRET", "sekrit")
    r = client.post(
        "/api/v1/system/credentials/register",
        json={"adapter_name": "not_a_real_adapter", "api_key": "k"},
        headers={"X-Admin-Secret": "sekrit"},
    )
    assert r.status_code == 400
    assert "unknown" in r.json()["detail"].lower()


def test_registered_credential_survives_across_requests(client, monkeypatch, cred_dir_tmp) -> None:
    monkeypatch.setenv("ADMIN_SECRET", "sekrit")
    monkeypatch.delenv("REGULATIONS_GOV_API_KEY", raising=False)
    key = "regulations-file-key-xyz"
    r = client.post(
        "/api/v1/system/credentials/register",
        json={"adapter_name": "regulations", "api_key": key},
        headers={"X-Admin-Secret": "sekrit"},
    )
    assert r.status_code == 200
    assert CredentialRegistry.get_credential("regulations") == key
    # Simulates a later request in the same process (no restart).
    assert CredentialRegistry.get_credential("regulations") == key
