"""Public demo routes (OPEN_CASE_PUBLIC_DEMO)."""

from __future__ import annotations

from signing import generate_keypair


def test_demo_routes_hidden_by_default(client):
    r = client.get("/api/v1/demo/cohort")
    assert r.status_code == 404


def test_demo_cohort_when_enabled(client, monkeypatch):
    monkeypatch.setenv("OPEN_CASE_PUBLIC_DEMO", "1")
    r = client.get("/api/v1/demo/cohort")
    assert r.status_code == 200
    data = r.json()
    assert "figures" in data
    assert len(data["figures"]) == 7


def test_demo_investigate_with_stubbed_pipeline(client, monkeypatch):
    priv, pub = generate_keypair()
    monkeypatch.setenv("OPEN_CASE_PRIVATE_KEY", priv)
    monkeypatch.setenv("OPEN_CASE_PUBLIC_KEY", pub)
    monkeypatch.setenv("OPEN_CASE_PUBLIC_DEMO", "1")

    async def _fake_execute(db, case_id, request, background_tasks, **kwargs):
        return {
            "case_id": str(case_id),
            "signals_detected": 0,
            "signals": [],
            "errors": [],
            "source_statuses": [],
        }

    monkeypatch.setattr(
        "routes.demo.execute_investigation_for_case",
        _fake_execute,
    )

    r = client.post("/api/v1/demo/investigate", json={"max_figures": 1})
    assert r.status_code == 200
    data = r.json()
    assert len(data["figures"]) == 1
    assert data["export_text_plain"]
    assert data["export_csv"]
    share = data["figures"][0].get("share_report_url") or ""
    assert "/api/v1/cases/" in share and "/report/view" in share
