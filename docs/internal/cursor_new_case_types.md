# Open Case — New Case Types: Corporate, Court, Bill, Policy

## Overview

Currently Open Case only supports senator cases. This sprint adds four new case types, each with its own subject schema, adapter set, and pattern rules. The architecture is additive — existing senator cases are unchanged.

All four case types share the same `CaseFile` model, `EvidenceEntry` storage, `Signal` and `PatternAlert` infrastructure, and Ed25519 signing. What changes is the `case_type` field, the adapters that run during investigate, and the pattern rules that fire.

---

## Architecture changes

### 1. Add `case_type` to `CaseFile`

In `models.py`, add:

```python
class CaseType(str, Enum):
    senator = "senator"
    corporation = "corporation"
    court_case = "court_case"
    bill = "bill"
    policy = "policy"
```

Add `case_type: CaseType` column to `CaseFile` with default `"senator"` for backward compatibility. Alembic migration required.

### 2. Add case-type-specific subject fields to `CaseFile`

Add nullable columns:
```python
# Corporation cases
sec_cik: str | None           # SEC Central Index Key (e.g. "0000012927")
fec_committee_id: str | None  # Corporate PAC committee ID (already exists for senator)
lda_registrant_name: str | None

# Court cases
court_case_number: str | None  # e.g. "1:23-cv-01234"
court_name: str | None         # e.g. "S.D.N.Y."
court_listener_id: str | None

# Bill cases
bill_number: str | None        # e.g. "H.R.7147" or "S.1234"
bill_congress: int | None      # e.g. 119
bill_type: str | None          # e.g. "hr", "s", "hjres", "sjres"

# Policy cases
regulations_docket_id: str | None  # e.g. "EPA-HQ-OAR-2021-0317"
agency_name: str | None
federal_register_number: str | None
```

### 3. Route: batch-open accepts case_type

Update `POST /api/v1/cases/batch-open` to accept `case_type` and the relevant subject fields. Existing senator cases continue to work unchanged.

### 4. Route: investigate dispatches by case_type

In `routes/investigate.py`, add a dispatcher at the top of the investigate function:

```python
case_type = case.case_type or CaseType.senator

if case_type == CaseType.senator:
    return await _investigate_senator(case, db, request_body)
elif case_type == CaseType.corporation:
    return await _investigate_corporation(case, db, request_body)
elif case_type == CaseType.court_case:
    return await _investigate_court_case(case, db, request_body)
elif case_type == CaseType.bill:
    return await _investigate_bill(case, db, request_body)
elif case_type == CaseType.policy:
    return await _investigate_policy(case, db, request_body)
```

Move all existing investigate logic into `_investigate_senator`. The other four are new functions.

---

## CASE TYPE 1: Corporation

### What it investigates

Starting from a company name or FEC committee ID, traces:
- Who they gave money to (Schedule B disbursements from their PAC)
- What they're lobbying for (LDA filings by registrant name)
- What government contracts they hold (USASpending)
- SEC enforcement actions and proxy filings
- Pattern: did their lobbying targets vote in their favor?

### Adapters to run

**FEC Schedule B** — pull disbursements FROM the corporate PAC committee:
```python
# This is the reverse of the senator case
# Senator case: Schedule A receipts TO the senator's committee
# Corporation case: Schedule B disbursements FROM the corporate PAC
recipients = fetch_schedule_b(committee_id=case.fec_committee_id, api_key=fec_key)
# Each recipient is a senator or party committee that received money
```

Store each disbursement as `entry_type="corporate_disbursement"` with:
- `recipient_name`
- `recipient_committee_id`
- `disbursement_amount`
- `disbursement_date`
- `disbursement_description`

**LDA** — search by registrant/client name:
```python
lda_filings = search_lda_by_registrant(registrant_name=case.subject_name)
# Returns: issue_codes, specific_lobbying_issues, bill_numbers, filing_year
```

Store as `entry_type="lda_corporate_filing"`.

**USASpending** — search by recipient name (already have adapter):
```python
contracts = search_usa_spending(recipient_name=case.subject_name)
```

Store as `entry_type="federal_contract"`.

**SEC EDGAR** — enforcement actions and proxy filings:
```python
# SEC EDGAR full-text search API (free, no key required)
# https://efts.sec.gov/LATEST/search-index?q="company+name"&dateRange=custom&startdt=2024-01-01&forms=DEF+14A,8-K
sec_filings = fetch_sec_edgar(cik=case.sec_cik, form_types=["DEF14A", "8-K", "10-K"])
```

Store enforcement-related 8-K filings as `entry_type="sec_enforcement"`.
Store DEF 14A (proxy) as `entry_type="sec_proxy"` — extract board member names for revolving door matching.

