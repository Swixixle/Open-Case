from __future__ import annotations


def compute_relevance_score(
    *,
    has_jurisdictional_match: bool,
    subject_is_sponsor_any: bool,
    subject_is_cosponsor_any: bool,
    has_lda_filing: bool = False,
    has_regulatory_comment: bool = False,
    has_hearing_appearance: bool = False,
) -> float:
    score = 0.0
    if has_jurisdictional_match:
        score += 0.5
    if subject_is_sponsor_any:
        score += 0.4
    elif subject_is_cosponsor_any:
        score += 0.2
    if has_lda_filing:
        score += 0.3
    if has_regulatory_comment:
        score += 0.25
    if has_hearing_appearance:
        score += 0.35
    return min(1.0, score)
