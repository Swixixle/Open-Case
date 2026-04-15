"""Subject type taxonomy, government level, and branch constants."""

from __future__ import annotations

# Full subject_type enum (CaseFile.subject_type / SubjectProfile.subject_type).
SUBJECT_TYPES: frozenset[str] = frozenset(
    {
        "senator",
        "house_member",
        "executive",
        "vp",
        "federal_judge_scotus",
        "federal_judge_circuit",
        "federal_judge_district",
        "federal_judge_magistrate",
        "federal_judge_bankruptcy",
        "administrative_law_judge",
        "state_governor",
        "state_legislator",
        "state_judge",
        "state_ag",
        "state_sos",
        "mayor",
        "official",
        "city_council",
        "city_executive",
        "county_sheriff",
        "sheriff",
        "county_prosecutor",
        "district_attorney",
        "state_attorney_general",
        "public_defender_chief",
        "county_clerk",
        "county_assessor",
        "school_board",
        "school_board_member",
        "police_commissioner",
        "police_chief",
        "federal_marshal",
        "zoning_board_member",
        "planning_commission_member",
        "historic_preservation_board_member",
        "utility_board_member",
        "water_board_member",
        "transit_board_member",
        "port_authority_commissioner",
        "airport_authority_member",
        "school_superintendent",
        "university_regent",
        "public_health_officer",
        "fire_commissioner",
        "emergency_management_director",
        "parole_board_member",
        "corrections_commissioner",
        "juvenile_court_officer",
        "city_comptroller",
        "state_treasurer",
        "inspector_general",
        "auditor_general",
        "liquor_control_board_member",
        "gaming_commission_member",
        "professional_licensing_board_member",
        "regional_planning_authority_member",
        "special_district_board_member",
        "flood_control_district_member",
        "public_official",
        # non-person case types (existing)
        "corporation",
        "organization",
    }
)

# Appointed / elected decision-makers added for local–state investigative coverage (registry + UI).
# Tests iterate this to assert source registry parity with SUBJECT_TYPES.
APPOINTED_AND_DECISION_MAKER_SUBJECT_TYPES: frozenset[str] = frozenset(
    {
        "police_commissioner",
        "police_chief",
        "federal_marshal",
        "sheriff",
        "district_attorney",
        "state_attorney_general",
        "public_defender_chief",
        "zoning_board_member",
        "planning_commission_member",
        "historic_preservation_board_member",
        "utility_board_member",
        "water_board_member",
        "transit_board_member",
        "port_authority_commissioner",
        "airport_authority_member",
        "school_board_member",
        "school_superintendent",
        "university_regent",
        "public_health_officer",
        "fire_commissioner",
        "emergency_management_director",
        "parole_board_member",
        "corrections_commissioner",
        "juvenile_court_officer",
        "city_comptroller",
        "state_treasurer",
        "inspector_general",
        "auditor_general",
        "liquor_control_board_member",
        "gaming_commission_member",
        "professional_licensing_board_member",
        "regional_planning_authority_member",
        "special_district_board_member",
        "flood_control_district_member",
    }
)

GOVERNMENT_LEVELS: frozenset[str] = frozenset({"federal", "state", "local"})
BRANCHES: frozenset[str] = frozenset(
    {"legislative", "executive", "judicial", "administrative"}
)
HISTORICAL_DEPTHS: frozenset[str] = frozenset({"full", "career", "recent"})

JUDICIAL_SUBJECT_TYPES: frozenset[str] = frozenset(
    {
        "federal_judge_scotus",
        "federal_judge_circuit",
        "federal_judge_district",
        "federal_judge_magistrate",
        "federal_judge_bankruptcy",
        "administrative_law_judge",
        "state_judge",
    }
)

# Same investigative path as legacy public_official (FEC principal + Congress votes, etc.).
FEC_CONGRESS_PIPELINE_SUBJECT_TYPES: frozenset[str] = frozenset(
    {
        "public_official",
        "senator",
        "house_member",
        "vp",
        "executive",
    }
)