### Pattern rules for corporations

**`CORPORATE_CAPTURE_V1`**: Corporate PAC disbursed to senator's committee → senator voted on bill where corporation had active LDA filing → vote aligned with corporation's lobbying position.

```python
# The three-hop chain:
# 1. corporate_disbursement to senator X
# 2. lda_corporate_filing with issue_codes matching senator X's committee jurisdiction
# 3. senator X voted Yea on bill covered by LDA filing
```

**`REVOLVING_DOOR_CORPORATE_V1`**: Board member (from SEC proxy) previously held government position → company lobbied on issues in that agency's jurisdiction.

```python
# Match: sec_proxy board member names → congressional staff disclosures → LDA issue codes
```

**`CONTRACT_CAPTURE_V1`**: Company received federal contract from agency → company donated to committee that oversees that agency → appropriations vote in window.

### Open a corporation case

```bash
curl -X POST https://open-case.onrender.com/api/v1/cases/batch-open \
  -H "Authorization: Bearer $OPEN_CASE_API_KEY" \
  -d '{
    "subjects": [{
      "subject_name": "Raytheon Technologies",
      "case_type": "corporation",
      "fec_committee_id": "C00170407",
      "sec_cik": "0001047122",
      "lda_registrant_name": "RTX Corporation"
    }],
    "created_by": "alex"
  }'
```

---

## CASE TYPE 2: Court Case

### What it investigates

Starting from a federal court case number, traces:
- Who are the parties
- Who funded the legal effort (FEC + LDA dark money connections)
- Which judges presided and who appointed them
- Outcome and whether it aligned with political money flows
- Related legislation introduced around the same time

### Primary data source: CourtListener

CourtListener API is free with registration. Base URL: `https://www.courtlistener.com/api/rest/v3/`

Endpoints:
- `/dockets/?docket_number={case_number}&court={court_id}` — get docket
- `/clusters/?docket={docket_id}` — get opinion clusters
- `/parties/?docket={docket_id}` — get parties

**In `adapters/court_listener.py`** (new file):

```python
def fetch_docket(case_number: str, court: str = None, api_key: str = None) -> dict:
    """
    Fetches docket from CourtListener by case number.
    Returns: {docket_id, case_name, court, date_filed, date_terminated,
              parties: [{name, type}], attorneys: [{name, firm}]}
    """

def fetch_opinions(docket_id: str, api_key: str = None) -> list[dict]:
    """
    Fetches opinion texts and metadata for a docket.
    Returns: [{date_filed, judge, opinion_type, plain_text}]
    """
```

Store docket as `entry_type="court_docket"`.
Store each party as `entry_type="court_party"` with `matched_name` set to the party name.
Store opinions as `entry_type="court_opinion"`.

### Cross-reference adapters

After loading court parties, run FEC and LDA cross-reference:

```python
# For each corporate party in the case
for party in corporate_parties:
    # Find their FEC PAC
    fec_results = search_fec_by_name(party.name)
    # Find their LDA filings
    lda_results = search_lda_by_registrant(party.name)
    # Find related legislation (bills mentioning the party or case topic)
    congress_results = search_congress_by_keyword(extract_topic_keywords(case_name))
```

### Pattern rules for court cases

**`LITIGATION_MONEY_V1`**: Corporate litigant donated to senator → senator introduced or co-sponsored bill that would benefit the litigant's position in the case → bill introduced during active litigation.

**`JUDGE_APPOINTMENT_V1`**: Judge who ruled in case was appointed by senator who received donations from winning party.

```python
# judge_name → appointed_by (from Senate confirmation vote records)
# appointed_by bioguide → check against corporate party donations via FEC
```

### Open a court case

```bash
curl -X POST https://open-case.onrender.com/api/v1/cases/batch-open \
  -H "Authorization: Bearer $OPEN_CASE_API_KEY" \
  -d '{
    "subjects": [{
      "subject_name": "FTC v. Meta Platforms",
      "case_type": "court_case",
      "court_case_number": "1:20-cv-03590",
      "court_name": "D.D.C."
    }],
    "created_by": "alex"
  }'
```

---

## CASE TYPE 3: Bill

### What it investigates

Starting from a bill number, reconstructs the full influence map:
- Who sponsored and co-sponsored it
- Who lobbied for and against it (LDA bill-specific filings)
- Who donated to the sponsors in the window around introduction
- How it moved through committee (amendment history)
- Whether it passed and how sponsors voted on amendments

### Primary data source: Congress.gov v3

