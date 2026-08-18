#!/usr/bin/env python3
"""Archive the V1 production state and create a clean V2 manifest.

The operation is intentionally non-destructive: every V1 task package, log,
manifest, and production event is moved under ``archives/``. Repository caches
and the audited repository candidate registry are retained for V2.
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


LANGUAGES = ("typescript", "go", "python", "javascript", "rust")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--archive-id")
    args = parser.parse_args()
    root = args.root.resolve()
    manifest = root / "registry/task_manifest.jsonl"
    events = root / "registry/production-events.jsonl"
    rows = load_jsonl(manifest)
    if len(rows) != 500:
        raise SystemExit(f"expected 500 V1 manifest rows, found {len(rows)}")
    task1 = next((row for row in rows if row.get("slot") == "task-0001"), None)
    if not task1 or task1.get("status") != "finalized" or task1.get("stage") != "finalized":
        raise SystemExit("task-0001 is not finalized; refusing V2 migration")

    archive_id = args.archive_id or datetime.now(timezone.utc).strftime("v1-%Y%m%dT%H%M%SZ")
    archive = root / "archives" / archive_id
    if archive.exists():
        raise SystemExit(f"archive already exists: {archive}")
    (archive / "tasks").mkdir(parents=True)
    (archive / "registry").mkdir(parents=True)

    task_dirs = sorted((root / "tasks").glob("task-*"))
    migrated = []
    for package in task_dirs:
        if package.name == "task-0001":
            continue
        target = archive / "tasks" / package.name
        package.rename(target)
        migrated.append(package.name)

    if (root / "logs").exists():
        (root / "logs").rename(archive / "logs")
    (root / "logs").mkdir()
    manifest.rename(archive / "registry/task_manifest.jsonl")
    if events.exists():
        events.rename(archive / "registry/production-events.jsonl")

    for name in ("HANDOFF.md", "制作方案.md"):
        source = root / name
        if source.is_file():
            shutil.copy2(source, archive / name)

    migrated_task1 = dict(task1)
    migrated_task1.update(
        pipeline_version="2.0",
        origin_pipeline_version="1.0",
        migrated_at=now(),
    )
    fresh_rows = [migrated_task1]
    for index in range(2, 501):
        fresh_rows.append(
            {
                "slot": f"task-{index:04d}",
                "language": LANGUAGES[(index - 1) % len(LANGUAGES)],
                "status": "pending",
                "stage": "repository_discovery",
                "pipeline_version": "2.0",
                "rl_rollout_enabled": False,
                "artifacts": [],
                "usage": {},
                "errors": [],
            }
        )
    write_jsonl(manifest, fresh_rows)

    event = {
        "timestamp": now(),
        "stage": "pipeline_v2_migration",
        "status": "success",
        "pipeline_version": "2.0",
        "archive": str(archive.relative_to(root)),
        "migrated_finalized_slots": ["task-0001"],
        "reset_slots": 499,
        "api_key_stored": False,
    }
    events.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")

    metadata = {
        "archive_id": archive_id,
        "archived_at": now(),
        "source_manifest_rows": len(rows),
        "source_status_counts": dict(Counter(row.get("status") for row in rows)),
        "source_stage_counts": dict(Counter(row.get("stage") for row in rows)),
        "archived_task_directories": migrated,
        "preserved_in_v2": [
            "tasks/task-0001",
            "registry/repository-candidates.jsonl",
            "workspaces/repositories",
        ],
    }
    (archive / "archive-metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"archive": str(archive), "archived_tasks": len(migrated), "v2_rows": len(fresh_rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
