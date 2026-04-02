# Contributing to Open Case

Thank you for your interest. This document explains how to contribute effectively.

Open Case is civic investigation infrastructure — the kind of project where a
well-placed adapter can expose patterns that took journalists months to find manually.
That's worth doing carefully.

---

## The Most Valuable Thing You Can Do Right Now

Before writing any code: **document a data source.**

The hardest problem in civic investigation is local data. City council minutes.
Municipal contracts. Zoning board decisions. School board votes. County property
transfers. Most of this is technically public record. Almost none of it is in
a database that a computer can query easily.

If you know how to access public records in your jurisdiction — especially records
that aren't in a national database — open an issue with:

- The jurisdiction (city, county, state)
- What data is available (type of records)
- How to access it (URL, form submission, PDF download, API if it exists)
- Whether it requires a FOIA request or is directly accessible
- Any quirks (names are listed last-name-first, dates are in a weird format, etc.)

You don't need to write code. A good data source description is enough to
build an adapter from.

---

## How to Contribute Code

### 1. Find something to work on

Check the [Issues](https://github.com/Swixixle/Open-Case/issues) tab.
Issues labeled `good first issue` are well-scoped and documented.
Issues labeled `adapter` are new data source connectors.
Issues labeled `engine` are new pattern detection logic.

If you want to work on something not in the issues, open an issue first
to discuss it before writing code. This saves everyone time.

### 2. Set up your environment

```bash
git clone https://github.com/Swixixle/Open-Case.git
cd Open-Case
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Add CONGRESS_API_KEY from https://api.data.gov/signup/
uvicorn main:app --reload
```

Run the end-to-end test to confirm your environment works:

```bash
python -m scripts.test_todd_young
```

If that exits 0, you're good. If it fails, check the diagnostic output
and see the README troubleshooting section.

### 3. Make your changes

Keep the scope narrow. One adapter per PR. One engine change per PR.
A PR that touches adapters, engines, models, and routes simultaneously
is hard to review and hard to reason about.

### 4. Test it

For adapters: make sure empty results return `AdapterResponse(found=True, results=[])`
not an exception. Make sure errors return `AdapterResponse(found=False, error=str(e))`.

For engines: test with real data where possible. Use the investigation endpoint
against a known subject and confirm the signal makes sense.

For routes: the FastAPI `/docs` endpoint gives you an interactive test interface.

### 5. Submit a PR

PR title: plain English description of what changed.
PR description: what problem this solves, what data source it connects, or
what pattern it detects. Include an example of the signal or evidence entry
it produces if applicable.

---

## Building an Adapter

Every adapter follows the same interface. Copy `adapters/indiana_cf.py` as a
starting template for a source with no API (documented absence pattern), or
`adapters/fec.py` for a source with a JSON API.

The interface:

```python
class YourAdapter(BaseAdapter):
    source_name = "Your Source Name"

    async def search(self, query: str, query_type: str = "person") -> AdapterResponse:
        try:
            # Make your API call or scrape here
            # ...

            # Handle empty results — this is not a failure
            if not results:
                return self._make_empty_response(query)

            # Apply collision rule — if multiple entities matched the name,
            # mark all results as unverified
            collision_count = len(unique_name_set)

            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[
                    AdapterResult(
                        source_name=self.source_name,
                        source_url="direct link to primary source",
                        entry_type="financial_connection",  # see EvidenceEntry.entry_type
                        title="Short label for this evidence",
                        body="Plain English description of what this shows",
                        date_of_event="2023-01-15",  # ISO format
                        amount=45000.00,  # if financial
                        confidence="confirmed" if collision_count == 1 else "unverified",
                        matched_name="name as it appears in the source",
                        collision_count=collision_count,
                        raw_data=raw_api_response  # full response for audit
                    )
                    for result in results
                ],
                found=True,
                result_hash=hashlib.sha256(json.dumps(raw_data).encode()).hexdigest()
            )

        except Exception as e:
            # Always catch exceptions — never let adapter failure crash investigate()
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error=str(e)
            )
```

**Required entry types:**
- `financial_connection` — donations, contracts, payments
- `vote_record` — legislative votes
- `property_record` — ownership, permits, zoning
- `court_record` — filings, judgments, appearances
- `disclosure` — financial disclosures, lobbying filings
- `timeline_event` — a dated event that matters to the case
- `gap_documented` — you looked here and found nothing (auto-created by pipeline)

**The collision rule is not optional.** If your adapter does a name search
and more than one entity matches, every result must have `collision_count > 1`
and `confidence = "unverified"`. This prevents false positive links.
The investigator resolves the collision manually via `PATCH /evidence/{id}/disambiguate`.

After building your adapter:
1. Add it to `adapters/__init__.py`
2. Import and add it to the appropriate adapter list in `routes/investigate.py`
3. Register it with the cache helper so results are cached with TTL

---

## Building a Detection Engine

Detection engines analyze evidence entries and return signal candidates.
They should never reach conclusions — they surface patterns for human review.

```python
from dataclasses import dataclass
from typing import List

@dataclass
class YourSignal:
    description: str
    evidence_entry_id_a: str
    evidence_entry_id_b: str
    weight: float

    def to_description(self) -> str:
        return self.description

    def to_explanation(self) -> str:
        return "Plain English explanation of why this weight was assigned"

    def to_breakdown(self) -> dict:
        return {
            "component_a": 0.6,
            "component_b": 0.4,
            "final_weight": self.weight
        }


def detect_your_pattern(evidence_entries: list) -> List[YourSignal]:
    """
    Return a list of signals ranked by weight descending.
    This function must never raise — catch all exceptions internally.
    """
    signals = []

    for entry in evidence_entries:
        # Your detection logic here
        pass

    return sorted(signals, key=lambda s: s.weight, reverse=True)
```

Then add a corresponding function to `engines/signal_scorer.py` that converts
your signal type to the dict format that gets stored as a Signal row. Follow
the existing `build_signals_from_proximity` pattern exactly — the identity hash
is critical for deduplication.

---

## The Non-Negotiables

These are architectural decisions that are not up for debate in PRs.
If you disagree with any of them, open an issue to discuss.

**1. No unconfirmed signals in public receipts.**
`PATCH /expose` enforces `signal.confirmed == True`. Period.
A receipt that can contain unverified signals is not a receipt — it's a list of allegations.

**2. The collision rule.**
Any adapter result where multiple entities matched the same query must be flagged
as `unverified`. The investigator confirms the match. There are no exceptions.

**3. Adapters never raise exceptions.**
Every adapter catches its own exceptions and returns `found=False` with an error message.
The investigation pipeline runs many adapters; one failure should not fail the whole run.

**4. Documented absence is real evidence.**
When an adapter returns no results, the pipeline creates a `gap_documented` evidence entry
and a `SourceCheckLog` row. "We looked here and found nothing" is meaningful signal.
Never suppress empty results.

**5. The receipt format has no guilt field.**
If a PR adds a `likely_guilty`, `verdict`, or `conclusion` field to any model,
it will not be merged. Signal descriptions end with what the record shows.
They do not end with what that means.

**6. Private individuals are out of scope.**
No adapter should connect to data about private citizens who haven't chosen public roles.
Subject profiles are for public officials, corporations, and organizations.
The `subject_type` field exists for a reason.

---

## Issue Labels

- `adapter` — new data source connector
- `engine` — new pattern detection logic
- `coverage` — expanding geographic or jurisdictional coverage
- `bug` — something broken
- `good first issue` — well-scoped, good starting point
- `documentation` — README, API docs, jurisdiction guides
- `discussion` — design questions, architectural decisions
- `data-source` — a new data source has been identified (may not have code yet)

---

## A Word on Scope

Open Case is not trying to be everything. It is trying to be one thing well:
a platform where someone can build a documented, signed, verifiable case file
from public records and hand it off to someone else to continue.

The most common scope creep in civic tech projects is trying to do analysis
that belongs to journalists or prosecutors. We provide the evidence and the
structure. Others reach the conclusions.

If you're building something that tells users what to think — rather than
showing them what the record shows — it's probably out of scope for this project.

---

*Questions? Open an issue or start a discussion.*
