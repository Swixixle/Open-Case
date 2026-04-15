"""Dossier deep-research claim deduplication (TF cosine merge)."""

from services.dossier_claim_dedup import (
    dedupe_merge_claims,
    extract_primary_entity,
    tf_cosine_similarity,
)


def test_tf_cosine_identical() -> None:
    assert abs(tf_cosine_similarity("foo bar baz", "foo bar baz") - 1.0) < 1e-12


def test_tf_cosine_unrelated() -> None:
    s = tf_cosine_similarity("apple orange", "k1 z9 q2 x4")
    assert s < 0.3


def test_extract_primary_entity_quoted() -> None:
    assert extract_primary_entity('"Acme Corp" filed a report.') == "Acme Corp"


def test_extract_primary_entity_capitalized() -> None:
    t = "David Polyansky joined a lobbying firm after serving as deputy chief of staff."
    assert extract_primary_entity(t) == "David Polyansky"


def test_dedupe_merges_sources() -> None:
    claims = [
        {
            "claim": "David Polyansky registered as a lobbyist for Example Group in 2021.",
            "source": "https://a.example/1",
            "type": "fact",
        },
        {
            "claim": "David Polyansky registered as a lobbyist for Example Group in 2021 per Senate filings.",
            "source": "https://b.example/2",
            "type": "fact",
        },
    ]
    out = dedupe_merge_claims(claims, threshold=0.85)
    assert len(out) == 1
    srcs = out[0].get("sources")
    assert isinstance(srcs, list)
    assert "https://a.example/1" in srcs
    assert "https://b.example/2" in srcs


def test_dedupe_keeps_distinct() -> None:
    claims = [
        {"claim": "Alice Jones donated to the campaign.", "source": "https://x/1"},
        {"claim": "The moon is made of cheese allegedly.", "source": "https://x/2"},
    ]
    out = dedupe_merge_claims(claims, threshold=0.85)
    assert len(out) == 2
