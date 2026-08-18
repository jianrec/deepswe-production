#!/usr/bin/env python3
"""Archive one non-finalized task attempt and return its slot to discovery."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def atomic_jsonl(path: Path, rows: list[dict]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--slot", required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()

    root = args.root.resolve()
    manifest = root / "registry/task_manifest.jsonl"
    rows = load_jsonl(manifest)
    row = next((item for item in rows if item.get("slot") == args.slot), None)
    if row is None:
        raise SystemExit(f"unknown slot: {args.slot}")
    if row.get("status") == "finalized":
        raise SystemExit(f"refusing to requeue finalized slot: {args.slot}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    package = root / "tasks" / args.slot
    archive = root / "logs" / "discarded-tasks" / f"{args.slot}-{timestamp}"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        raise SystemExit(f"archive already exists: {archive}")
    if package.exists():
        package.rename(archive)

    discarded = {
        "repository": row.get("repository"),
        "stage": row.get("stage"),
        "status": row.get("status"),
        "task_id": row.get("task_id"),
        "usage": row.get("usage", {}),
        "reason": args.reason,
        "archived_at": timestamp,
        "archive": str(archive.relative_to(root)) if archive.exists() else None,
    }
    row.setdefault("discarded_attempts", []).append(discarded)
    for key in (
        "repository",
        "base_commit_hash",
        "task_id",
        "qa",
        "reference_stats",
        "author_model",
        "test_model",
    ):
        row.pop(key, None)
    row.update(status="pending", stage="repository_discovery", artifacts=[], usage={}, errors=[])

    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "slot": args.slot,
        "stage": "task_preflight",
        "status": "discarded",
        "reason": args.reason,
        "repository": discarded["repository"],
        "api_key_stored": False,
    }
    events = root / "registry" / "production-events.jsonl"
    with events.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    atomic_jsonl(manifest, rows)
    print(json.dumps({"slot": args.slot, "status": "pending", "archive": discarded["archive"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
