"""Issue #2: bootstrap_env_keys must FAIL CLOSED in production.

Today bootstrap_env_keys auto-generates a keypair when OPEN_CASE_PRIVATE_KEY is
missing/malformed — silently rotating the trust anchor and letting a dev key
become a prod key. In production it must instead raise and refuse to start.

Production is detected from a single source of truth: ENV=production (the same
convention main.py's BASE_URL check uses). Development otherwise.

These tests prove the guard FIRES (raises / aborts a process), not merely that
the guard code exists. Non-prod auto-generation is preserved.

Note: tests/conftest.py forces ENV=development globally so the suite never runs
in prod mode; the prod cases below opt in per-test via monkeypatch.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from signing import _is_production, bootstrap_env_keys, generate_keypair

REPO_ROOT = Path(__file__).resolve().parent.parent

# Truncated / bad padding — same class as a real copy-paste error.
_BAD_B64 = "MC4CAQAwBQYDK2VwBCIEIEiLLi1YC8qhZjr/FO9A6Fv23fEa3d4nwPJCIxRz2"


def _write_env(path: Path, priv=None, pub=None) -> None:
    lines = []
    if priv is not None:
        lines.append(f"OPEN_CASE_PRIVATE_KEY={priv}")
    if pub is not None:
        lines.append(f"OPEN_CASE_PUBLIC_KEY={pub}")
    (path / ".env").write_text(("\n".join(lines) + "\n") if lines else "")


def _clear_keys(monkeypatch) -> None:
    monkeypatch.delenv("OPEN_CASE_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("OPEN_CASE_PUBLIC_KEY", raising=False)


# --- prod detection: ENV only -------------------------------------------------

def test_is_production_only_when_ENV_production(monkeypatch):
    monkeypatch.delenv("ENV", raising=False)
    assert _is_production() is False          # unset -> development
    monkeypatch.setenv("ENV", "")
    assert _is_production() is False          # empty -> development
    monkeypatch.setenv("ENV", "development")
    assert _is_production() is False
    monkeypatch.setenv("ENV", "staging")
    assert _is_production() is False
    monkeypatch.setenv("ENV", "production")
    assert _is_production() is True
    monkeypatch.setenv("ENV", "Production")   # case-insensitive
    assert _is_production() is True


# --- the guard FIRES in production -------------------------------------------

def test_prod_missing_key_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("ENV", "production")
    _clear_keys(monkeypatch)
    _write_env(tmp_path)  # empty .env, no key
    with pytest.raises(RuntimeError, match="Refusing to start"):
        bootstrap_env_keys(tmp_path)


def test_prod_malformed_key_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("ENV", "production")
    _clear_keys(monkeypatch)
    _write_env(tmp_path, priv=_BAD_B64, pub="xx")
    with pytest.raises(RuntimeError, match="Refusing to start"):
        bootstrap_env_keys(tmp_path)


def test_prod_does_not_generate_a_key_file(tmp_path, monkeypatch):
    """Belt-and-suspenders: the .env must be left untouched (no new key written)."""
    monkeypatch.setenv("ENV", "production")
    _clear_keys(monkeypatch)
    _write_env(tmp_path)  # empty
    before = (tmp_path / ".env").read_text()
    with pytest.raises(RuntimeError):
        bootstrap_env_keys(tmp_path)
    assert (tmp_path / ".env").read_text() == before
    assert "OPEN_CASE_PRIVATE_KEY" not in os.environ


# --- prod WITH a valid key starts; dev/test still auto-generate ---------------

def test_prod_with_valid_key_starts(tmp_path, monkeypatch):
    priv, pub = generate_keypair()
    monkeypatch.setenv("ENV", "production")
    _clear_keys(monkeypatch)
    _write_env(tmp_path, priv=priv, pub=pub)
    bootstrap_env_keys(tmp_path)  # must NOT raise
    assert os.environ["OPEN_CASE_PRIVATE_KEY"] == priv


def test_dev_missing_key_auto_generates(tmp_path, monkeypatch):
    monkeypatch.delenv("ENV", raising=False)  # unset -> development default
    _clear_keys(monkeypatch)
    _write_env(tmp_path)
    bootstrap_env_keys(tmp_path)  # must NOT raise
    assert os.environ.get("OPEN_CASE_PRIVATE_KEY")


# --- end-to-end: the guard aborts a real process at "startup" -----------------

def test_import_time_guard_aborts_process_under_prod(tmp_path):
    """Run bootstrap_env_keys (what main.py calls at import) in a fresh process
    under ENV=production with no key available → the process must exit nonzero
    with the refusal message. Proves the guard aborts startup, not just unit scope."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("OPEN_CASE_PRIVATE_KEY", "OPEN_CASE_PUBLIC_KEY")
    }
    env["ENV"] = "production"
    script = (
        "import pathlib, signing; "
        f"signing.bootstrap_env_keys(pathlib.Path(r'{tmp_path}'))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "Refusing to start" in (proc.stdout + proc.stderr)
