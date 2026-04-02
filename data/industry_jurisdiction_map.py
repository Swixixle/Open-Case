from __future__ import annotations

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
