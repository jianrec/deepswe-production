#!/usr/bin/env python3
"""Publish one fully finalized staging task into output and clean exact resources."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def load_manifest(path: Path, slot: str) -> dict:
    if not path.is_file():
        raise SystemExit(f"manifest is missing: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("slot") == slot:
                return row
    raise SystemExit(f"manifest slot is missing: {slot}")


def verify_finalized(row: dict) -> None:
    if row.get("status") != "finalized" or row.get("stage") != "finalized":
        raise SystemExit(f"{row.get('slot')} is not finalized/finalized")
    qa = row.get("qa") or {}
    docker = qa.get("docker") or {}
    if qa.get("status") != "passed" or not docker.get("passed"):
        raise SystemExit(f"{row.get('slot')} does not contain passing Docker QA")
    if not (docker.get("nop_ok") and docker.get("oracle_ok") and docker.get("mutant_ok")):
        raise SystemExit(f"{row.get('slot')} failed a required QA gate")


def validate(root: Path, task: Path) -> None:
    result = run(["python3", str(root / "pipeline/validate_task.py"), str(task)], check=False)
    if result.returncode:
        raise SystemExit(result.stdout.strip() or result.stderr.strip() or "task validation failed")


def remove_task_containers(image: str) -> list[str]:
    result = run(["docker", "ps", "-aq", "--filter", f"ancestor={image}"], check=False)
    ids = [item for item in result.stdout.splitlines() if item.strip()]
    if ids:
        run(["docker", "rm", "-f", *ids], check=False)
    return ids


def cleanup_docker(slot: str, commit: str) -> dict:
    if run(["docker", "info"], check=False).returncode:
        return {"available": False, "containers": [], "images": []}
    tag = commit[:12]
    images = [f"deepswe-{slot}-tests:{tag}", f"deepswe-{slot}-environment:{tag}"]
    removed_containers: list[str] = []
    removed_images: list[str] = []
    for image in images:
        removed_containers.extend(remove_task_containers(image))
        inspected = run(["docker", "image", "inspect", image], check=False)
        if inspected.returncode == 0:
            result = run(["docker", "image", "rm", image], check=False)
            if result.returncode == 0:
                removed_images.append(image)
    return {"available": True, "containers": removed_containers, "images": removed_images}


def cleanup_workspaces(root: Path, row: dict) -> list[str]:
    slot = row["slot"]
    repository = str(row.get("repository") or "").replace("/", "__")
    repo = root / "workspaces/repositories" / repository if repository else None
    targets = [
        root / "workspaces/docker-context" / slot,
        root / "workspaces/qa" / slot,
        root / "workspaces/verifier" / slot,
        root / "workspaces/mutants" / slot,
        root / "workspaces/reference" / slot,
        root / "workspaces/author-responses" / slot,
        root / "workspaces/strong-artifacts" / slot,
    ]
    removed: list[str] = []
    for target in targets:
        if not target.exists():
            continue
        if repo and (repo / ".git").exists():
            run(["git", "worktree", "remove", "--force", str(target)], cwd=repo, check=False)
        if target.exists():
            shutil.rmtree(target)
        removed.append(str(target.relative_to(root)))
    return removed


def cleanup_logs(root: Path, row: dict) -> list[str]:
    """Remove verbose transient logs for one published slot only."""
    slot = row["slot"]
    targets = [
        root / "logs/qa" / slot,
        root / "logs/model-responses" / slot,
        root / "logs/author-responses" / slot,
    ]
    removed: list[str] = []
    for target in targets:
        if target.exists():
            shutil.rmtree(target)
            removed.append(str(target.relative_to(root)))
    return removed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--slot", required=True)
    parser.add_argument("--cleanup-docker", action="store_true")
    parser.add_argument("--cleanup-workspaces", action="store_true")
    parser.add_argument(
        "--cleanup-logs",
        action="store_true",
        help="remove verbose transient QA/model logs for this published slot",
    )
    parser.add_argument("--cleanup-staging", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    row = load_manifest(root / "registry/task_manifest.jsonl", args.slot)
    verify_finalized(row)

    source = root / "tasks" / args.slot
    destination = root / "output" / args.slot
    if not source.is_dir():
        raise SystemExit(f"staging task is missing: {source}")
    if destination.exists():
        raise SystemExit(f"output already exists: {destination}")
    validate(root, source)

    output = root / "output"
    output.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{args.slot}-publish-", dir=output))
    shutil.rmtree(temporary)
    try:
        shutil.copytree(
            source,
            temporary,
            ignore=shutil.ignore_patterns(".DS_Store", "__pycache__", "*.pyc"),
        )
        validate(root, temporary)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)

    result: dict[str, object] = {"slot": args.slot, "published": str(destination)}
    if args.cleanup_docker:
        result["docker_cleanup"] = cleanup_docker(args.slot, str(row["base_commit_hash"]))
    if args.cleanup_workspaces:
        result["workspace_cleanup"] = cleanup_workspaces(root, row)
    if args.cleanup_logs:
        result["log_cleanup"] = cleanup_logs(root, row)
    if args.cleanup_staging:
        shutil.rmtree(source)
        result["staging_removed"] = True
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
