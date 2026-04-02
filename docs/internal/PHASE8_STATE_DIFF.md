# Phase 8 state diff (Witness Room)

## 1. New files created

- `adapters/regulations.py` — Regulations.gov v4 client, token/Jaccard entity match, `fetch_docket_comments`.
- `adapters/govinfo_hearings.py` — GovInfo CHRG collection + package summary witness search, `search_hearing_witnesses`, `current_congress_number`.
- `tests/test_phase8_confirmation.py` — confirmation rule tests (two relevance indicators, collision gate).

## 2. Files modified (summary)

| File | Change |
|------|--------|
| `data/industry_jurisdiction_map.py` | Added `COMMITTEE_AGENCY_MAP`, `get_agencies_for_committees`, `COMMITTEE_CHRG_CODES`, `get_chrg_codes_for_committees`. |
| `engines/relevance.py` | `+0.25` for `has_regulatory_comment`, `+0.35` for `has_hearing_appearance` (capped at 1.0). |
| `engines/temporal_proximity.py` | `DonorCluster`: witness flags/confidences, `witness_evidence_ids`; `refresh_cluster_scoring`; initial relevance includes new kwargs as false. |
| `engines/signal_scorer.py` | Breakdown + confirmation: new `evaluate_confirmation_status` (identity + direction + ≥2 relevance indicators); sponsorship from sponsor/cosponsor flags. |
| `routes/investigate.py` | After `detect_proximity`, `_ingest_regulations_and_hearings_for_clusters` (LDA already ran earlier); evidence types `regulatory_comment`, `hearing_witness`, `hearing_absence`; source_statuses for regulations/govinfo. |
| `core/credentials.py` | File fallback under `CREDENTIAL_DATA_DIR` (default `/data/.credentials/{adapter}.key`); `write_credential_file`; statuses include `rotatable_without_redeploy`; `file_rotatable` on adapter specs. |
| `routes/system.py` | `POST /api/v1/system/credentials/register` with `X-Admin-Secret` / `ADMIN_SECRET`. |
| `routes/reporting.py` | Supporting-evidence labels for new entry types; report rows expose regulatory/hearing flags from breakdown. |
| `templates/report.html` | Badges: Regulatory Comment (confirmed vs probable), Testified at Hearing. |

## 3. New environment variables

| Variable | Required | Role |
|----------|----------|------|
| `REGULATIONS_GOV_API_KEY` | Optional | Regulations.gov API key (or file via register endpoint). |
| `GOVINFO_API_KEY` | Optional | GovInfo API key (or file). |
| `CREDENTIAL_DATA_DIR` | Optional | Directory for `.key` files (default `/data/.credentials`). |
| `ADMIN_SECRET` | Optional | If unset, `POST .../credentials/register` returns **503** “Admin endpoint not configured.” |

## 4. New evidence `entry_type` values

- `regulatory_comment` — matched Regulations.gov filing/submitter text.
- `hearing_witness` — GovInfo CHRG hearing summary matched donor/org.
- `hearing_absence` — GovInfo search ran (`searched: true`, `match: false`) for receipt transparency.

## 5. New / updated report badges

- **Regulatory Comment** — `match_confidence == confirmed`.
- **Regulatory Comment — probable** — regulatory match at probable confidence.
- **Testified at Hearing** — `has_hearing_appearance`.

## 6. Test count

- **Before:** 3 tests (`test_temporal_clusters.py`).
- **After:** 5 tests (+2 in `test_phase8_confirmation.py`).

## 7. Confirmed signals & Todd Young (plain English)

A **`confirmed: true`** proximity signal no longer means only “jurisdiction OR heavy sponsorship weight.” It now requires a **clean identity** (no collision quarantine), **verified donation–vote direction wording**, and **at least two** of: composite relevance ≥0.5 track (`jurisdictional_match` in checks), **sponsor/cosponsor** on an exemplar vote, **LDA filing** link for that donor cluster, a **Regulations.gov comment** match, or a **hearing** hit in GovInfo. That forces multi-source corroboration before the UI treats a lead as “confirmed.”

For **Todd Young** (or any senator), Phase 8 adds what Phase 7 did not: **committee-scoped** scans of **federal docket comments** and **published hearing packages**, surfaced as evidence rows and badges, plus **negative GovInfo hearing searches** when a search ran but no name matched—so the receipt reflects an honest **“we looked in the hearing record”** audit trail when keys are configured. With keys, a reporter can see whether a donor’s name appears on **Regulations.gov** or in a **GovInfo hearing** tied to the member’s committees, not only FEC timing and LDA.
