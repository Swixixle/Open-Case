"""
Curated entity matching for **federal** and **local** jurisdictions.

Implementation lives in ``local_entity_matching`` (historic module name). Import from
``utils.entity_matching`` when you want the broader scope; behavior is identical.
"""

from utils.local_entity_matching import (  # noqa: F401
    MATCH_ALIAS,
    MATCH_DIRECT,
    MATCH_NONE,
    MATCH_RELATED_ENTITY,
    aliases_path_for_jurisdiction,
    local_jurisdiction_alias_key,
    local_match_eligible_for_loop_and_timing,
)
