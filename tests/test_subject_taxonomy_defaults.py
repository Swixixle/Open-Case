"""Defaults for CaseFile / SubjectProfile taxonomy fields."""

from __future__ import annotations

from core.subject_taxonomy import (
    BRANCHES,
    GOVERNMENT_LEVELS,
    SUBJECT_TYPES,
    default_branch_for_subject_type,
    default_government_level_for_subject_type,
)


def test_state_judge_defaults_state_judicial() -> None:
    assert default_government_level_for_subject_type("state_judge") == "state"
    assert default_branch_for_subject_type("state_judge") == "judicial"


def test_all_registered_subject_types_have_valid_level_and_branch() -> None:
    for st in SUBJECT_TYPES:
        gl = default_government_level_for_subject_type(st)
        br = default_branch_for_subject_type(st)
        assert gl in GOVERNMENT_LEVELS, (st, gl)
        assert br in BRANCHES, (st, br)
