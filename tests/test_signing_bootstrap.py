"""bootstrap_env_keys repairs missing, mismatched, or malformed signing keys in .env."""

from __future__ import annotations

import os

from signing import bootstrap_env_keys, generate_keypair, regenerate_signing_keys_in_dotenv


# Truncated / bad padding — same class of error as user-reported binascii.Error
_BAD_B64_PRIVATE = (
    "MC4CAQAwBQYDK2VwBCIEIEiLLi1YC8qhZjr/FO9A6Fv23fEa3d4nwPJCIxRz2"  # length ≈61, invalid padding
)


def test_bootstrap_regenerates_malformed_private(tmp_path, monkeypatch):
    monkeypatch.delenv("OPEN_CASE_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("OPEN_CASE_PUBLIC_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text(
        f"OPEN_CASE_PRIVATE_KEY={_BAD_B64_PRIVATE}\nOPEN_CASE_PUBLIC_KEY=xx\nFOO=bar\n",
        encoding="utf-8",
    )
    bootstrap_env_keys(tmp_path)
    priv = os.environ["OPEN_CASE_PRIVATE_KEY"]
    pub = os.environ["OPEN_CASE_PUBLIC_KEY"]
    assert priv and pub
    assert priv != _BAD_B64_PRIVATE
    text = env.read_text(encoding="utf-8")
    assert "FOO=bar" in text
    assert _BAD_B64_PRIVATE not in text


def test_bootstrap_preserves_valid_pair(tmp_path, monkeypatch):
    monkeypatch.delenv("OPEN_CASE_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("OPEN_CASE_PUBLIC_KEY", raising=False)
    priv, pub = generate_keypair()
    env = tmp_path / ".env"
    env.write_text(f"OPEN_CASE_PRIVATE_KEY={priv}\nOPEN_CASE_PUBLIC_KEY={pub}\n", encoding="utf-8")
    bootstrap_env_keys(tmp_path)
    assert os.environ["OPEN_CASE_PRIVATE_KEY"] == priv
    assert os.environ["OPEN_CASE_PUBLIC_KEY"] == pub


def test_bootstrap_derives_missing_public(tmp_path, monkeypatch):
    monkeypatch.delenv("OPEN_CASE_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("OPEN_CASE_PUBLIC_KEY", raising=False)
    priv, pub = generate_keypair()
    env = tmp_path / ".env"
    env.write_text(f"OPEN_CASE_PRIVATE_KEY={priv}\n", encoding="utf-8")
    bootstrap_env_keys(tmp_path)
    assert os.environ["OPEN_CASE_PRIVATE_KEY"] == priv
    assert os.environ["OPEN_CASE_PUBLIC_KEY"] == pub


def test_regenerate_overwrites(tmp_path, monkeypatch):
    monkeypatch.delenv("OPEN_CASE_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("OPEN_CASE_PUBLIC_KEY", raising=False)
    priv, pub = generate_keypair()
    env = tmp_path / ".env"
    env.write_text(f"OPEN_CASE_PRIVATE_KEY={priv}\nOPEN_CASE_PUBLIC_KEY={pub}\n", encoding="utf-8")
    regenerate_signing_keys_in_dotenv(tmp_path)
    assert os.environ["OPEN_CASE_PRIVATE_KEY"] != priv
    assert os.environ["OPEN_CASE_PUBLIC_KEY"] != pub
