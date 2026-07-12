# Constraints — Open Case

Invariants that must not be broken without a deliberate, documented migration plan. Derived from the project canon (`canon.md`, sections "What Open Case is NOT" and "Forbidden changes"). If a change would violate one of these, stop and flag it rather than proceeding.

## Cryptographic integrity
- Never remove or bypass **Ed25519 signing** for load-bearing receipts. Signing uses Ed25519 + **JCS canonicalization** + **SHA-256** digest (`signing.py`). Frozen payloads must stay third-party verifiable.
- Never weaken or skip receipt verification paths to make something "just work."

## Epistemic rigor (the core of the product)
- Never turn the engine into an **AI verdict layer**. No automated "guilty" output, no AI-generated accusations. Rules surface structure, timing, and proximity — they are not model opinions.
- Every finding's label (`VERIFIED`, `REPORTED`, `ALLEGED`, `DISPUTED`, `CONTEXTUAL`) comes from **source type**, not model speculation (`services/epistemic_classifier.py`). Never assign labels from narrative confidence.
- Never weaken labels, skip provenance, or hide uncertainty to "simplify." Distinguish **implemented** vs **planned** adapter coverage honestly.
- Never imply court-proven corruption without actual court / primary-record support and correct labels. The engine documents **proximity and timing**; causation and criminality are out of scope.

## Publication safety
- Never auto-publish findings to the public as if final. Human journalist / editorial review is assumed for anything that reads like a publishable allegation. Principle: *"Receipts, not verdicts."*

## Pattern engine
- The active rule set is **`PATTERN_RULE_IDS` in `engines/pattern_engine.py`** (v2.7, 18 rules) — that file is authoritative. Don't treat spec'd-but-unimplemented rules as live.

## Operational
- **Never commit secrets.** Keys and `DATABASE_URL` live in environment / host config; start from `.env.example`.
- Hosting + API budget ceiling is roughly **~$200/month** — a design constraint, not a guarantee. New external-API dependencies should respect it.
