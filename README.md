# Open Case

**Public records. Signed findings. No verdicts.**

[open-case.onrender.com](https://open-case.onrender.com)

---

Open Case is an investigation pipeline for public officials. It links campaign finance records, Senate votes, lobbying filings, and regulatory data — then surfaces patterns a human would take weeks to find manually.

It does not assert conclusions. It produces receipts.

---

## What it finds

**Pattern engine** — five rules running across all cases:

- **SOFT_BUNDLE** — three or more donors to the same committee within seven days, scored against vote proximity and sector alignment
- **SECTOR_CONVERGENCE** — industry money clustering around relevant votes
- **GEO_MISMATCH** — out-of-state donor floods around specific legislative windows
- **REVOLVING_DOOR** — donor entities tied to active LDA lobbying registrants
- **BASELINE_ANOMALY** — donation spikes statistically extreme against a senator's own historical baseline, gated by temporal proximity to votes

**Senator dossiers** — 20 senators fully researched via a six-category Perplexity pipeline:

- Ethics complaints and investigations
- Financial disclosures and conflict of interest
- Donor vs vote record
- Public statements vs voting record
- Revolving door — staff who left for K Street
- Recent news and scrutiny

**Gap analysis** — plain English sentences derived from FEC data and vote records. "Received $X from Y industry on [date]. [N] days later voted [result] on [bill]."

**Stock trade proximity** — Senate financial disclosure trades cross-referenced against committee hearing schedules. Flags trades within 30 days of a relevant hearing in the same sector.

**Amendment fingerprint** — how often a senator votes in alignment with their top donor sectors on amendments, not just final passage votes.

**Staff network** — senior staff cross-referenced against LDA lobbying disclosures. Flags when a former staffer lobbies for a company that is also a donor.

---

## Everything is signed

Every dossier receipt is Ed25519 signed. Verifiable at `/verify/:dossier_id`. Downloadable as JSON or PDF. Chain of custody from public record to signed artifact.

---

## What this is not

Open Case does not prove intent. It does not assert coordination. It does not accuse anyone of anything.

Every alert includes: "This alert documents donor appearance across public records. It does not assert coordination, intent, or quid pro quo."

Pattern scores are documented signals, not verdicts. The reader draws the conclusion.

---

## Data sources

FEC Schedule A/B · Senate LIS XML roll call votes · Senate LDA lobbying filings · Congress.gov member data · Senate financial disclosures · Regulations.gov · GovInfo congressional hearings · USASpending federal awards · Indiana Campaign Finance

---

## Stack

FastAPI · SQLAlchemy · PostgreSQL · Alembic · Ed25519 signing · Perplexity sonar-deep-research · React/Vite frontend · Render

Built by Alex Maksimovich / [Nikodemus Systems](https://swixixle.github.io)
