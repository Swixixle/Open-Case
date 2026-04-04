# NIKODEMUS SYSTEMS
## Voice, Standards, and Epistemological Commitments
## A governing document for all products

---

## THE CORE COMMITMENT

Every product built under Nikodemus Systems shares one commitment:

**Claims should be separable from verdicts.**

A claim is a documented fact. A verdict is a moral conclusion drawn from facts.
We build the claim layer. The reader draws the verdict.

This is not neutrality. The choice of what to measure is never neutral.
We choose to measure what institutions would prefer remain unmeasured.
But we do not tell the reader what to feel about what we find.
We tell them what the record says, where the record comes from,
and what the record does not say.

The absence of a record is itself a record.

---

## THE EPISTEMIC COMMITMENTS

### 1. Receipts, not verdicts

We document what happened. We do not characterize what it means.

**Correct:** "The company settled for $282M, admitting no wrongdoing, per DOJ press release."
**Incorrect:** "The company is corrupt."

**Correct:** "The executive received $90M in severance following substantiated harassment claims."
**Incorrect:** "The executive got away with it."

**Correct:** "Local retailers recirculate 65% of revenue locally, compared to 30% for chains."
**Incorrect:** "Chains are destroying communities."

The documented fact is more damning than the editorial verdict.
Let it speak.

### 2. Absence is measurable

When a politician does not respond to a documented record, that silence is signal.
When a company's investigation returns limited public data, that is a finding.
When an AI-generated clinical note cannot produce a signed receipt, that gap matters.
When a codebase has no audit trail for what the model contributed, that is information.

We do not hide absence. We measure it, label it, and surface it explicitly.

**In practice:**
- "No EPA enforcement actions found in public record for this review period."
- "Limited public data available — realtime research, verify sources."
- "DNCS: 0 — no documented response found in the public record."
- "Confidence: low — inference from scene context, not direct identification."

Absence labeled is more honest than absence hidden.
Absence hidden is itself a form of misinformation.

### 3. Confidence is always stated

Every claim carries an implicit confidence level.
We make it explicit.

Confidence levels across products:

| Label | Meaning |
|-------|---------|
| `high` / `confirmed` | Primary source, verified, directly documented |
| `medium` / `likely` | Well-supported inference, multiple secondary sources |
| `low` / `inferred` | Reasonable inference, limited direct documentation |
| `realtime_search` | Live research, not curated — verify independently |
| `model_knowledge` | AI training knowledge — may not reflect current state |

The reader deserves to know how confident we are.
Overclaiming confidence is a form of dishonesty.

### 4. Sources are primary or nothing

We cite government records, court filings, regulatory actions, and
established investigative journalism. We do not cite blogs, Wikipedia
as a primary source, company press releases for negative claims, or
social media.

If a claim cannot be sourced to a primary document, we do not make it.
If we cannot verify a claim's source, we say so explicitly.

**Acceptable primary sources:**
- Government agency databases (EPA ECHO, OSHA, FEC, DOJ, FTC, NLRB, SEC)
- Federal and state court records (CourtListener, PACER)
- Congressional records and legislative filings
- Peer-reviewed research and NBER working papers
- Established investigative outlets (ProPublica, NYT, Reuters, WSJ, The Guardian)
- Company SEC filings and official disclosures
- Inspector General reports

**Not acceptable as primary sources for negative claims:**
- Wikipedia (can point to primary sources, is not one itself)
- Company press releases or sustainability reports
- Social media posts
- Anonymous sourcing without corroboration
- AI-generated summaries without source chain

### 5. Specificity over generalization

"Large corporate violations" is not a finding.
"The company settled a $282M FCPA case in 2019, admitting bribery of officials
in Mexico, Brazil, India, and China" is a finding.

Dollar amounts, dates, case names, agency identifiers — these are
the difference between a claim and an allegation.

When specificity is unavailable, say so and explain why.

### 6. Category, not verdict

When describing systemic effects — community impact, market dynamics,
industry patterns — we speak in categories, not companies.

"Fast food chains" not "McDonald's" in the community impact section.
"Businesses of this type" not "this specific company" when describing
documented industry-wide patterns.

This discipline serves two purposes:
First, it keeps systemic analysis separate from individual company record.
Second, it makes the analysis transferable — the same community math
applies to every fast food chain, not just one.

---

## THE TONE

### What it sounds like

Precise. Spare. Non-prosecutorial. Occasionally dry.
The tone of a forensic accountant who has seen everything
and is no longer surprised but remains rigorous.

It does not perform outrage. It does not reach for adjectives.
It states what happened and trusts the facts.

**Reference voices:**
- A well-sourced ProPublica investigation
- A federal inspector general report that has learned to write clearly
- A good appellate brief
- The best long-form WSJ or Reuters accountability journalism

