#!/usr/bin/env python3
"""
Emit hash-verified repository evidence for documentation / Debrief DCI.

Writes:
  - docs/DEBRIEF_STRUCTURE_EVIDENCE.json — hashes + counts
  - docs/DEBRIEF_CLAIMS_FOR_DCI.md — short verbatim claims for tools (e.g. Debrief) that should not infer from README

Run from repository root:
  python3 scripts/generate_debrief_evidence.py
  python3 scripts/generate_debrief_evidence.py --check   # fail if outputs would change
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = REPO_ROOT / "docs" / "DEBRIEF_STRUCTURE_EVIDENCE.json"
OUTPUT_MD = REPO_ROOT / "docs" / "DEBRIEF_CLAIMS_FOR_DCI.md"

# Paths we never count (match a path segment).
EXCLUDE_DIR_PARTS = frozenset(
    {
        ".git",
        "node_modules",
        "venv",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        "dist",
    }
)


def _path_excluded(rel: Path) -> bool:
    return any(part in EXCLUDE_DIR_PARTS for part in rel.parts)


def _sha256_file(path: Path) -> str:
    """
    Hash file bytes with CRLF normalized to LF so CI (Linux) and local checkouts
    agree regardless of git autocrlf / editor line endings.
    """
    raw = path.read_bytes()
    if b"\r\n" in raw:
        raw = raw.replace(b"\r\n", b"\n")
    return hashlib.sha256(raw).hexdigest()


def _sha256_utf8_lines(lines: list[str]) -> str:
    body = "\n".join(lines).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _collect_python_files() -> list[str]:
    out: list[str] = []
    for p in REPO_ROOT.rglob("*.py"):
        try:
            rel = p.relative_to(REPO_ROOT)
        except ValueError:
            continue
        if _path_excluded(rel):
            continue
        out.append(rel.as_posix())
    return sorted(out)


def _collect_client_js_files() -> list[str]:
    client = REPO_ROOT / "client"
    if not client.is_dir():
        return []
    out: list[str] = []
    for ext in (".js", ".jsx", ".mjs", ".cjs"):
        for p in client.rglob(f"*{ext}"):
            try:
                rel = p.relative_to(REPO_ROOT)
            except ValueError:
                continue
            if _path_excluded(rel):
                continue
            out.append(rel.as_posix())
    return sorted(set(out))


def _tree_hash(relative_paths: list[str]) -> str:
    return _sha256_utf8_lines(relative_paths)


def _marker_paths() -> list[Path]:
    return [
        REPO_ROOT / "client" / "package.json",
        REPO_ROOT / "server" / "__init__.py",
        REPO_ROOT / "main.py",
        REPO_ROOT / "client" / "vite.config.js",
        REPO_ROOT / "client" / "src" / "main.jsx",
    ]


def _without_timestamp(doc: dict) -> dict:
    """Stable compare for --check (timestamp changes every run)."""
    out = json.loads(json.dumps(doc))
    out.pop("generated_at_utc", None)
    return out


def _build_dci_markdown(doc: dict) -> str:
    """Minimal doc for DCI tools: same statements as JSON, no extra prose to mis-extract."""
    claims = doc["claims"]
    s1 = claims["claim_001"]["statement"]
    s2 = claims["claim_002"]["statement"]
    return (
        "# Open Case — DCI claims (auto-generated)\n\n"
        "Do not edit by hand. Regenerate with "
        "`python3 scripts/generate_debrief_evidence.py`.\n\n"
        "Hash-verified evidence: `docs/DEBRIEF_STRUCTURE_EVIDENCE.json`\n\n"
        "## claim_001 — Primary languages\n\n"
        f"{s1}\n\n"
        "## claim_002 — client/ and server/\n\n"
        f"{s2}\n"
    )


def build_document() -> dict:
    py_files = _collect_python_files()
    js_files = _collect_client_js_files()

    client_tree_paths: list[str] = []
    server_tree_paths: list[str] = []
    client_root = REPO_ROOT / "client"
    server_root = REPO_ROOT / "server"
    if client_root.is_dir():
        for p in client_root.rglob("*"):
            if p.is_file():
                rel = p.relative_to(REPO_ROOT)
                if _path_excluded(rel):
                    continue
                client_tree_paths.append(rel.as_posix())
    if server_root.is_dir():
        for p in server_root.rglob("*"):
            if p.is_file():
                rel = p.relative_to(REPO_ROOT)
                if _path_excluded(rel):
                    continue
                server_tree_paths.append(rel.as_posix())
    client_tree_paths.sort()
    server_tree_paths.sort()

    markers_by_path: dict[str, str] = {}
    file_evidence: list[dict] = []
    for rel in _marker_paths():
        if rel.is_file():
            r = rel.relative_to(REPO_ROOT)
            h = _sha256_file(rel)
            key = r.as_posix()
            markers_by_path[key] = h
            file_evidence.append(
                {
                    "type": "marker_file",
                    "path": key,
                    "sha256": h,
                    "snippet_hash_verified": True,
                }
            )

    now = datetime.now(timezone.utc).isoformat()

    pkg = markers_by_path.get("client/package.json")
    srv = markers_by_path.get("server/__init__.py")
    if pkg is None or srv is None:
        raise RuntimeError("Missing client/package.json or server/__init__.py — cannot emit structure evidence.")

    return {
        "schema_version": 1,
        "generated_at_utc": now,
        "generator": "scripts/generate_debrief_evidence.py",
        "count_method": {
            "python_glob": "**/*.py under repository root",
            "javascript_glob": "client/**/*.{js,jsx,mjs,cjs} under repository root",
            "excluded_path_segments": sorted(EXCLUDE_DIR_PARTS),
        },
        "claims": {
            "claim_001": {
                "statement": (
                    f"Primary languages: JavaScript ({len(js_files)} source files under client/), "
                    f"Python ({len(py_files)} .py files in the repository tree, same exclusions)."
                ),
                "status": "evidenced",
                "counts": {
                    "javascript_client_source_files": len(js_files),
                    "python_repository_files": len(py_files),
                },
                "evidence": [
                    {
                        "type": "file_list_sha256",
                        "label": "sorted_relative_paths_all_python_files",
                        "sha256": _tree_hash(py_files),
                        "path_count": len(py_files),
                    },
                    {
                        "type": "file_list_sha256",
                        "label": "sorted_relative_paths_client_javascript_files",
                        "sha256": _tree_hash(js_files),
                        "path_count": len(js_files),
                    },
                    *[
                        e
                        for e in file_evidence
                        if e["path"]
                        in (
                            "main.py",
                            "client/package.json",
                            "client/src/main.jsx",
                        )
                    ],
                ],
            },
            "claim_002": {
                "statement": (
                    "Project has both client/ and server/ directories (full-stack structure: "
                    "React/Vite UI plus Python service layout)."
                ),
                "status": "evidenced",
                "evidence": [
                    {
                        "type": "directory_marker_file",
                        "path": "client/package.json",
                        "sha256": pkg,
                        "snippet_hash_verified": True,
                    },
                    {
                        "type": "directory_marker_file",
                        "path": "server/__init__.py",
                        "sha256": srv,
                        "snippet_hash_verified": True,
                    },
                    {
                        "type": "file_tree_sha256",
                        "root": "client/",
                        "file_count": len(client_tree_paths),
                        "sha256": _tree_hash(client_tree_paths),
                    },
                    {
                        "type": "file_tree_sha256",
                        "root": "server/",
                        "file_count": len(server_tree_paths),
                        "sha256": _tree_hash(server_tree_paths),
                    },
                ],
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if evidence JSON or DCI markdown is out of date.",
    )
    args = parser.parse_args()
    doc = build_document()
    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    md_text = _build_dci_markdown(doc)

    if args.check:
        if not OUTPUT.is_file():
            print(f"VERIFY FAIL: missing {OUTPUT}", file=sys.stderr)
            return 1
        on_disk = json.loads(OUTPUT.read_text(encoding="utf-8"))
        if _without_timestamp(on_disk) != _without_timestamp(doc):
            print(
                f"VERIFY FAIL: {OUTPUT} is stale. Run: python3 scripts/generate_debrief_evidence.py",
                file=sys.stderr,
            )
            return 1
        print(f"OK: {OUTPUT} matches regenerated content (excluding timestamp).")
        if not OUTPUT_MD.is_file():
            print(f"VERIFY FAIL: missing {OUTPUT_MD}", file=sys.stderr)
            return 1
        if OUTPUT_MD.read_text(encoding="utf-8") != md_text:
            print(
                f"VERIFY FAIL: {OUTPUT_MD} is stale. Run: python3 scripts/generate_debrief_evidence.py",
                file=sys.stderr,
            )
            return 1
        print(f"OK: {OUTPUT_MD} matches regenerated content.")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(text, encoding="utf-8")
    OUTPUT_MD.write_text(md_text, encoding="utf-8")
    print(f"Wrote {OUTPUT}")
    print(f"Wrote {OUTPUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
