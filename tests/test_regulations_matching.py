"""Phase 9A — Regulations.gov donor / submitter matching (Jaccard-style)."""

from __future__ import annotations

from adapters.regulations import _match_confidence, _normalize_tokens


def test_exact_match_is_confirmed() -> None:
    assert _match_confidence("MASS MUTUAL", "MASS MUTUAL") == "confirmed"


def test_probable_match_massmutual_above_threshold() -> None:
    """CamelCase split + token Jaccard ≥ 0.6 yields probable."""
    assert (
        _match_confidence("MassMutual Financial Group", "Mass Mutual") == "probable"
    )


def test_legal_noise_tokens_do_not_count() -> None:
    """LLC / PAC / Corp-style tokens are stripped before scoring."""
    a = _normalize_tokens("Foo PAC LLC")
    b = _normalize_tokens("Foo Incorporated")
    assert "llc" not in a
    assert "pac" not in a
    assert "corp" not in b
    assert "foo" in a and "foo" in b


def test_negative_match_below_threshold() -> None:
    """Unrelated 'Mass …' entities must not match Mass Mutual."""
    assert _match_confidence("Mass Construction LLC", "Mass Mutual") is None