Already have the API key. Endpoints:
- `/v3/bill/{congress}/{type}/{number}` — bill metadata
- `/v3/bill/{congress}/{type}/{number}/cosponsors` — cosponsor list
- `/v3/bill/{congress}/{type}/{number}/amendments` — amendment history
- `/v3/bill/{congress}/{type}/{number}/committees` — committee referrals
- `/v3/bill/{congress}/{type}/{number}/actions` — full action history

**In `adapters/congress_votes.py`** add `fetch_bill_details(bill_number, congress, bill_type, api_key)`:

```python
def fetch_bill_details(bill_number: str, congress: int, bill_type: str, api_key: str) -> dict:
    """
    Returns full bill record including sponsors, cosponsors, committees,
    amendments, actions, and related bills.
    """
```

Store bill metadata as `entry_type="bill_record"`.
Store each cosponsor as `entry_type="bill_cosponsor"` with `matched_name` = bioguide_id.
Store each amendment as `entry_type="bill_amendment"`.

### LDA bill cross-reference

LDA filings can reference specific bill numbers. Search for LDA filings mentioning this bill:

```python
# Senate LDA API supports bill number search
lda_url = f"https://lda.senate.gov/api/v1/filings/?bill_number={bill_number}"
```

Store as `entry_type="lda_bill_filing"` — these are the lobbyists who specifically worked this bill.

### FEC cross-reference

For each sponsor and cosponsor, run the standard FEC Schedule A fetch to find their donors. This creates the money map: who lobbied for the bill AND who funded its sponsors.

### Pattern rules for bills

**`BILL_CAPTURE_V1`**: Lobbyist registered on this specific bill → their client donated to bill sponsor within 90 days of introduction → bill passed committee with sponsor's support.

**`AMENDMENT_GUTTING_V1`**: Version of the AMENDMENT_TELL rule but anchored to a specific bill. Tracks the complete amendment history and scores each amendment by whether it weakened or strengthened the bill, cross-referenced against which senators voted for weakening amendments and who funded them.

**`COSPONSOR_MONEY_MAP_V1`**: For each cosponsor, compute what fraction of their top 10 donors had active LDA filings on this bill's issue area. Flag cosponsors where >50% of top donors were actively lobbying on the bill.

### Open a bill case

```bash
curl -X POST https://open-case.onrender.com/api/v1/cases/batch-open \
  -H "Authorization: Bearer $OPEN_CASE_API_KEY" \
  -d '{
    "subjects": [{
      "subject_name": "H.R. 7147 - Laken Riley Act",
      "case_type": "bill",
      "bill_number": "7147",
      "bill_congress": 119,
      "bill_type": "hr"
    }],
    "created_by": "alex"
  }'
```

---

## CASE TYPE 4: Policy (Regulatory Rule)

### What it investigates

This is the most powerful case type. Federal agencies publish proposed rules, accept public comments, then issue final rules. The gap between proposed and final rule is where influence is measurable — changes from proposed to final that benefit specific industries are the signal.

Starting from a regulations.gov docket number:
- Pull all public comments (who commented, when, what they said)
- Pull the proposed rule text and final rule text
- Compare: what changed between proposed and final
- Cross-reference commenters against FEC (did they donate to the oversight committee?)
- Cross-reference commenters against LDA (were they registered lobbyists?)
- Flag changes in final rule that align with specific corporate commenter positions

### Primary data source: Regulations.gov

Requires `REGULATIONS_GOV_API_KEY` (free registration at api.data.gov). You have a credential placeholder already.

Endpoints:
- `GET /v4/dockets/{docketId}` — docket metadata
- `GET /v4/documents?filter[docketId]={docketId}&filter[documentType]=Rule` — proposed and final rule documents
- `GET /v4/comments?filter[docketId]={docketId}&page[size]=250` — public comments

**In `adapters/regulations.py`** (already exists, extend it):

```python
def fetch_docket_comments(docket_id: str, api_key: str, max_comments: int = 500) -> list[dict]:
    """
    Fetches public comments for a regulations.gov docket.
    Returns: [{commenter_name, organization, comment_date, comment_text_url, document_id}]
    """

def fetch_rule_documents(docket_id: str, api_key: str) -> dict:
    """
    Returns proposed rule and final rule documents for a docket.
    {proposed: {date, text_url, fr_number}, final: {date, text_url, fr_number}}
    """
```

Store proposed rule as `entry_type="proposed_rule"`.
Store final rule as `entry_type="final_rule"`.
Store each comment as `entry_type="public_comment"` with:
- `matched_name` = commenter organization name (for cross-referencing)
- `comment_date`
- `commenter_type`: "corporate", "individual", "ngo", "government"

### FEC cross-reference on commenters

For each corporate commenter:
```python
# Find their PAC
fec_committee = search_fec_by_name(commenter_organization)
if fec_committee:
    # Find donations to members of the committee that oversees this agency
    donations = fetch_schedule_b(committee_id=fec_committee.id)
    # Cross-reference against oversight committee members
```

