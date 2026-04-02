"""Phase 9A — ≥2 relevance-indicator confirmation standard."""

from __future__ import annotations

from engines.signal_scorer import evaluate_confirmation_status


def _base(**kwargs: bool | float) -> dict:
    data: dict = {
        "has_collision": False,
        "direction_verified": True,
        "relevance_score": 0.1,
        "subject_is_sponsor": False,
        "subject_is_cosponsor": False,
        "has_lda_filing": False,
        "has_regulatory_comment": False,
        "has_hearing_appearance": False,
    }
    data.update(kwargs)
    return data


def test_one_indicator_does_not_confirm() -> None:
    out = evaluate_confirmation_status(
        _base(relevance_score=0.6)
    )  # jurisdictional_match via score ≥ 0.5 only
    assert out["relevance_indicator_count"] == 1
    assert out["confirmed"] is False


def test_two_indicators_confirms() -> None:
    out = evaluate_confirmation_status(_base(relevance_score=0.6, has_lda_filing=True))
    assert out["relevance_indicator_count"] >= 2
    assert out["confirmed"] is True


def test_jurisdictional_plus_lda_confirms() -> None:
    out = evaluate_confirmation_status(_base(relevance_score=0.6, has_lda_filing=True))
    assert out["confirmation_checks"]["jurisdictional_match"] is True
    assert out["confirmation_checks"]["lda_filing"] is True
    assert out["confirmed"] is True


def test_zero_indicators_does_not_confirm() -> None:
    """No indicators → not confirmed even when identity and direction are ok."""
    out = evaluate_confirmation_status(_base(relevance_score=0.05))
    assert out["relevance_indicator_count"] == 0
    assert out["confirmed"] is False
