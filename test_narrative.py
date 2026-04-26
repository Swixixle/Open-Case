#!/usr/bin/env python3
"""Test the narrative synthesis directly."""

import sys
sys.path.insert(0, '.')

import uuid
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

# Setup DB
engine = create_engine("sqlite:///./open_case.db")
SessionLocal = sessionmaker(bind=engine)

from models import CaseFile, CaseNarrative, EvidenceEntry, Signal
from routes.narrative import (
    _build_narrative_prompt,
    _generate_id,
    _hash_prompt,
    synthesize_narrative
)

db = SessionLocal()

# Get Todd Young case
case_id = uuid.UUID("ef5493ed-0a5c-4b1d-8401-a81519ae1084")
case_file = db.execute(select(CaseFile).where(CaseFile.id == case_id)).scalar_one_or_none()

if not case_file:
    print(f"Case {case_id} not found")
    sys.exit(1)

print(f"Found case: {case_file.subject_name}")
print(f"Subject type: {case_file.subject_type}")
print(f"Jurisdiction: {case_file.jurisdiction}")

# Get evidence, signals
evidence = db.execute(
    select(EvidenceEntry)
    .where(EvidenceEntry.case_file_id == case_id)
).scalars().all()

signals = db.execute(
    select(Signal)
    .where(Signal.case_file_id == case_id)
).scalars().all()

print(f"\nEvidence count: {len(evidence)}")
print(f"Signals count: {len(signals)}")

# Build prompt
from engines.pattern_engine import pattern_alerts_for_case, run_pattern_engine
pal = run_pattern_engine(db)
pattern_rows = pattern_alerts_for_case(case_id, pal, include_unreviewed=True)
print(f"Pattern alerts: {len(pattern_rows)}")

prompt = _build_narrative_prompt(case_file, evidence, signals, pattern_rows)
prompt_hash = _hash_prompt(prompt)

print(f"\nPrompt length: {len(prompt)} characters")
print(f"Prompt hash: {prompt_hash[:16]}...")

print("\n" + "="*70)
print("PROMPT PREVIEW (first 1000 chars):")
print("="*70)
print(prompt[:1000])
print("...")

print("\n" + "="*70)
print("TESTING AI CALLS:")
print("="*70)

# Try Claude
narrative_text = None
model_used = None

try:
    import anthropic
    client = anthropic.Anthropic()
    print("Attempting Claude call...")
    response = client.messages.create(
        model="claude-3-sonnet-20240229",
        max_tokens=2000,
        temperature=0.3,
        messages=[{"role": "user", "content": prompt}],
    )
    narrative_text = response.content[0].text
    model_used = "claude-sonnet-4-20250514"
    print(f"✓ Claude succeeded ({len(narrative_text)} chars)")
except Exception as e:
    print(f"✗ Claude failed: {e}")

# Try Perplexity if Claude failed
if not narrative_text:
    try:
        import requests
        import os
        # Load from .env if available
        try:
            from dotenv import load_dotenv
            load_dotenv('.env')
        except:
            pass
        api_key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
        print(f"Perplexity key: {api_key[:20]}...")
        if api_key:
            print(f"Attempting Perplexity call with key {api_key[:10]}...")
            response = requests.post(
                "https://api.perplexity.ai/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "sonar",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 2000,
                    "temperature": 0.3,
                },
                timeout=30,
            )
            if response.status_code == 200:
                data = response.json()
                narrative_text = data["choices"][0]["message"]["content"]
                model_used = "perplexity-sonar"
                print(f"✓ Perplexity succeeded ({len(narrative_text)} chars)")
            else:
                print(f"✗ Perplexity failed: {response.status_code}")
        else:
            print("✗ No Perplexity API key")
    except Exception as e:
        print(f"✗ Perplexity failed: {e}")

if narrative_text:
    print("\n" + "="*70)
    print(f"NARRATIVE ({model_used}):")
    print("="*70)
    print(narrative_text)
else:
    print("\n✗ All AI models failed")

db.close()