### Pattern rules for policies

**`REGULATORY_CAPTURE_V1`**: Corporate commenter donated to oversight committee member → final rule changed in direction favoring commenter's stated position → change occurred between proposed and final rule.

```python
# This requires basic text comparison between proposed and final rule
# Use keyword extraction from comment text vs final rule changes
# Flag when final rule adopts commenter's specific language
```

**`COMMENT_FLOOD_V1`**: Coordinated commenting pattern — multiple organizations with same parent company, same employer, or same industry cluster all submitted comments in a tight window. Similar to SOFT_BUNDLE_V1 but for regulatory comments instead of donations.

```python
# Group comments by organization sector and date
# If 3+ from same sector within 7 days → flag
```

**`REVOLVING_DOOR_REGULATORY_V1`**: Agency official who issued the rule previously worked at a company that benefited from the rule, OR left to join that company within 2 years of the rule.

```python
# Requires: agency staff disclosure records (varies by agency)
# OGE (Office of Government Ethics) has some public disclosure data
# Start with keyword matching on rule text vs former official bios
```

### Open a policy case

```bash
curl -X POST https://open-case.onrender.com/api/v1/cases/batch-open \
  -H "Authorization: Bearer $OPEN_CASE_API_KEY" \
  -d '{
    "subjects": [{
      "subject_name": "EPA Clean Power Plan 2.0",
      "case_type": "policy",
      "regulations_docket_id": "EPA-HQ-OAR-2022-0337",
      "agency_name": "EPA"
    }],
    "created_by": "alex"
  }'
```

---

## Implementation order

Ship in this order — each builds on the previous:

1. **Architecture** — `case_type` enum, new CaseFile columns, Alembic migration, dispatcher in investigate route. No new functionality, just the scaffolding. (1 sprint)

2. **Bill case type** — uses APIs you already have (Congress.gov, FEC, LDA). Highest value-to-effort ratio. (1 sprint)

3. **Policy case type** — adds Regulations.gov (already have placeholder). Most powerful detection. (1 sprint)

4. **Corporation case type** — adds SEC EDGAR adapter. Flips the FEC direction. (1 sprint)

5. **Court case type** — adds CourtListener adapter. Requires new API key registration. (1 sprint)

---

## Tests

For each case type, add to `tests/test_case_types.py`:

1. `test_bill_case_opens_and_investigates` — open H.R. 7147, verify bill_record and bill_cosponsor evidence stored
2. `test_policy_case_opens_and_investigates` — open EPA docket, verify proposed_rule and public_comment evidence stored (mock Regulations.gov)
3. `test_corporation_case_opens_and_investigates` — open Raytheon, verify corporate_disbursement evidence stored
4. `test_court_case_opens_and_investigates` — open FTC v Meta, verify court_docket and court_party evidence stored
5. `test_bill_capture_v1_fires` — seed bill with lobbyist + sponsor + sponsor donation → alert fires
6. `test_regulatory_capture_v1_fires` — seed comment + oversight committee donation + final rule change → alert fires
7. `test_corporate_capture_v1_fires` — seed corporate disbursement + LDA filing + aligned vote → alert fires

---

## Acceptance criteria

- [ ] `POST /api/v1/cases/batch-open` accepts all four new `case_type` values
- [ ] Each case type runs its own adapter set during investigate
- [ ] `GET /api/v1/patterns` returns new rule IDs for each case type
- [ ] Senator cases completely unchanged
- [ ] All existing 139 tests still pass
- [ ] `PATTERN_ENGINE_VERSION` → `3.0`

---

## Commit messages (one per sprint)

```
feat: add case_type scaffold — corporation, court_case, bill, policy with CaseFile columns and investigate dispatcher
feat: bill case type — Congress.gov bill details, cosponsor map, LDA bill filings, BILL_CAPTURE_V1
feat: policy case type — Regulations.gov docket comments, rule comparison, REGULATORY_CAPTURE_V1
feat: corporation case type — FEC Schedule B disbursements, SEC EDGAR, CONTRACT_CAPTURE_V1
feat: court case type — CourtListener docket fetch, party cross-reference, LITIGATION_MONEY_V1
```

---

## After all five case types ship

The system can answer:
- Who funded the senators who voted for this bill?
- Which corporations commented on this rule and then benefited from the final version?
- Who are the top donors to the senators who sit on the committee that oversees the agency that issued this rule?
- Did the company that won this court case donate to the senator who appointed the judge?

That is a complete influence mapping system across all four channels through which money shapes policy: legislation, regulation, litigation, and direct political finance.