def subject_type_uses_fec_congress_pipeline(subject_type: str | None) -> bool:
    st = (subject_type or "").strip()
    return st in FEC_CONGRESS_PIPELINE_SUBJECT_TYPES


def subject_type_is_judicial(subject_type: str | None) -> bool:
    return (subject_type or "").strip() in JUDICIAL_SUBJECT_TYPES


def default_government_level_for_subject_type(subject_type: str | None) -> str:
    st = (subject_type or "").strip()
    # State court judges are judicial branch but state government (not federal).
    if st == "state_judge":
        return "state"
    if (
        st in JUDICIAL_SUBJECT_TYPES
        or st in {"senator", "house_member", "vp", "executive", "federal_marshal"}
    ):
        return "federal"
    if st.startswith("state_") or st in {"state_governor"}:
        return "state"
    if st in {
        "parole_board_member",
        "corrections_commissioner",
        "university_regent",
        "state_treasurer",
        "gaming_commission_member",
        "professional_licensing_board_member",
        "auditor_general",
        "liquor_control_board_member",
    }:
        return "state"
    if st in {
        "mayor",
        "official",
        "city_council",
        "city_executive",
        "county_sheriff",
        "sheriff",
        "county_prosecutor",
        "district_attorney",
        "county_clerk",
        "county_assessor",
        "school_board",
        "school_board_member",
        "police_commissioner",
        "police_chief",
        "public_defender_chief",
        "zoning_board_member",
        "planning_commission_member",
        "historic_preservation_board_member",
        "utility_board_member",
        "water_board_member",
        "transit_board_member",
        "port_authority_commissioner",
        "airport_authority_member",
        "school_superintendent",
        "public_health_officer",
        "fire_commissioner",
        "emergency_management_director",
        "juvenile_court_officer",
        "city_comptroller",
        "inspector_general",
        "regional_planning_authority_member",
        "special_district_board_member",
        "flood_control_district_member",
    }:
        return "local"
    if st == "public_official":
        return "federal"
    return "federal"


def default_branch_for_subject_type(subject_type: str | None) -> str:
    st = (subject_type or "").strip()
    if st == "administrative_law_judge":
        return "administrative"
    if st in JUDICIAL_SUBJECT_TYPES:
        return "judicial"
    if st in {
        "senator",
        "house_member",
        "state_legislator",
        "city_council",
        "school_board",
        "school_board_member",
    }:
        return "legislative"
    if st in {
        "zoning_board_member",
        "planning_commission_member",
        "historic_preservation_board_member",
        "utility_board_member",
        "water_board_member",
        "transit_board_member",
        "port_authority_commissioner",
        "airport_authority_member",
        "university_regent",
        "liquor_control_board_member",
        "gaming_commission_member",
        "professional_licensing_board_member",
        "regional_planning_authority_member",
        "special_district_board_member",
        "flood_control_district_member",
        "inspector_general",
        "auditor_general",
    }:
        return "administrative"
    if st in {
        "executive",
        "vp",
        "state_governor",
        "mayor",
        "official",
        "city_executive",
        "county_sheriff",
        "sheriff",
        "county_prosecutor",
        "district_attorney",
        "state_attorney_general",
        "state_ag",
        "state_sos",
        "police_commissioner",
        "police_chief",
        "federal_marshal",
        "public_defender_chief",
        "school_superintendent",
        "public_health_officer",
        "fire_commissioner",
        "emergency_management_director",
        "parole_board_member",
        "corrections_commissioner",
        "juvenile_court_officer",
        "city_comptroller",
        "state_treasurer",
    }:
        return "executive"
    if st in {"county_clerk", "county_assessor"}:
        return "executive"
    if st == "public_official":
        return "legislative"
    return "legislative"


def default_historical_depth_for_subject_type(subject_type: str | None) -> str:
    """Default SubjectProfile.historical_depth for new cases (model default is also career)."""
    _ = (subject_type or "").strip()
    return "career"
