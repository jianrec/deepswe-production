#!/usr/bin/env python3
"""Archive and requeue authored tasks whose repositories cannot supply P2P tests."""

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
    parser.add_argument("--minimum-tests", type=int, default=100)
    args = parser.parse_args()
    root = args.root.resolve()
    manifest = root / "registry/task_manifest.jsonl"
    candidates = load_jsonl(root / "registry/repository-candidates.jsonl")
    capacities = {row["full_name"]: row.get("test_capacity") for row in candidates}
    rows = load_jsonl(manifest)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_root = root / "logs/discarded-tasks"
    events = root / "registry/production-events.jsonl"
    requeued = []
    for row in rows:
        repository = row.get("repository")
        capacity = capacities.get(repository) if repository else None
        if row.get("status") == "finalized" or repository is None or capacity is None or int(capacity) >= args.minimum_tests:
            continue
        slot = row["slot"]
        package = root / "tasks" / slot
        archive = archive_root / f"{slot}-{timestamp}"
        archive_root.mkdir(parents=True, exist_ok=True)
        if package.exists():
            package.rename(archive)
        discarded = {
            "repository": repository,
            "test_capacity": capacity,
            "stage": row.get("stage"),
            "status": row.get("status"),
            "task_id": row.get("task_id"),
            "usage": row.get("usage", {}),
            "reason": f"repository exposes fewer than {args.minimum_tests} statically reportable tests",
            "archived_at": timestamp,
            "archive": str(archive.relative_to(root)) if archive.exists() else None,
        }
        row.setdefault("discarded_attempts", []).append(discarded)
        for key in ("repository", "base_commit_hash", "task_id", "qa"):
            row.pop(key, None)
        row.update({"status": "pending", "stage": "repository_discovery", "artifacts": [], "usage": {}, "errors": []})
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "slot": slot,
            "stage": "repository_preflight",
            "status": "discarded",
            "reason": discarded["reason"],
            "repository": repository,
            "test_capacity": capacity,
            "api_key_stored": False,
        }
        with events.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        requeued.append(slot)
    atomic_jsonl(manifest, rows)
    print(json.dumps({"requeued": len(requeued), "slots": requeued}, ensure_ascii=False))


if __name__ == "__main__":
    main()
