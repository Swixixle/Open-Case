"""perplexity_router classification and claim URL enrichment (no network)."""

from __future__ import annotations

from services.perplexity_router import (
    ResearchPhase1Kind,
    classify_enrichment_phase1_template_index,
    classify_senator_deep_research_phase1,
    classify_staff_network_phase1,
    enrich_claims_with_inline_urls,
)


def test_classify_senator_ethics_is_deep():
    assert (
        classify_senator_deep_research_phase1("ethics_and_investigations")
        == ResearchPhase1Kind.perplexity_deep
    )


def test_classify_senator_financial_is_gemini_first():
    assert (
        classify_senator_deep_research_phase1("financial_disclosures")
        == ResearchPhase1Kind.gemini_first
    )


def test_classify_senator_donor_is_sonar():
    assert (
        classify_senator_deep_research_phase1("donor_vs_vote_record")
        == ResearchPhase1Kind.perplexity_sonar
    )


def test_classify_enrichment_templates():
    assert classify_enrichment_phase1_template_index(0) == ResearchPhase1Kind.gemini_first
    assert classify_enrichment_phase1_template_index(1) == ResearchPhase1Kind.perplexity_deep
    assert classify_enrichment_phase1_template_index(2) == ResearchPhase1Kind.perplexity_sonar


def test_staff_default_sonar():
    import os

    from unittest.mock import patch

    with patch.dict(os.environ, {"STAFF_NETWORK_TRY_GEMINI": ""}, clear=False):
        assert classify_staff_network_phase1() == ResearchPhase1Kind.perplexity_sonar


def test_staff_gemini_when_env():
    import os

    from unittest.mock import patch

    with patch.dict(os.environ, {"STAFF_NETWORK_TRY_GEMINI": "1"}):
        assert classify_staff_network_phase1() == ResearchPhase1Kind.gemini_first


def test_enrich_claims_inline_urls():
    claims: list[dict] = [
        {"claim": "Filed disclosure", "source": "Per https://efts.senate.gov/foo (2024)."}
    ]
    enrich_claims_with_inline_urls(claims)
    assert claims[0].get("sources") == ["https://efts.senate.gov/foo"]
