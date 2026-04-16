"""Citation URL extraction and claim.sources enrichment (category-scoped)."""

from __future__ import annotations

from services.citation_maps import (
    enrich_claim_sources_from_references,
    ordered_urls_from_perplexity_response,
    references_payload_from_ordered_urls,
)


def test_ordered_urls_prefers_first_non_empty_list() -> None:
    data = {
        "citations": [
            "https://a.example/1",
            {"url": "https://b.example/2"},
        ]
    }
    assert ordered_urls_from_perplexity_response(data) == [
        "https://a.example/1",
        "https://b.example/2",
    ]


def test_ordered_urls_allows_duplicate_urls_preserves_positions() -> None:
    data = {
        "citations": [
            "https://same.example/x",
            "https://same.example/x",
        ]
    }
    assert ordered_urls_from_perplexity_response(data) == [
        "https://same.example/x",
        "https://same.example/x",
    ]


def test_ordered_urls_from_message_citations() -> None:
    data = {
        "choices": [
            {
                "message": {
                    "citations": [
                        {"url": "https://m.example/1"},
                    ]
                }
            }
        ]
    }
    assert ordered_urls_from_perplexity_response(data) == ["https://m.example/1"]


def test_references_payload_indices() -> None:
    refs = references_payload_from_ordered_urls(
        ["https://a.example/", "https://b.example/"]
    )
    assert refs == [
        {"index": 1, "url": "https://a.example/"},
        {"index": 2, "url": "https://b.example/"},
    ]


def test_enrich_claim_sources_appends_urls_not_touching_claim_text() -> None:
    claims = [
        {
            "claim": "Fact one [1][3]",
            "source": "[1][3]",
            "type": "fact",
        }
    ]
    refs = [
        {"index": 1, "url": "https://one.example/"},
        {"index": 2, "url": "https://two.example/"},
        {"index": 3, "url": "https://three.example/"},
    ]
    enrich_claim_sources_from_references(claims, refs)
    assert claims[0]["claim"] == "Fact one [1][3]"
    assert claims[0]["source"] == "[1][3]"
    assert claims[0]["sources"] == [
        "https://one.example/",
        "https://three.example/",
    ]


def test_enrich_skips_without_brackets() -> None:
    c = {"claim": "x", "source": "https://direct.example/", "sources": []}
    enrich_claim_sources_from_references([c], [{"index": 1, "url": "https://a/"}])
    assert c["sources"] == []
