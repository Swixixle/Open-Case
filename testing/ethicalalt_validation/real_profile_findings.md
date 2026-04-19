# EthicalAlt mapper — real-profile findings (compact)

Generated from `validate_real_profiles.py` against local EthicalAlt `deep_research_output` (monolithic `*_deep.json` + per-brand category folders). Re-run the script to refresh numbers.

## Summary table

| Profile | Source incidents | Mapper incidents | Donation fixtures | Lobbying (mapper) | Date coverage\* | Notes |
|--------|-------------------|------------------|-------------------|-------------------|-----------------|-------|
| target (target_deep.json) | 37 | 37 | 0 | 0 | ~0.84 | Labor/env/legal; no political category. |
| altria/ | 85 | 85 | 0 | 0 | ~0.96 | Regulatory/tobacco; year/month dates common. |
| amazon/ | 78 | 78 | 0 | 0 | ~0.90 | Mixed institutional/labor; “contributed to” env false-positive removed. |
| cigna/ | 66 | 66 | 0 | 0 | ~0.98 | |
| comcast/ | 107 | 107 | 0 | ~2 | ~0.79 | Some lobbying-tagged text. |
| mcdonalds/ | 97 | 97 | 0 | 0 | ~0.77 | |
| nestle/ | 135 | 135 | ~1 | 0 | ~0.89 | Charitable “donated” recall wording may still surface as donation-like. |
| pepsico/ | 154 | 154 | 0 | 0 | ~0.73 | Highest missing-date share in sample. |
| philip-morris/ | 107 | 107 | 0 | ~3 | ~0.96 | Lobbying phrases present; not folded into donation fixtures. |
| tyson-foods/ | 119 | 119 | 0 | 0 | ~0.82 | |
| unitedhealth/ | 74 | 74 | 0 | 0 | ~0.93 | |

\*`date_coverage_rate` = share of mapper incidents with a normalized `YYYY-MM-DD` (including month/year–only sources anchored to first of month/year).

## Repeated patterns (evidence-driven)

1. **Date strings `YYYY-MM` and `YYYY`** appeared across many EthicalAlt JSON lines. **Change:** extend `normalize_date()` to map month-only → `YYYY-MM-01` and year-only → `YYYY-01-01` (precision documented in code).
2. **False donation classification** from **“contributed to”** matching non-political environmental text (e.g. Amazon data-center nitrate). **Change:** remove bare `contributed to` from donation keyword list; keep `contributed $` and campaign-specific phrases.
3. **Zero donation fixtures** on most profiles is **expected** when text is settlements/labor/env without campaign/PAC language. Not a failure by itself.
4. **Lobbying** appears in some brands (e.g. comcast, philip-morris) as **mapper `lobbying_expenditure`**, not as strict donation rows — boundary preserved.

## Next refinement candidates (only if needed after more runs)

- Charitable / product-recall “donated” vs FEC-style donation (needs clearer rules before touching inclusion).
- `amount_usd` is still **not** merged into amount parsing (intentionally conservative).
