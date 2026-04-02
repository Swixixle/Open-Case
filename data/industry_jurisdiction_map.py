from __future__ import annotations

COMMITTEE_AGENCY_MAP: dict[str, list[str]] = {
    "Senate Banking": ["CFPB", "SEC", "TREAS", "FDIC", "OCC", "NCUA", "HUD"],
    "Senate Finance": ["IRS", "TREAS", "CMS", "SSA"],
    "Senate Commerce": ["FTC", "FCC", "DOT", "NHTSA", "FAA", "FMC"],
    "Senate Health": ["FDA", "CMS", "CDC", "NIH", "HHS"],
    "Senate Energy": ["DOE", "FERC", "EPA", "NRC"],
    "Senate Environment": ["EPA", "CEQ", "NOAA"],
    "Senate Armed Services": ["DOD", "DARPA", "ARMY", "NAVY", "USAF"],
    "Senate Agriculture": ["USDA", "FSIS", "AMS", "RMA"],
    "Senate Judiciary": ["DOJ", "FBI", "ATF", "BOP"],
    "Senate Foreign Relations": ["DOS", "USAID", "EXIM"],
}


def get_agencies_for_committees(committee_names: list[str]) -> list[str]:
    agencies: set[str] = set()
    for name in committee_names:
        for key, vals in COMMITTEE_AGENCY_MAP.items():
            if key.lower() in name.lower():
                agencies.update(vals)
    return list(agencies)


COMMITTEE_CHRG_CODES: dict[str, str] = {
    "Senate Banking": "SSBK",
    "Senate Finance": "SSFI",
    "Senate Commerce": "SSCM",
    "Senate Health": "SSHR",
    "Senate Energy": "SSEG",
    "Senate Environment": "SSEV",
    "Senate Armed Services": "SSAS",
    "Senate Agriculture": "SSAF",
    "Senate Judiciary": "SSJU",
    "Senate Foreign Relations": "SSFR",
    "Senate Intelligence": "SLIN",
}


def get_chrg_codes_for_committees(committee_names: list[str]) -> list[str]:
    codes: list[str] = []
    for name in committee_names:
        for key, code in COMMITTEE_CHRG_CODES.items():
            if key.lower() in name.lower():
                codes.append(code)
    return list(set(codes))


INDUSTRY_JURISDICTION_MAP: dict[str, list[str]] = {
    "insurance": ["Senate Commerce", "Senate Finance", "Senate Banking"],
    "bank": ["Senate Banking", "Senate Finance"],
    "financial": ["Senate Banking", "Senate Finance"],
    "mortgage": ["Senate Banking", "Senate Finance"],
    "energy": ["Senate Energy", "Senate Environment"],
    "oil": ["Senate Energy", "Senate Environment"],
    "gas": ["Senate Energy", "Senate Environment"],
    "chemical": ["Senate Environment", "Senate Commerce"],
    "pharma": ["Senate Health", "Senate Finance"],
    "health": ["Senate Health", "Senate Finance"],
    "hospital": ["Senate Health", "Senate Finance"],
    "defense": ["Senate Armed Services"],
    "aerospace": ["Senate Armed Services", "Senate Commerce"],
    "tech": ["Senate Commerce", "Senate Science"],
    "telecom": ["Senate Commerce"],
    "agriculture": ["Senate Agriculture"],
    "farm": ["Senate Agriculture"],
    "railroad": ["Senate Commerce"],
    "transport": ["Senate Commerce"],
    "real estate": ["Senate Banking", "Senate Finance"],
    "builder": ["Senate Banking", "Senate Commerce"],
}

# Maps shorthand labels to Senate assignment link codes and canonical name substrings.
JURISDICTION_MATCHERS: dict[str, tuple[frozenset[str], tuple[str, ...]]] = {
    "Senate Commerce": (
        frozenset({"SSCM"}),
        ("commerce, science, and transportation",),
    ),
    "Senate Finance": (
        frozenset({"SSFI"}),
        ("committee on finance",),
    ),
    "Senate Banking": (
        frozenset({"SSBK"}),
        ("banking, housing, and urban affairs",),
    ),
    "Senate Energy": (
        frozenset({"SSEG"}),
        ("energy and natural resources",),
    ),
    "Senate Environment": (
        frozenset({"SSEV"}),
        ("environment and public works",),
    ),
    "Senate Health": (
        frozenset({"SSHR"}),
        ("health, education, labor, and pensions",),
    ),
    "Senate Armed Services": (
        frozenset({"SSAS"}),
        ("armed services",),
    ),
    "Senate Science": (
        frozenset({"SSCM"}),
        ("commerce, science, and transportation",),
    ),
    "Senate Agriculture": (
        frozenset({"SSAF"}),
        ("agriculture, nutrition, and forestry",),
    ),
}


def get_jurisdictions_for_donor(donor_name: str, connected_org_name: str) -> list[str]:
    """Match FEC donor / employer text to Senate committee jurisdiction labels (shorthand)."""
    blob = f"{donor_name or ''} {connected_org_name or ''}".lower()
    out: list[str] = []
    seen: set[str] = set()
    for keyword, labels in INDUSTRY_JURISDICTION_MAP.items():
        if keyword in blob:
            for lab in labels:
                if lab not in seen:
                    seen.add(lab)
                    out.append(lab)
    return out


def jurisdiction_label_matches_committee(
    jurisdiction_label: str,
    committee_name: str,
    committee_code: str,
) -> bool:
    """True if a shorthand jurisdiction (e.g. 'Senate Finance') fits a Senate assignment row."""
    spec = JURISDICTION_MATCHERS.get(jurisdiction_label.strip())
    if not spec:
        return False
    codes, substrings = spec
    code_u = (committee_code or "").strip().upper()
    if code_u and code_u in codes:
        return True
    name_l = (committee_name or "").lower()
    return any(s in name_l for s in substrings)
