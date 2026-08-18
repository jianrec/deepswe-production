#!/usr/bin/env python3
"""Create a resumable 500-task manifest without calling any model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

LANGUAGES = ("typescript", "go", "python", "javascript", "rust")
STAGES = (
    "repository_discovery",
    "author_issue_pr_chain",
    "reference_implementation",
    "qwen_hidden_tests",
    "original_docker",
    "qa",
    "finalized",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--start-index", type=int, default=1)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    if args.count < 1:
        raise SystemExit("--count must be positive")
    if args.start_index < 1:
        raise SystemExit("--start-index must be positive")

    registry = args.root / "registry"
    registry.mkdir(parents=True, exist_ok=True)
    manifest = registry / "task_manifest.jsonl"
    existing = {}
    if manifest.exists():
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                existing[row["slot"]] = row

    rows = []
    for index in range(args.start_index, args.start_index + args.count):
        slot = f"task-{index:04d}"
        language = LANGUAGES[(index - 1) % len(LANGUAGES)]
        row = existing.get(slot, {
            "slot": slot,
            "language": language,
            "status": "pending",
            "stage": STAGES[0],
            "task_id": None,
            "repository": None,
            "base_commit_hash": None,
            "author_model": "claude-opus-4-8",
            "test_model": "gpt-5.6-sol",
            "rl_rollout_enabled": False,
            "artifacts": [],
            "usage": {},
            "qa": {},
            "errors": [],
        })
        rows.append(row)

    manifest.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    (registry / "repository-candidates.jsonl").touch()
    (registry / "production-events.jsonl").touch()
    print(json.dumps({"count": len(rows), "manifest": str(manifest)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
