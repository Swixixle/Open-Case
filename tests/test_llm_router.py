"""Tier classification for story-angle LLM routing (no network)."""

from __future__ import annotations

from services.llm_router import TaskTier, classify_story_angles_tier


def test_tier_simple_sparse_dossier():
    d = {
        "subject": {"name": "X", "state": "ST"},
        "deep_research": {"categories": {"a": "short"}},
        "pattern_alerts": [],
    }
    assert classify_story_angles_tier(d) == TaskTier.simple


def test_tier_complex_high_suspicion_score():
    d = {
        "subject": {"name": "X", "state": "ST"},
        "deep_research": {"categories": {}},
        "pattern_alerts": [{"rule_id": "GEO_MISMATCH_V1", "suspicion_score": 0.93}],
    }
    assert classify_story_angles_tier(d) == TaskTier.complex


def test_tier_complex_soft_bundle_rule():
    d = {
        "subject": {"name": "X", "state": "ST"},
        "deep_research": {"categories": {}},
        "pattern_alerts": [{"rule_id": "SOFT_BUNDLE_V1", "suspicion_score": 0.1}],
    }
    assert classify_story_angles_tier(d) == TaskTier.complex


def test_tier_medium_default():
    d = {
        "subject": {"name": "X", "state": "ST"},
        "deep_research": {"categories": {"x": "y"}},
        "pattern_alerts": [
            {"rule_id": "A", "suspicion_score": 0.4},
            {"rule_id": "B", "suspicion_score": 0.3},
        ],
    }
    assert classify_story_angles_tier(d) == TaskTier.medium
