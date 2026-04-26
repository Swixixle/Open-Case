"""Subject name fuzzy scoring for /api/v1/subjects/search."""

from core.subject_name_match import subject_name_match_score

_DECOY = [
    "Randall Fuller",
    "Eric Swalwell",
    "Tony Gonzales",
    "Mitch McConnell",
    "Lisa Murkowski",
]


def _assert_first(query: str, expected: str) -> None:
    pool = [
        "Bernie Sanders",
        "Ted Cruz",
        "Tom Cotton",
        "Elizabeth Warren",
        "Chuck Grassley",
        *_DECOY,
    ]
    ranked = sorted(
        pool,
        key=lambda n: (-subject_name_match_score(query, n), n),
    )
    assert ranked[0] == expected, (query, ranked[:5])


def test_exact_match() -> None:
    assert subject_name_match_score("Tom Cotton", "Tom Cotton") == 1.0


def test_typo_last_name_close() -> None:
    s = subject_name_match_score("Coton", "Tom Cotton")
    assert s >= 0.55


def test_typo_warren() -> None:
    s = subject_name_match_score("Warrn", "Elizabeth Warren")
    assert s >= 0.45


def test_prefix_strong() -> None:
    s = subject_name_match_score("tom cot", "Tom Cotton")
    assert s >= 0.84


def test_sander_ranks_sanders_first() -> None:
    _assert_first("sander", "Bernie Sanders")


def test_coton_ranks_cotton_first() -> None:
    _assert_first("coton", "Tom Cotton")


def test_warrn_ranks_warren_first() -> None:
    _assert_first("warrn", "Elizabeth Warren")


def test_cruz_ranks_cruz_first() -> None:
    _assert_first("cruz", "Ted Cruz")


def test_gras_ranks_grassley_first() -> None:
    _assert_first("gras", "Chuck Grassley")


def test_unrelated_name_scores_zero() -> None:
    assert subject_name_match_score("sander", "Randall Fuller") == 0.0
    assert subject_name_match_score("sander", "Eric Swalwell") == 0.0


def test_congress_gov_last_first_order() -> None:
    """Congress.gov list API uses ``Cruz, Ted`` style; must match first+last query."""
    s = subject_name_match_score("Ted Cruz", "Cruz, Ted")
    assert s >= 0.40
