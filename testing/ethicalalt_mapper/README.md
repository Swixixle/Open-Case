# EthicalAlt → Open Case mapper (manual smoke tests)

The mapper lives at `scripts/ethicalalt_to_open_case.py`. EthicalAlt deep exports use a **merged** `incidents` array; `testing/ethicalalt_mapper/profile_adapter.py` flattens that into the shape the mapper expects.

## Run on real JSON

From the Open Case repo root:

```bash
# Use JSON already on disk in EthicalAlt (adjust path if needed)
python3 testing/ethicalalt_mapper/run_real_profile_smoke.py \
  /Users/alexmaksimovich/ETHICAL_ALTERNATIVES/server/deep_research_output/target_deep.json

# Or every *_deep.json next to EthicalAlt deep_research_output
python3 testing/ethicalalt_mapper/run_real_profile_smoke.py --use-default-ethicalalt-dir
```

Or copy `*_deep.json` files into `testing/ethicalalt_mapper/data/` (gitignored) and run with no arguments.

## What to expect

- **Donation fixtures** only include rows classified as `donation` or `pac_contribution` with a **normalized** date. Most EthicalAlt incidents are environmental/labor/settlements — **zero donation fixtures is normal** unless the text looks like campaign/PAC giving.
- **Amounts** are parsed from **description text** only (`amount_usd` is not merged into the mapper by design).
- **Lobbying** should not appear in donation fixtures when descriptions are classified as lobbying.

## Findings template (paste after a run)

```markdown
## Profiles tested
- [brand]: source_incidents → donation_fixtures (mapper_incidents)

## Counts
- Incidents missing normalized date: N
- Classified lobbying_expenditure: N
- Recipients resolved on donation rows: N / donation_fixtures

## Issues
- [ ] Date parsing gaps (examples)
- [ ] Lobbying in donation list (should be none)
- [ ] Recipient extraction too sparse (expected if donation list is empty)

## Next steps
- [ ] Optional: lab-only enrichment from `amount_usd` (not in core mapper until approved)
- [ ] Add political-category samples for donation/PAC/lobbying coverage
```
