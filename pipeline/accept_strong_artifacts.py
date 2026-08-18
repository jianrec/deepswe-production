#!/usr/bin/env python3
"""Validate and atomically accept V2 strong-agent artifacts.

The strong agent writes only to ``workspaces/strong-artifacts``. This
receiver is the sole path that can promote those outputs into a Harbor task
package and advance the manifest to hidden-test generation.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from filelock import exclusive_lock

from author_tasks import slugify, validate_design, validate_design_against_repo


MODEL = "claude-opus-4-8"
TEST_MODEL = "gpt-5.6-sol"
NEXT_STAGE = "qwen_hidden_tests"
SOURCE_EXCLUDE = re.compile(
    r"(^|/)(tests?|testdata|examples?|docs?|scripts?|\.github)(/|$)"
    r"|(^|/)[^/]*(?:_test|\.test|\.spec)\.[^/]+$"
    r"|\.(?:md|rst|txt|json|ya?ml|toml|lock|sh)$",
    re.I,
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def atomic_jsonl(path: Path, rows: list[dict]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(path)


def run(command: list[str], cwd: Path, timeout: int = 1800) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def repository_name(design: dict) -> str:
    repository = design.get("repository") or {}
    if not isinstance(repository, dict):
        raise ValueError("design.repository must be an object")
    full_name = str(repository.get("full_name") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full_name):
        raise ValueError(f"invalid repository full_name: {full_name!r}")
    return full_name


def base_commit(design: dict) -> str:
    repository = design.get("repository") or {}
    commit = str(repository.get("base_commit") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError(f"base commit must be a full lowercase SHA-1: {commit!r}")
    return commit


def verify_build_test(value: dict) -> None:
    if value.get("status") != "passed":
        raise ValueError("build-test.json status must be 'passed'")
    commands = value.get("commands")
    if not isinstance(commands, list) or not commands:
        raise ValueError("build-test.json must contain at least one command result")
    for index, item in enumerate(commands, 1):
        if not isinstance(item, dict) or not str(item.get("command") or "").strip():
            raise ValueError(f"build-test command {index} is malformed")
        returncode = item.get("returncode", item.get("exit_code"))
        if returncode != 0:
            raise ValueError(f"build-test command {index} did not pass: returncode={returncode!r}")


def normalize_usage(value: dict) -> dict:
    if value.get("model") not in {None, MODEL}:
        raise ValueError(f"usage.json reports the wrong strong model: {value.get('model')!r}")
    result = dict(value)
    result["model"] = MODEL
    result.setdefault("measurement", "unavailable_for_http_provider")
    for name in ("input", "cache", "output"):
        raw = result.get(name, 0)
        if not isinstance(raw, int) or raw < 0:
            raise ValueError(f"usage.{name} must be a non-negative integer")
        result[name] = raw
    return result


def validate_candidate(root: Path, repository: str) -> dict:
    candidates = load_jsonl(root / "registry/repository-candidates.jsonl")
    candidate = next((item for item in candidates if item.get("full_name") == repository), None)
    if candidate is None:
        raise ValueError(f"repository is not in the audited candidate registry: {repository}")
    if int(candidate.get("stars") or 0) < 1000:
        raise ValueError(f"repository is below the 1,000-star floor: {candidate.get('stars')}")
    if int(candidate.get("test_capacity") or 0) < 100:
        raise ValueError(f"repository exposes fewer than 100 reportable tests: {candidate.get('test_capacity')}")
    preflight = candidate.get("runtime_preflight") or {}
    if preflight.get("status") != "passed":
        raise ValueError("repository has no passing offline Linux preflight")
    return candidate


def patch_stats(repo: Path, patch: Path, commit: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="deepswe-accept-") as directory:
        worktree = Path(directory) / "repo"
        add = run(["git", "worktree", "add", "--detach", str(worktree), commit], repo)
        if add.returncode:
            raise ValueError(f"cannot create validation worktree: {add.stderr[-1500:]}")
        try:
            checked = run(["git", "apply", "--check", "--whitespace=nowarn", str(patch)], worktree)
            if checked.returncode:
                raise ValueError(f"solution.patch does not apply to {commit}: {checked.stderr[-2000:]}")
            applied = run(["git", "apply", "--index", "--whitespace=nowarn", str(patch)], worktree)
            if applied.returncode:
                raise ValueError(f"solution.patch application failed: {applied.stderr[-2000:]}")
            diff_check = run(["git", "diff", "--cached", "--check"], worktree)
            if diff_check.returncode:
                raise ValueError(f"solution.patch fails git diff --check: {diff_check.stdout[-2000:]}")
            names = run(["git", "diff", "--cached", "--name-only", commit], worktree)
            if names.returncode:
                raise ValueError(names.stderr[-1500:])
            changed = [line for line in names.stdout.splitlines() if line]
            source_files = [path for path in changed if not SOURCE_EXCLUDE.search(path)]
            numstat = run(["git", "diff", "--cached", "--numstat", commit], worktree)
            source_set = set(source_files)
            source_changed_lines = 0
            total_changed_lines = 0
            for line in numstat.stdout.splitlines():
                fields = line.split("\t")
                if len(fields) < 3 or not fields[0].isdigit() or not fields[1].isdigit():
                    continue
                changed_lines = int(fields[0]) + int(fields[1])
                total_changed_lines += changed_lines
                if fields[-1] in source_set:
                    source_changed_lines += changed_lines
            return {
                "changed_files": changed,
                "source_files": source_files,
                "source_file_count": len(source_files),
                "source_changed_lines": source_changed_lines,
                "total_changed_lines": total_changed_lines,
            }
        finally:
            run(["git", "worktree", "remove", "--force", str(worktree)], repo)
            run(["git", "worktree", "prune"], repo)


def validate_patch_coverage(design: dict, stats: dict) -> None:
    source_files = set(stats["source_files"])
    if not 7 <= len(source_files) <= 16:
        raise ValueError(f"reference patch source file count outside 7..16: {len(source_files)}")
    changed_lines = int(stats["source_changed_lines"])
    if not 500 <= changed_lines <= 1800:
        raise ValueError(f"reference patch source changed lines outside 500..1800: {changed_lines}")
    declared = {str(item) for item in design.get("affected_source_files", []) if str(item)}
    covered = declared & source_files
    required = max(7, (3 * len(declared) + 3) // 4)
    if len(covered) < required:
        raise ValueError(
            f"reference patch covers {len(covered)}/{len(declared)} declared source files; "
            f"requires {required}; missing={sorted(declared - covered)}"
        )
    missing_stages = []
    for index, stage in enumerate(design.get("pr_chain", []), 1):
        stage_files = {str(item) for item in stage.get("files", []) if str(item)}
        if stage_files and source_files.isdisjoint(stage_files):
            missing_stages.append(str(stage.get("stage") or index))
    if missing_stages:
        raise ValueError(f"reference patch omits PR-chain stages: {missing_stages}")


def instruction_text(design: dict) -> str:
    issue = str(design["issue"]).strip()
    public_api = "\n".join(f"- {item}" for item in design.get("public_api_contract", []))
    acceptance = "\n".join(f"- {item}" for item in design.get("acceptance_criteria", []))
    suffix = f"\n\nPublic API contract:\n{public_api}\n\nAcceptance criteria:\n{acceptance}\n"
    if len(issue) + len(suffix) <= 6000:
        return issue + suffix
    budget = max(1200, 6000 - len(suffix) - 4)
    return issue[:budget].rstrip() + "..." + suffix


def task_toml(task_id: str, design: dict, language: str, repository: str, commit: str) -> str:
    title = str(design["title"]).replace('"', "'")
    return f'''schema_version = "1.3"
artifacts = ["/logs/artifacts/model.patch"]

[task]
name = "datacurve/{task_id}"
description = "{title}"
authors = []
keywords = ["{language}", "feature-request", "deepswe"]

[metadata]
ext_id = "{design['slot']}"
task_id = "{task_id}"
display_title = "{title}"
display_description = "{title}"
original_title = "{title}"
category = "feature_request"
language = "{language}"
repository_url = "https://github.com/{repository}.git"
base_commit_hash = "{commit}"

[verifier]
network_mode = "no-network"
environment_mode = "separate"
timeout_sec = 1800.0

[verifier.environment]
build_timeout_sec = 1800.0
cpus = 2
memory_mb = 8192
storage_mb = 20480

[agent]
network_mode = "no-network"
timeout_sec = 5400.0

[environment]
build_timeout_sec = 1800.0
os = "linux"
cpus = 2
memory_mb = 8192
storage_mb = 20480
gpus = 0

[environment.env]
[solution.env]
'''


def create_package(destination: Path, artifact_dir: Path, design: dict, task_id: str, language: str, repository: str, commit: str) -> None:
    (destination / "authoring").mkdir(parents=True)
    (destination / "solution").mkdir()
    (destination / "environment").mkdir()
    shutil.copy2(artifact_dir / "issue-design.json", destination / "authoring/issue-design.json")
    shutil.copy2(artifact_dir / "solution.patch", destination / "solution/solution.patch")
    shutil.copy2(artifact_dir / "build-test.json", destination / "authoring/strong-build-test.json")
    shutil.copy2(artifact_dir / "usage.json", destination / "authoring/strong-usage.json")
    (destination / "instruction.md").write_text(instruction_text(design), encoding="utf-8")
    (destination / "task.toml").write_text(task_toml(task_id, design, language, repository, commit), encoding="utf-8")
    (destination / "pre_artifacts.sh").write_text(
        f"#!/bin/sh\nset -eu\ncd /app\ngit diff --binary {commit} HEAD > /logs/artifacts/model.patch\n",
        encoding="utf-8",
    )
    (destination / "solution/solve.sh").write_text(
        "#!/bin/sh\nset -eu\ncd /app\ngit apply --whitespace=nowarn /solution/solution.patch\n",
        encoding="utf-8",
    )
    (destination / "environment/Dockerfile").write_text(
        "FROM ubuntu:24.04\n"
        "ENV DEBIAN_FRONTEND=noninteractive\n"
        "RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates build-essential python3 && rm -rf /var/lib/apt/lists/*\n"
        "WORKDIR /app\n"
        "COPY repo/ /app/\n",
        encoding="utf-8",
    )
    os.chmod(destination / "pre_artifacts.sh", 0o755)
    os.chmod(destination / "solution/solve.sh", 0o755)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--slot", required=True)
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--repository", required=True, help="expected owner/repository")
    parser.add_argument("--base-commit", required=True, help="expected full commit SHA")
    args = parser.parse_args()
    root = args.root.resolve()
    artifact_dir = (args.artifact_dir or root / "workspaces/strong-artifacts" / args.slot).resolve()
    required = ("issue-design.json", "solution.patch", "build-test.json", "usage.json")
    missing = [name for name in required if not (artifact_dir / name).is_file()]
    if missing:
        raise SystemExit(f"strong artifact directory is incomplete: missing {missing}")

    design = load_json(artifact_dir / "issue-design.json")
    build_test = load_json(artifact_dir / "build-test.json")
    usage = normalize_usage(load_json(artifact_dir / "usage.json"))
    if design.get("slot") not in {None, args.slot}:
        raise SystemExit(f"design slot mismatch: {design.get('slot')!r}")
    design["slot"] = args.slot
    design["pipeline_version"] = "2.0"
    design["author_model"] = MODEL
    repository = repository_name(design)
    commit = base_commit(design)
    if repository != args.repository or commit != args.base_commit:
        raise SystemExit(
            f"strong artifact target mismatch: got {repository}@{commit}, "
            f"expected {args.repository}@{args.base_commit}"
        )
    repo = root / "workspaces/repositories" / repository.replace("/", "__")
    if not (repo / ".git").exists():
        raise SystemExit(f"cached repository is missing: {repo}")
    validate_candidate(root, repository)
    dirty = run(["git", "status", "--porcelain"], repo)
    if dirty.returncode or dirty.stdout.strip():
        raise SystemExit(f"cached repository is not clean: {dirty.stdout[:1500] or dirty.stderr[-1500:]}")
    resolved = run(["git", "rev-parse", commit], repo)
    if resolved.returncode or resolved.stdout.strip() != commit:
        raise SystemExit(f"pinned commit is unavailable in cached repository: {commit}")

    validate_design(design)
    validate_design_against_repo(design, repo)
    verify_build_test(build_test)
    stats = patch_stats(repo, artifact_dir / "solution.patch", commit)
    validate_patch_coverage(design, stats)

    slot_number = args.slot.rsplit("-", 1)[-1]
    task_id = f"{slugify(str(design['title']), 'deep-swe-task')}-{slot_number}"
    design["task_id_slug"] = task_id
    (artifact_dir / "issue-design.json").write_text(json.dumps(design, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (artifact_dir / "usage.json").write_text(json.dumps(usage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest_path = root / "registry/task_manifest.jsonl"
    lock_path = root / "registry/.manifest.lock"
    with exclusive_lock(lock_path):
        rows = load_jsonl(manifest_path)
        row = next((item for item in rows if item.get("slot") == args.slot), None)
        if row is None:
            raise SystemExit(f"manifest slot does not exist: {args.slot}")
        if row.get("stage") not in {"repository_discovery", "strong_model_authoring"} or row.get("status") not in {"pending", "in_progress", "failed"}:
            raise SystemExit(f"slot is not eligible for strong-artifact acceptance: {row.get('status')}/{row.get('stage')}")
        if row.get("language") != str((design.get("repository") or {}).get("language") or row.get("language")):
            raise SystemExit("design repository language does not match the manifest slot")
        destination = root / "tasks" / args.slot
        if destination.exists():
            raise SystemExit(f"task package already exists: {destination}")
        temporary = Path(tempfile.mkdtemp(prefix=f".{args.slot}-accept-", dir=root / "tasks"))
        try:
            create_package(temporary, artifact_dir, design, task_id, row["language"], repository, commit)
            temporary.replace(destination)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

        row.update(
            {
                "status": "in_progress",
                "stage": NEXT_STAGE,
                "repository": repository,
                "base_commit_hash": commit,
                "task_id": task_id,
                "author_model": MODEL,
                "test_model": TEST_MODEL,
                "artifacts": [
                    "authoring/issue-design.json",
                    "authoring/strong-build-test.json",
                    "authoring/strong-usage.json",
                    "instruction.md",
                    "task.toml",
                    "pre_artifacts.sh",
                    "environment/Dockerfile",
                    "solution/solution.patch",
                    "solution/solve.sh",
                ],
                "usage": {**row.get("usage", {}), "strong_agent": usage},
                "reference_stats": stats,
                "errors": [],
            }
        )
        try:
            atomic_jsonl(manifest_path, rows)
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        event = {
            "timestamp": now(),
            "slot": args.slot,
            "stage": "strong_artifact_acceptance",
            "status": "success",
            "model": MODEL,
            "repository": repository,
            "base_commit_hash": commit,
            "task_id": task_id,
            "reference_stats": stats,
            "usage": usage,
            "api_key_stored": False,
        }
        with (root / "registry/production-events.jsonl").open("a", encoding="utf-8") as events:
            events.write(json.dumps(event, ensure_ascii=False) + "\n")

    print(json.dumps({"slot": args.slot, "status": "accepted", "stage": NEXT_STAGE, "task_id": task_id, "reference_stats": stats}, ensure_ascii=False))


if __name__ == "__main__":
    main()
