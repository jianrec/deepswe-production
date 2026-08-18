#!/usr/bin/env python3
"""Atomically record a successful offline repository preflight."""

from __future__ import annotations

import argparse
import fcntl
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    parser.add_argument("--repository", required=True)
    parser.add_argument("--base-commit", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument("--test-count", type=int, required=True)
    parser.add_argument("--environment", default="Linux container, runtime network disabled")
    parser.add_argument("--excluded-test-id", action="append", default=[])
    args = parser.parse_args()
    root = args.root.resolve()
    if args.test_count < 100:
        raise SystemExit("preflight evidence must cover at least 100 passing tests")
    repo = root / "workspaces/repositories" / args.repository.replace("/", "__")
    resolved = subprocess.run(
        ["git", "rev-parse", args.base_commit], cwd=repo, capture_output=True, text=True
    )
    if resolved.returncode or resolved.stdout.strip() != args.base_commit:
        raise SystemExit("base commit is not present in the cached repository")
    image = subprocess.run(
        ["docker", "image", "inspect", args.image, "--format", "{{.Id}}"],
        capture_output=True,
        text=True,
    )
    if image.returncode or not image.stdout.strip().startswith("sha256:"):
        raise SystemExit(f"preflight image is unavailable: {args.image}")

    registry = root / "registry/repository-candidates.jsonl"
    lock_path = root / "registry/.repository-candidates.lock"
    lock_path.touch(exist_ok=True)
    with lock_path.open("r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        rows = load_jsonl(registry)
        candidate = next((row for row in rows if row.get("full_name") == args.repository), None)
        if candidate is None:
            raise SystemExit(f"repository is absent from candidate registry: {args.repository}")
        candidate["runtime_preflight"] = {
            "status": "passed",
            "base_commit_hash": args.base_commit,
            "command": args.command,
            "test_count": args.test_count,
            "environment": args.environment,
            "network_mode": "no-network",
            "image": args.image,
            "image_id": image.stdout.strip(),
            "excluded_test_ids": args.excluded_test_id,
            "passed_at": now(),
        }
        atomic_jsonl(registry, rows)
        fcntl.flock(lock, fcntl.LOCK_UN)
    print(json.dumps({"repository": args.repository, "status": "passed", "test_count": args.test_count}, ensure_ascii=False))


if __name__ == "__main__":
    main()
