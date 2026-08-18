#!/usr/bin/env python3
"""Run the DeepSWE production stages in order and resume safely.

Each loop advances independent slots through authoring, reference
implementation, hidden tests, Docker QA, and publication.  Stages are run
sequentially so workers cannot overwrite one another's manifest snapshots.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_stage(root: Path, command: list[str]) -> int:
    print("+", " ".join(command), flush=True)
    result = subprocess.run(command, cwd=root, check=False)
    if result.returncode:
        print(f"stage exited with code {result.returncode}; state is preserved", flush=True)
    return result.returncode


def stage_command(root: Path, script: str, env_file: Path, limit: int, workers: int, retry_failed: bool) -> list[str]:
    command = [sys.executable, str(root / "pipeline" / script), "--root", str(root), "--limit", str(limit), "--workers", str(workers), "--env-file", str(env_file)]
    if retry_failed:
        command.append("--retry-failed")
    return command


def publish_finalized(root: Path, cleanup: bool, limit: int) -> int:
    rows = load_jsonl(root / "registry/task_manifest.jsonl")
    published = {path.name for path in (root / "output").glob("task-*") if path.is_dir()}
    count = 0
    for row in rows:
        slot = str(row.get("slot"))
        if count >= limit or slot in published:
            continue
        if row.get("status") != "finalized" or row.get("stage") != "finalized":
            continue
        command = [sys.executable, str(root / "pipeline/publish_task.py"), "--root", str(root), "--slot", slot]
        if cleanup:
            command.extend(["--cleanup-docker", "--cleanup-workspaces", "--cleanup-logs", "--cleanup-staging"])
        if run_stage(root, command) == 0:
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--env-file", type=Path, help="provider env file outside the repository")
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--max-rounds", type=int, default=0, help="0 means continue until no work remains")
    parser.add_argument("--once", action="store_true", help="run one stage pass and return")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--discover", action="store_true", help="refresh GitHub candidates before production")
    parser.add_argument("--cleanup", action="store_true", help="remove per-task Docker/workspace/log staging after publication")
    parser.add_argument("--skip-doctor", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="check state and print the next stage without calling models or Docker")
    args = parser.parse_args()
    if args.batch_size < 1 or args.workers < 1:
        parser.error("--batch-size and --workers must be positive")
    root = args.root.resolve()
    env_file = (args.env_file or Path(os.environ.get("DEEPSWE_ENV_FILE", root.parent / "packy.env"))).expanduser().resolve()
    if not args.skip_doctor:
        doctor = [sys.executable, str(root / "scripts/doctor.py"), "--root", str(root), "--env-file", str(env_file)]
        if run_stage(root, doctor):
            raise SystemExit("doctor failed; fix the environment before production")
    manifest_path = root / "registry/task_manifest.jsonl"
    candidates_path = root / "registry/repository-candidates.jsonl"
    if not manifest_path.is_file() or not candidates_path.is_file():
        partial_registry = [path for path in (manifest_path, candidates_path) if path.is_file() and path.stat().st_size > 0]
        if partial_registry:
            raise SystemExit(
                "runtime registry is incomplete (one of manifest/candidates is missing); "
                "restore both from state explicitly instead of overwriting partial progress"
            )
        imported = run_stage(
            root,
            [sys.executable, str(root / "scripts/import_state.py"), "--root", str(root), "--force"],
        )
        if imported:
            raise SystemExit("portable state import failed; refusing to start production")
    rows = load_jsonl(root / "registry/task_manifest.jsonl")
    counts = Counter((row.get("status"), row.get("stage")) for row in rows)
    if args.dry_run:
        print(json.dumps({"dry_run": True, "counts": {f"{a}/{b}": n for (a, b), n in counts.items()}, "next": [
            "author_tasks.py", "reference_tasks.py", "qwen_tests.py", "finalize_task.py", "publish_task.py"
        ]}, ensure_ascii=False))
        return
    if args.discover:
        run_stage(root, [sys.executable, str(root / "pipeline/discover_repositories.py"), "--root", str(root), "--append"])
    rounds = 0
    while True:
        rounds += 1
        before = json.dumps(load_jsonl(root / "registry/task_manifest.jsonl"), sort_keys=True)
        run_stage(root, stage_command(root, "author_tasks.py", env_file, args.batch_size, args.workers, args.retry_failed))
        run_stage(root, stage_command(root, "reference_tasks.py", env_file, args.batch_size, args.workers, args.retry_failed))
        run_stage(root, stage_command(root, "qwen_tests.py", env_file, args.batch_size, args.workers, args.retry_failed))
        run_stage(root, [sys.executable, str(root / "pipeline/finalize_task.py"), "--root", str(root), "--limit", str(args.batch_size), "--repeats", "3", "--mutants", "4", *( ["--retry-failed"] if args.retry_failed else [])])
        publish_finalized(root, args.cleanup, args.batch_size)
        rows = load_jsonl(root / "registry/task_manifest.jsonl")
        counts = Counter((row.get("status"), row.get("stage")) for row in rows)
        print(json.dumps({"round": rounds, "counts": {f"{a}/{b}": n for (a, b), n in counts.items()}}, ensure_ascii=False), flush=True)
        after = json.dumps(rows, sort_keys=True)
        if args.once or (args.max_rounds and rounds >= args.max_rounds):
            break
        if before == after:
            print("no manifest progress; stopping to avoid a busy loop", flush=True)
            break


if __name__ == "__main__":
    main()
