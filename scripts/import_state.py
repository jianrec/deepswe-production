#!/usr/bin/env python3
"""Restore the runtime registry from a portable state snapshot."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


SECRET = re.compile(r"\b(?:sk|gh[opsu])[-_][A-Za-z0-9_-]{20,}\b")


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        raise SystemExit(f"snapshot file is missing: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate(rows: list[dict], label: str) -> None:
    slots = [str(row.get("slot")) for row in rows]
    if label == "manifest":
        if not rows or len(slots) != len(set(slots)) or any(not slot.startswith("task-") for slot in slots):
            raise SystemExit("manifest seed must contain unique task-NNNN slots")
    text = json.dumps(rows, ensure_ascii=False)
    if SECRET.search(text):
        raise SystemExit(f"{label} seed appears to contain a credential; refusing import")
    if any("/Users/" in text_part or "\\Users\\" in text_part for text_part in [text]):
        raise SystemExit(f"{label} seed contains a machine-specific absolute path; regenerate with export_state.py")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--force", action="store_true", help="replace an existing runtime registry")
    args = parser.parse_args()
    root = args.root.resolve()
    state_dir = (args.state_dir or root / "state").resolve()
    manifest_rows = load_jsonl(state_dir / "manifest.seed.jsonl")
    candidate_rows = load_jsonl(state_dir / "repository-candidates.seed.jsonl")
    validate(manifest_rows, "manifest")
    validate(candidate_rows, "candidates")
    registry = root / "registry"
    registry.mkdir(parents=True, exist_ok=True)
    manifest = registry / "task_manifest.jsonl"
    candidates = registry / "repository-candidates.jsonl"
    if not args.force and (manifest.exists() or candidates.exists()):
        raise SystemExit("runtime registry already exists; use --force only after verifying the snapshot")
    write_jsonl(manifest, manifest_rows)
    write_jsonl(candidates, candidate_rows)
    (registry / "production-events.jsonl").touch(exist_ok=True)
    print(json.dumps({"manifest": str(manifest), "manifest_rows": len(manifest_rows), "candidate_rows": len(candidate_rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