**Not:**
- An activist press release
- A Twitter thread performing outrage
- A corporate sustainability report performing virtue
- Academic writing performing complexity

### Specific language rules

**Never use:**
- "Corrupt" as a conclusion rather than a documented finding
- "Evil," "greedy," "immoral" — these are verdicts, not claims
- "As we all know" — condescending and epistemically lazy
- "Clearly" or "obviously" — if it's clear, the evidence will show it
- "Deeply troubling" — editorial, not analytical
- Passive voice to soften findings: "mistakes were made"

**Always use:**
- Specific dollar amounts when available
- Specific dates when available
- Specific case names, docket numbers, agency identifiers
- "Research suggests" / "studies show" / "documented pattern" when
  evidence is strong but not absolute
- "Allegedly" when charges are filed but not proven
- "Settled without admitting wrongdoing" when that is the documented outcome

**When confidence is uncertain:**
- "Typically" — pattern is documented across similar cases
- "Research suggests" — peer-reviewed or well-sourced evidence exists
- "The documented record shows" — direct citation available
- "No public record found for" — absence statement, honest

### What we never do

We never:
- Fabricate citations or invent sources
- State as fact what is allegation
- State as allegation what is documented fact
- Omit the confidence level of a claim
- Present a corporate sustainability commitment as equivalent to documented action
- Name a company in community impact sections (which describe category effects)
- Present speculation as inference, or inference as fact

---

## THE PRODUCTS AND THEIR APPLICATION

### EthicalAlt
**Claim layer:** Corporate investigation — what the company has documented done.
**Verdict layer:** Absent by design — concern level indicates severity, not judgment.
**Community impact:** Category-level economic analysis, not company-specific verdict.
**Confidence:** Shown on every identification (direct_logo / scene_inference / etc.)
**Absence handling:** "Limited public data — realtime research, verify sources."

### Frame / PUBLIC EYE
**Claim layer:** Public records, signed and timestamped.
**Verdict layer:** The journalist draws it.
**Confidence:** Cryptographic — the signature is the confidence.
**Absence handling:** The DNCS (Did Not Confirm Score) — absence is quantified.

### SPLIT
**Claim layer:** What the public figure said. What the documented record shows.
**Verdict layer:** The gap between claim and record speaks for itself.
**Confidence:** Source-linked. Undocumented claims are labeled as such.
**Absence handling:** No documented response = signal, shown explicitly.

### Debrief
**Claim layer:** What the codebase actually contains and does.
**Verdict layer:** Risk rating — descriptive, not prosecutorial.
**Confidence:** Shown per finding. "Possible," "likely," "confirmed."
**Absence handling:** Missing tests, missing documentation, missing receipts — all surfaced.

### RACK / CDIL
**Claim layer:** The AI said this, at this time, using this model.
**Verdict layer:** The clinician or user evaluates it.
**Confidence:** Signed. The signature is the chain of custody.
**Absence handling:** No signature = unverified claim. Always labeled.

---

## THE STRATEGIC PRINCIPLE

Every product in this portfolio answers a version of the same question:

**"How do you prove that something happened, or that it didn't?"**

The answer is always the same:

You build the receipt layer.

A receipt is not a verdict. It is a documented chain of custody
from event to record to reader. It says: this happened, here is the
evidence, here is the timestamp, here is the source, here is what
we do not know.

The institutions that benefit from unverifiable claims
— corporations, politicians, AI systems, medical systems —
are structurally opposed to the receipt layer.

That opposition is the market.

---

## GOVERNING RULES FOR CURSOR AND CLAUDE

When generating text for any Nikodemus Systems product, follow these rules:

1. **Never fabricate.** If a fact is not in the source material, do not invent it.
   Return null or "not found" before inventing.

2. **State the confidence.** Every generated claim should carry its confidence level
   explicitly — in the field, the label, or the UI.

3. **Source or mark as unsourced.** If a claim has a URL, include it.
   If it does not, mark it `model_knowledge` or `realtime_search`.

4. **Specific over general.** Dollar amounts, dates, case names always beat
   "significant" or "large" or "multiple."

5. **Category in community sections.** Never name the specific company
   in community impact text. Always speak in category terms.

6. **No editorial adjectives.** The facts are enough.
   Remove "shocking," "egregious," "troubling," "deeply."

7. **Absence is a finding.** "No public record found" is a valid and
   important output. It is not a failure. Return it explicitly.

8. **The reader draws the verdict.** We draw the map.
   We do not tell the reader what country they are in.

---

*Nikodemus Systems · Indianapolis · 2026*
*"The absence of a record is itself a record."*
