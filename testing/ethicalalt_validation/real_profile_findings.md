# EthicalAlt mapper — real-profile findings (compact)

Generated from `validate_real_profiles.py` against local EthicalAlt `deep_research_output` (monolithic `*_deep.json` + per-brand category folders). Re-run the script to refresh numbers.

## Summary table

| Profile | Source incidents | Mapper incidents | Donation fixtures | Lobbying (mapper) | Date coverage\* | Notes |
|--------|-------------------|------------------|-------------------|-------------------|-----------------|-------|
| target (target_deep.json) | 37 | 37 | 0 | 0 | ~0.84 | Labor/env/legal; no political category. |
| altria/ | 85 | 85 | 0 | 0 | ~0.96 | Regulatory/tobacco; year/month dates common. |
| amazon/ | 78 | 78 | 0 | 0 | ~0.90 | “contributed to” env false-positive removed earlier. |
| cigna/ | 66 | 66 | 0 | 0 | ~0.98 | |
| comcast/ | 107 | 107 | 0 | ~2 | ~0.79 | Lobbying-tagged text; not in donation fixtures. |
| mcdonalds/ | 97 | 97 | 0 | 0 | ~0.77 | |
| nestle/ | 135 | 135 | **0** | 0 | ~0.89 | Charitable “donated to a charitable organization” excluded from strict donations (see below). |
| pepsico/ | 154 | 154 | 0 | 0 | ~0.73 | Highest missing-date share in sample. |
| philip-morris/ | 107 | 107 | 0 | ~3 | ~0.96 | Lobbying phrases; not in donation fixtures. |
| tyson-foods/ | 119 | 119 | 0 | 0 | ~0.82 | |
| unitedhealth/ | 74 | 74 | 0 | 0 | ~0.93 | |

\*`date_coverage_rate` = share of mapper incidents with a normalized `YYYY-MM-DD` (including month/year–only sources anchored to first of month/year).

## Repeated patterns (evidence-driven)

1. **Date strings `YYYY-MM` / `YYYY`** — `normalize_date()` maps to first of month/year (documented precision).
2. **Bare `contributed to`** — removed from donation keywords (environmental false positives).
3. **Generic `donate` / `donated` / donation markers without electoral context** — across 11 profiles the **only** strict donation fixture was Nestlé product recall text (“donated to a charitable organization”). That **contaminated 100% of donation fixtures** when present. **Change:** `is_political_donation_context()` + `_donation_or_unknown()` — strict `donation` event type now requires plausible campaign-finance cues (PAC, campaign, FEC, Friends of …, senate/congress, etc.). Charity-only lines → `unknown_political`.
4. **Zero donation fixtures** on the full 11-profile run is **expected** after the above: EthicalAlt corpora here are mostly regulatory/labor/env, not FEC-style giving.
5. **Lobbying** (e.g. comcast, philip-morris) stays out of strict donation rows.

## Deliberately unresolved

- **`amount_usd`** not merged into `parse_amount` (still description-only).
- **Recipient extraction** not expanded (no new NER).
- **No new schema fields** — same dataclasses.

## CI hygiene (repo)

Workflow: `pip install` → `pip upgrade`; job **timeout-minutes** on build/client to avoid hung GitHub runners. Local `pytest` / `verify_documentation` / `npm run build` should match CI steps.
