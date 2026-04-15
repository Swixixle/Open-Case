"""Privileged ops routes require ADMIN_SECRET + X-Admin-Secret."""

from __future__ import annotations


def test_clear_adapter_cache_requires_admin(client, monkeypatch) -> None:
    monkeypatch.delenv("ADMIN_SECRET", raising=False)
    r = client.post("/api/v1/admin/clear-cache")
    assert r.status_code == 503

    monkeypatch.setenv("ADMIN_SECRET", "ops-secret")
    r2 = client.post("/api/v1/admin/clear-cache")
    assert r2.status_code == 403

    r3 = client.post(
        "/api/v1/admin/clear-cache",
        headers={"X-Admin-Secret": "ops-secret"},
    )
    assert r3.status_code == 200
    body = r3.json()
    assert "deleted" in body
    assert body.get("message") == "adapter_cache table cleared"


def test_auth_keys_requires_admin(client, monkeypatch) -> None:
    monkeypatch.delenv("ADMIN_SECRET", raising=False)
    r = client.post("/api/v1/auth/keys?handle=keymint_test")
    assert r.status_code == 503

    monkeypatch.setenv("ADMIN_SECRET", "key-admin")
    r2 = client.post("/api/v1/auth/keys?handle=keymint_test")
    assert r2.status_code == 403

    r3 = client.post(
        "/api/v1/auth/keys?handle=keymint_test",
        headers={"X-Admin-Secret": "key-admin"},
    )
    assert r3.status_code == 200
    data = r3.json()
    assert data["handle"] == "keymint_test"
    assert data["api_key"].startswith("open_case_")
