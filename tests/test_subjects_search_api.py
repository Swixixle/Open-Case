"""GET /api/v1/subjects/search query shape."""

from unittest.mock import AsyncMock, MagicMock, patch


def test_search_accepts_q_alias(client):
    r = client.get("/api/v1/subjects/search", params={"q": "Ted"})
    assert r.status_code == 200
    data = r.json()
    assert "query" in data
    assert data["query"] == "Ted"


def test_search_rejects_empty_name_and_q(client):
    r = client.get("/api/v1/subjects/search", params={})
    assert r.status_code == 422


def test_congress_search_uses_path_based_state_code(client):
    """
    ``GET /v3/member?stateCode=TX`` does not filter; path ``/v3/member/TX`` does.
    See Library of Congress member endpoint docs and live API behavior.
    """
    got: dict[str, str] = {}

    async def aget(url: str, params=None, **kwargs):
        got["url"] = url
        r = MagicMock()
        r.json = lambda: {
            "members": {
                "member": [
                    {
                        "name": "Cruz, Ted",
                        "bioguideId": "C001098",
                        "state": "Texas",
                        "terms": {
                            "item": [
                                {
                                    "chamber": "Senate",
                                    "stateCode": "TX",
                                }
                            ]
                        },
                    }
                ]
            }
        }
        return r

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=aget)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    with (
        patch("core.credentials.CredentialRegistry.get_credential", return_value="k"),
        patch("routes.subjects.httpx.AsyncClient", return_value=mock_client),
    ):
        r = client.get(
            "/api/v1/subjects/search",
            params={"q": "Ted Cruz", "state": "TX"},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["source"] == "congress_gov_api"
    assert "member/TX" in got["url"]
    assert any(c.get("bioguide_id") == "C001098" for c in data["candidates"])
