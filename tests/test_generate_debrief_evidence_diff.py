"""Tests for compact --check diff in scripts/generate_debrief_evidence.py."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_generator():
    path = REPO_ROOT / "scripts" / "generate_debrief_evidence.py"
    spec = importlib.util.spec_from_file_location("generate_debrief_evidence", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gen():
    return _load_generator()


def test_format_debrief_evidence_diff_top_level_keys(gen):
    committed = {"schema_version": 1, "claims": {}}
    fresh = {"schema_version": 1, "generator": "x", "claims": {}}
    text = gen._format_debrief_evidence_diff(committed, fresh)
    assert "top-level" in text
    assert "generator" in text or "only in" in text


def test_format_debrief_evidence_diff_evidence_sha256(gen):
    base = {
        "schema_version": 1,
        "claims": {
            "claim_001": {
                "statement": "same",
                "evidence": [
                    {
                        "type": "file_list_sha256",
                        "label": "sorted_relative_paths_all_python_files",
                        "sha256": "aaa",
                        "path_count": 1,
                    }
                ],
            }
        },
    }
    other = copy.deepcopy(base)
    other["claims"]["claim_001"]["evidence"][0]["sha256"] = "bbb"
    text = gen._format_debrief_evidence_diff(base, other)
    assert "sha256" in text
    assert "aaa" in text
    assert "bbb" in text


def test_format_debrief_evidence_diff_only_in_regenerated(gen):
    committed = {
        "schema_version": 1,
        "claims": {
            "claim_001": {
                "statement": "s",
                "evidence": [],
            }
        },
    }
    fresh = {
        "schema_version": 1,
        "claims": {
            "claim_001": {
                "statement": "s",
                "evidence": [
                    {
                        "type": "file_list_sha256",
                        "label": "sorted_relative_paths_all_python_files",
                        "sha256": "z",
                        "path_count": 9,
                    }
                ],
            }
        },
    }
    text = gen._format_debrief_evidence_diff(committed, fresh)
    assert "only in regenerated" in text


def test_build_document_matches_check_strip(gen):
    """Regenerated doc should match itself after timestamp strip (sanity)."""
    doc = gen.build_document()
    doc2 = json.loads(json.dumps(doc))
    assert gen._without_timestamp(doc) == gen._without_timestamp(doc2)
