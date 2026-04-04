from __future__ import annotations

from engines.signal_scorer import evaluate_confirmation_status


def test_confirmation_requires_two_relevance_indicators() -> None:
    base = {
        "has_collision": False,
        "direction_verified": True,
        "relevance_score": 0.6,
        "subject_is_sponsor": False,
        "subject_is_cosponsor": False,
        "has_lda_filing": False,
        "has_regulatory_comment": False,
        "has_hearing_appearance": False,
    }
    r = evaluate_confirmation_status({**base})
    assert r["relevance_indicator_count"] == 1
    assert r["confirmed"] is False

    r2 = evaluate_confirmation_status({**base, "has_lda_filing": True})
    assert r2["relevance_indicator_count"] == 2
    assert r2["confirmed"] is True


def test_collision_blocks_confirmation_even_with_indicators() -> None:
    r = evaluate_confirmation_status(
        {
            "has_collision": True,
            "direction_verified": True,
            "relevance_score": 0.9,
            "subject_is_sponsor": True,
            "subject_is_cosponsor": False,
            "has_lda_filing": True,
            "has_regulatory_comment": True,
            "has_hearing_appearance": False,
        }
    )
    assert r["confirmed"] is False
    assert r["confirmation_checks"]["identity_resolved"] is False
