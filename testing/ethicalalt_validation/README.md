# EthicalAlt mapper validation (real profiles)

Runs the same conservative mapper as `scripts/ethicalalt_to_open_case.py` against EthicalAlt `*_deep.json` exports (via `testing/ethicalalt_mapper/profile_adapter.py`).

## Run

From the Open Case repository root:

```bash
python3 testing/ethicalalt_validation/validate_real_profiles.py
```

The script discovers JSON under:

- Current working directory (`*_deep.json`)
- `testing/ethicalalt_mapper/data/` (copy files here; gitignored)
- `/Users/alexmaksimovich/ETHICAL_ALTERNATIVES/server/deep_research_output/` (if present)

## Also available

Faster per-file smoke: `testing/ethicalalt_mapper/run_real_profile_smoke.py`
