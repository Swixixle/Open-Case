"""Production-safe enrichment: banned language, finding schema, narrative validation."""

from services.enrichment_signing import claims_to_findings
from services.enrichment_service import validate_narrative


def test_validate_narrative_flags_corrupt() -> None:
    _, flags = validate_narrative("Some records show conduct that was corrupt in nature.")
    assert "corrupt" in flags


def test_validate_narrative_clean() -> None:
    _, flags = validate_narrative("Public records document filings from 2024.")
    assert flags == []


def test_claims_to_findings_empty_sources_low_confidence() -> None:
    rows = claims_to_findings(
        [{"claim": "A disclosed amount.", "type": "fact", "source": "", "date": "2020-01-01"}]
    )
    assert len(rows) == 1
    assert rows[0]["confidence"] == "low"
    assert rows[0]["needs_human_review"] is True
    assert rows[0]["sources"] == []


def test_claims_to_findings_with_source_medium() -> None:
    rows = claims_to_findings(
        [
            {
                "claim": "A disclosed amount.",
                "type": "fact",
                "source": "https://example.gov/doc",
                "date": "2020-01-01",
            }
        ]
    )
    assert rows[0]["confidence"] == "medium"
    assert rows[0]["needs_human_review"] is False
    assert "https://example.gov/doc" in rows[0]["sources"]
