"""GET /api/v1/subjects/search query shape."""


def test_search_accepts_q_alias(client):
    r = client.get("/api/v1/subjects/search", params={"q": "Ted"})
    assert r.status_code == 200
    data = r.json()
    assert "query" in data
    assert data["query"] == "Ted"


def test_search_rejects_empty_name_and_q(client):
    r = client.get("/api/v1/subjects/search", params={})
    assert r.status_code == 422
