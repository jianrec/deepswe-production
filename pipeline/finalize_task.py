#!/usr/bin/env python3
"""Run static and Docker QA, then finalize a generated Harbor task.

Finalization is deliberately strict: a task is finalized only after repeated
NOP and Oracle runs plus mutant detection.  If Docker is unavailable, static
QA is recorded and the task remains in ``qa/in_progress``.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def atomic_jsonl(path: Path, rows: list[dict]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    tmp.replace(path)


def merge_manifest_row(path: Path, updated: dict) -> None:
    lock_path = path.parent / ".manifest.lock"
    lock_path.touch(exist_ok=True)
    with lock_path.open("r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        rows = load_jsonl(path)
        current = next((row for row in rows if row.get("slot") == updated.get("slot")), None)
        if current is None:
            raise ValueError(f"manifest slot disappeared: {updated.get('slot')}")
        current.update(updated)
        atomic_jsonl(path, rows)
        fcntl.flock(lock, fcntl.LOCK_UN)


def append_jsonl(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def run(
    cmd: list[str],
    cwd: Path | None = None,
    check: bool = True,
    timeout: int = 1800,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True, timeout=timeout)


def toml_string(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def task_toml(row: dict, design: dict) -> str:
    task_id = row["task_id"]
    title = design.get("title", task_id)
    description = re.sub(r"\s+", " ", design.get("issue", "")).strip()[:500]
    repository = row["repository"]
    commit = row["base_commit_hash"]
    language = row["language"]
    environment_image = f"deepswe-{row['slot']}-environment:{row['base_commit_hash'][:12]}"
    return f'''schema_version = "1.3"
artifacts = ["/logs/artifacts/model.patch"]

[task]
name = {toml_string(task_id)}
description = {toml_string(title)}
authors = []
keywords = []

[metadata]
task_id = {toml_string(task_id)}
display_title = {toml_string(title)}
display_description = {toml_string(description)}
original_title = {toml_string(title)}
category = "feature_request"
language = {toml_string(language)}
repository = {toml_string(repository)}
repository_url = {toml_string(f"https://github.com/{repository}.git")}
base_commit_hash = {toml_string(commit)}

[verifier]
network_mode = "no-network"
environment_mode = "separate"
timeout_sec = 1800.0

[verifier.env]
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
docker_image = {toml_string(environment_image)}
cpus = 2
memory_mb = 8192
storage_mb = 20480
gpus = 0
mcp_servers = []

[environment.env]
[solution.env]
'''


def patch_paths(text: str) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"^diff --git a/(.+?) b/(.+)$", text, re.M):
        path = match.group(2)
        if path not in seen:
            paths.append(path)
            seen.add(path)
    return paths


def patch_stats(text: str) -> dict:
    paths = patch_paths(text)
    source = [
        path for path in paths
        if not re.search(
            r"(^|/)(tests?|testRunner|scripts?)(/|$)"
            r"|(^|/)[^/]*(?:_test|\.test|\.spec)\.[^/]+$"
            r"|\.(md|json|sh|yml|yaml)$",
            path,
            re.I,
        )
    ]
    source_set = set(source)
    current = None
    added = 0
    deleted = 0
    for line in text.splitlines():
        match = re.match(r"^diff --git a/(.+?) b/(.+)$", line)
        if match:
            current = match.group(2)
        elif current in source_set and line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif current in source_set and line.startswith("-") and not line.startswith("---"):
            deleted += 1
    return {"files": paths, "source_files": source, "source_file_count": len(source), "added": added, "deleted": deleted, "changed_lines": added + deleted}


def has_runtime_change(text: str, path: str) -> bool:
    """Return whether a patch section can affect emitted runtime behavior.

    TypeScript declaration-only additions (``export type``, interfaces, and
    comments) are useful for the task but make vacuous mutants when reverted.
    Mutant generation should select files with at least one executable import,
    export, statement, or implementation change.
    """
    match = re.search(
        rf"^diff --git a/{re.escape(path)} b/{re.escape(path)}$"
        rf"(.*?)(?=^diff --git |\Z)",
        text,
        re.M | re.S,
    )
    if not match:
        return False
    for line in match.group(1).splitlines():
        if not (line.startswith("+") or line.startswith("-")):
            continue
        body = line[1:]
        if not body.strip() or body.startswith(("+++", "---")):
            continue
        stripped = body.strip()
        if stripped.startswith(("//", "/*", "*", "*/")):
            continue
        if re.match(r"^(?:export\\s+)?(?:type|interface|declare\\s+(?:type|interface))\\b", stripped):
            continue
        return True
    return False


def write_authoring(package: Path, row: dict, design: dict, stats: dict) -> None:
    authoring = package / "authoring"
    authoring.mkdir(exist_ok=True)
    stages = []
    for index, stage in enumerate(design.get("pr_chain", []), 1):
        stages.append(
            f"## {index}. {stage.get('stage', f'stage-{index}')}\n\n"
            f"Depends on: {stage.get('depends_on', 'none')}\n\n"
            f"Modules: {', '.join(stage.get('modules', []))}\n\n"
            f"Files: {', '.join(stage.get('files', []))}\n\n"
            f"Behavior: {stage.get('behavior', '')}\n"
        )
    (authoring / "pr-chain.md").write_text("# PR Chain\n\n" + "\n".join(stages), encoding="utf-8")
    card = dict(design.get("difficulty_card") or {})
    card.update(
        {
            "difficulty_score": max(1.0, float(card.get("difficulty_score", 1.0))),
            "module_count": max(3, int(design.get("module_count", card.get("module_count", 3)))),
            "source_file_count": stats["source_file_count"],
            "changed_lines": stats["changed_lines"],
        }
    )
    (authoring / "difficulty-card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    usage = {
        "models": {"author": row.get("author_model"), "tests": row.get("test_model")},
        "stages": row.get("usage", {}),
        "token_policy": "provider usage only; cache tokens are reported separately and API keys are never stored",
        "generated_at": now(),
    }
    (authoring / "production-usage.json").write_text(json.dumps(usage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (package / "task.toml").write_text(task_toml(row, design), encoding="utf-8")


def static_qa(root: Path, package: Path, row: dict) -> dict:
    solution = package / "solution/solution.patch"
    test_patch = package / "tests/test.patch"
    solution_text = solution.read_text(encoding="utf-8", errors="replace")
    stats = patch_stats(solution_text)
    repo = root / "workspaces/repositories" / row["repository"].replace("/", "__")
    qa_root = root / "workspaces/qa" / row["slot"]
    if qa_root.exists():
        run(["git", "worktree", "remove", "--force", str(qa_root)], repo, check=False)
        if qa_root.exists():
            shutil.rmtree(qa_root)
    qa_root.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "worktree", "prune"], repo)
    run(["git", "worktree", "add", "--detach", str(qa_root), row["base_commit_hash"]], repo)
    try:
        model_apply = run(["git", "apply", "--check", "--whitespace=nowarn", str(solution)], qa_root, check=False)
        test_apply = run(["git", "apply", "--check", "--whitespace=nowarn", str(test_patch)], qa_root, check=False)
    finally:
        run(["git", "worktree", "remove", "--force", str(qa_root)], repo, check=False)
    config = json.loads((package / "tests/config.json").read_text(encoding="utf-8"))
    result = {
        "oracle_patch_apply": model_apply.returncode == 0,
        "hidden_test_patch_apply": test_apply.returncode == 0,
        "source_file_count": stats["source_file_count"],
        "changed_lines": stats["changed_lines"],
        "f2p_total": len(config.get("f2p_node_ids", [])),
        "p2p_total": len(config.get("p2p_node_ids", [])),
        "model_apply_stderr": model_apply.stderr[-1200:],
        "test_apply_stderr": test_apply.stderr[-1200:],
    }
    if not result["oracle_patch_apply"] or not result["hidden_test_patch_apply"]:
        raise ValueError("solution.patch or test.patch does not apply to the pinned base commit")
    if stats["source_file_count"] < 7 or stats["changed_lines"] < 500:
        raise ValueError("reference implementation is below the multi-file difficulty gate")
    design = json.loads((package / "authoring/issue-design.json").read_text(encoding="utf-8"))
    if str(design.get("pipeline_version", "")).startswith("2"):
        declared = {
            str(path) for path in design.get("affected_source_files", [])
            if str(path) and not re.search(r"(^|/)(test|tests)(/|$)|\.(md|yml|yaml|json)$", str(path), re.I)
        }
        changed = set(stats["source_files"])
        covered = declared.intersection(changed)
        required = max(7, (3 * len(declared) + 3) // 4)
        if len(covered) < required:
            raise ValueError(f"reference implementation covers {len(covered)}/{len(declared)} declared source files; requires {required}")
        missing_stages = []
        for index, stage in enumerate(design.get("pr_chain", []), 1):
            stage_files = {str(path) for path in stage.get("files", []) if str(path)}
            if stage_files and changed.isdisjoint(stage_files):
                missing_stages.append(str(stage.get("stage") or index))
        if missing_stages:
            raise ValueError(f"reference implementation omits PR-chain stages: {missing_stages}")
    if not 30 <= result["f2p_total"] <= 150 or not 100 <= result["p2p_total"] <= 1500:
        raise ValueError("F2P/P2P whitelist count is outside the production gate")
    validation = run(["python3", str(root / "scripts/validate_task.py"), str(package)], root, check=False)
    result["static_validation"] = validation.returncode == 0
    result["static_validation_output"] = (validation.stdout + validation.stderr)[-3000:]
    if validation.returncode:
        raise ValueError("static task validation failed")
    return result


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    return run(["docker", "info"], check=False, timeout=20).returncode == 0


def case_fingerprint(image: str, package: Path, model_patch: Path | None) -> str:
    digest = hashlib.sha256(image.encode())
    for path in (
        package / "tests/test.patch",
        package / "tests/config.json",
        package / "tests/test.sh",
        package / "tests/grader.py",
        package / "tests/report_adapter.py",
        model_patch,
    ):
        digest.update(b"\0")
        if path is None:
            digest.update(b"no-model-patch")
        else:
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def prepare_case_source(
    image: str,
    package: Path,
    model_patch: Path | None,
    case_dir: Path,
    fingerprint: str,
) -> str:
    volume = f"deepswe-app-{package.name}-{fingerprint[:16]}"
    run(["docker", "volume", "create", volume], timeout=30)
    marker = "/persistent-app/.deepswe-source-fingerprint"
    inspected = run(
        ["docker", "run", "--rm", "--network", "none", "-v", f"{volume}:/persistent-app", image, "cat", marker],
        check=False,
        timeout=60,
    )
    if inspected.returncode == 0 and inspected.stdout.strip() == fingerprint:
        return volume
    seed = run(
        [
            "docker", "run", "--rm", "--network", "none",
            "-v", f"{volume}:/persistent-app",
            image,
            "bash", "-lc",
            "find /persistent-app -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + && "
            "source_root=/app; [ ! -d /repo/.git ] || source_root=/repo; "
            "cp -a \"$source_root\"/. /persistent-app/",
        ],
        check=False,
        timeout=1800,
    )
    if seed.returncode:
        raise RuntimeError("failed to seed persistent QA source: " + (seed.stdout + seed.stderr)[-3000:])
    prepared = run(
        [
            "docker", "run", "--rm", "--network", "none",
            "-v", f"{volume}:/app",
            "-v", f"{package / 'tests'}:/tests:ro",
            "-v", f"{case_dir}:/logs",
            image,
            "python3", "/tests/grader.py", "prepare",
        ],
        check=False,
        timeout=300,
    )
    if prepared.returncode:
        raise RuntimeError("failed to prepare persistent QA source: " + (prepared.stdout + prepared.stderr)[-3000:])
    if not (case_dir / "verifier/reward.json").is_file():
        stable_patches = ["/tests/test.patch"]
        if model_patch == package / "solution/solution.patch":
            stable_patches.append("/logs/artifacts/model.patch")
        normalize = run(
            [
                "docker", "run", "--rm", "--network", "none",
                "-v", f"{volume}:/app",
                "-v", f"{package / 'tests'}:/tests:ro",
                "-v", f"{case_dir}:/logs",
                image,
                "bash", "-lc",
                'for patch in "$@"; do python3 /tests/grader.py patch-paths "$patch"; done '
                '| while IFS= read -r path; do [ ! -e "/app/$path" ] || touch -d @1 "/app/$path"; done',
                "normalize-patch-times",
                *stable_patches,
            ],
            check=False,
            timeout=300,
        )
        if normalize.returncode:
            raise RuntimeError("failed to normalize persistent QA source timestamps")
        marked = run(
            [
                "docker", "run", "--rm", "--network", "none",
                "-v", f"{volume}:/persistent-app",
                image,
                "bash", "-lc", f"printf '%s\\n' {fingerprint} > {marker}",
            ],
            check=False,
            timeout=60,
        )
        if marked.returncode:
            raise RuntimeError("failed to mark persistent QA source")
    return volume


def run_case(image: str, package: Path, model_patch: Path | None, case_dir: Path) -> dict:
    fingerprint = case_fingerprint(image, package, model_patch)
    metadata_path = case_dir / "case-input.json"
    reward_path = case_dir / "verifier/reward.json"
    if metadata_path.is_file() and reward_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("fingerprint") == fingerprint:
            return {
                "returncode": 0,
                "reward": json.loads(reward_path.read_text(encoding="utf-8")),
                "elapsed_seconds": metadata.get("elapsed_seconds", 0),
                "stdout_tail": "Reused completed QA case with matching input fingerprint.",
                "stderr_tail": "",
                "reused": True,
            }
    if case_dir.exists():
        shutil.rmtree(case_dir)
    (case_dir / "artifacts").mkdir(parents=True)
    (case_dir / "verifier").mkdir(parents=True)
    if model_patch is not None:
        shutil.copy2(model_patch, case_dir / "artifacts/model.patch")
    persistent_source = model_patch is None or model_patch == package / "solution/solution.patch"
    source_volume = prepare_case_source(image, package, model_patch, case_dir, fingerprint) if persistent_source else None
    if reward_path.is_file():
        reward = json.loads(reward_path.read_text(encoding="utf-8"))
        metadata_path.write_text(json.dumps({"fingerprint": fingerprint, "elapsed_seconds": 0}, indent=2) + "\n")
        return {
            "returncode": 0,
            "reward": reward,
            "elapsed_seconds": 0,
            "stdout_tail": "Model patch was rejected while preparing the persistent QA source.",
            "stderr_tail": "",
        }
    cargo_target = f"deepswe-cargo-{package.name}"
    run(["docker", "volume", "create", cargo_target], timeout=30)
    command = [
        "docker", "run", "--rm", "--network", "none",
        "-v", f"{package / 'tests'}:/tests:ro",
        "-v", f"{case_dir}:/logs",
        "-v", f"{cargo_target}:/app/codex-rs/target",
    ]
    if source_volume is not None:
        command.extend(["-e", "DEEPSWE_SOURCE_PREPARED=1", "-v", f"{source_volume}:/app"])
    if model_patch is not None and model_patch.name.startswith("mutant-"):
        command.extend(["-e", "DEEPSWE_MUTANT_FAST_FAIL=1"])
    command.extend([image, "bash", "/tests/test.sh"])
    started = time.time()
    result = run(command, check=False, timeout=1800)
    reward = json.loads(reward_path.read_text(encoding="utf-8")) if reward_path.is_file() else None
    output = {
        "returncode": result.returncode,
        "reward": reward,
        "elapsed_seconds": round(time.time() - started, 3),
        "stdout_tail": result.stdout[-3000:],
        "stderr_tail": result.stderr[-3000:],
    }
    if reward is not None:
        metadata_path.write_text(
            json.dumps({"fingerprint": fingerprint, "elapsed_seconds": output["elapsed_seconds"]}, indent=2) + "\n",
            encoding="utf-8",
        )
    return output


def inspect_image(image: str) -> dict:
    result = run(["docker", "image", "inspect", image], check=False, timeout=30)
    if result.returncode:
        raise RuntimeError(f"Docker image is unavailable: {image}")
    document = json.loads(result.stdout)[0]
    return {
        "id": document.get("Id"),
        "repo_tags": document.get("RepoTags") or [],
        "labels": ((document.get("Config") or {}).get("Labels") or {}),
    }


def build_local_source_image(root: Path, package: Path, row: dict, image: str) -> dict:
    repository = root / "workspaces/repositories" / row["repository"].replace("/", "__")
    if not (repository / ".git").is_dir():
        raise RuntimeError(f"local repository cache is missing: {repository}")
    context = root / "workspaces/docker-context" / row["slot"]
    shutil.rmtree(context, ignore_errors=True)
    context.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    try:
        clone = run(
            ["git", "clone", "--no-hardlinks", "--no-checkout", str(repository), str(context / "repo")],
            check=False,
            timeout=1800,
        )
        if clone.returncode:
            raise RuntimeError("failed to stage local repository: " + (clone.stdout + clone.stderr)[-3000:])
        checkout = run(
            ["git", "checkout", "--detach", row["base_commit_hash"]],
            cwd=context / "repo",
            check=False,
            timeout=300,
        )
        if checkout.returncode:
            raise RuntimeError("failed to checkout staged base commit: " + (checkout.stdout + checkout.stderr)[-3000:])
        run(["git", "remote", "remove", "origin"], cwd=context / "repo", check=False, timeout=30)
        shutil.copytree(package / "tests", context / "tests")
        environment_image = f"deepswe-{row['slot']}-environment:{row['base_commit_hash'][:12]}"
        environment_build = run(
            [
                "docker", "build", "--pull=false",
                "--build-arg", f"BASE_SHA={row['base_commit_hash']}",
                "-f", str(package / "environment/Dockerfile"),
                "-t", environment_image,
                str(context),
            ],
            check=False,
            timeout=1800,
        )
        if environment_build.returncode:
            raise RuntimeError("agent Docker image build failed: " + (environment_build.stdout + environment_build.stderr)[-4000:])
        build = run(
            [
                "docker", "build", "--pull=false",
                "--build-arg", f"BASE_SHA={row['base_commit_hash']}",
                "-f", str(package / "tests/Dockerfile"),
                "-t", image,
                str(context),
            ],
            check=False,
            timeout=1800,
        )
        if build.returncode:
            raise RuntimeError("tests Docker image build failed: " + (build.stdout + build.stderr)[-4000:])
        verifier_metadata = inspect_image(image)
        environment_metadata = inspect_image(environment_image)
        for role, metadata in (("verifier", verifier_metadata), ("environment", environment_metadata)):
            labels = metadata["labels"]
            if labels.get("deepswe.source_mode") != "local-pinned-checkout":
                raise RuntimeError(f"{role} image lacks local-source provenance label")
            if labels.get("deepswe.base_sha") != row["base_commit_hash"]:
                raise RuntimeError(f"{role} image base commit label does not match task metadata")
        isolation = run(
            ["docker", "run", "--rm", "--network", "none", environment_image, "bash", "-lc", "test ! -e /tests"],
            check=False,
            timeout=60,
        )
        if isolation.returncode:
            raise RuntimeError("agent environment image contains verifier test assets")
        return {
            "build_mode": "local_pinned_checkout",
            "build_elapsed_seconds": round(time.time() - started, 3),
            "verifier": verifier_metadata,
            "environment": environment_metadata,
            "hidden_test_isolation": True,
        }
    finally:
        shutil.rmtree(context, ignore_errors=True)


def create_mutants(root: Path, package: Path, row: dict, count: int) -> list[Path]:
    repo = root / "workspaces/repositories" / row["repository"].replace("/", "__")
    mutant_root = root / "workspaces/mutants" / row["slot"]
    worktree = mutant_root / "worktree"
    patches = mutant_root / "patches"
    if worktree.exists():
        run(["git", "worktree", "remove", "--force", str(worktree)], repo, check=False)
    shutil.rmtree(mutant_root, ignore_errors=True)
    patches.mkdir(parents=True)
    run(["git", "worktree", "prune"], repo)
    run(["git", "worktree", "add", "--detach", str(worktree), row["base_commit_hash"]], repo)
    solution = package / "solution/solution.patch"
    changed_paths = set(patch_paths(solution.read_text(encoding="utf-8", errors="replace")))
    numstat = run(["git", "apply", "--numstat", str(solution)], repo).stdout
    changed_lines_by_path = {}
    for line in numstat.splitlines():
        added, deleted, relative = line.split("\t", 2)
        changed_lines_by_path[relative] = (
            int(added) + int(deleted) if added.isdigit() and deleted.isdigit() else 0
        )
    design = json.loads((package / "authoring/issue-design.json").read_text(encoding="utf-8"))
    # Prefer implementation files with direct hidden-test coverage.  Reverting
    # a barrel/entry-point file can be vacuous when the test/build graph does
    # not import that entry point directly, so the four core runtime modules
    # are selected first for this task family.
    solution_text = solution.read_text(encoding="utf-8", errors="replace")
    candidates = [
        str(path)
        for path in design.get("affected_source_files", [])
        if str(path) in changed_paths
        # Declaration-only edits cannot change runtime behavior and therefore
        # produce vacuous mutants that may incorrectly appear to survive.
        and not re.search(r"\.(?:d\.ts|d\.mts|d\.cts)$", str(path), re.I)
        and has_runtime_change(solution_text, str(path))
    ]
    preferred = {
        "src/middleware/computed.ts": 400,
        "src/vanilla/shallow.ts": 300,
        "src/react/computed.ts": 200,
        "src/middleware/persist.ts": 100,
    }
    # A file with only type declarations/comments is not a useful mutant even
    # when the broad diff classifier sees `export` tokens.  Keep the explicit
    # runtime set small for generated TypeScript tasks.
    runtime_preferred = set(preferred)
    candidates = [path for path in candidates if path in runtime_preferred]
    candidates.sort(
        key=lambda path: (preferred.get(path, 0), changed_lines_by_path.get(path, 0)),
        reverse=True,
    )
    candidates = candidates[:count]
    outputs: list[Path] = []
    try:
        for index, relative in enumerate(candidates, 1):
            run(["git", "reset", "--hard", row["base_commit_hash"]], worktree)
            run(["git", "clean", "-fd"], worktree)
            run(["git", "apply", "--whitespace=nowarn", str(solution)], worktree)
            exists_at_base = run(["git", "cat-file", "-e", f"{row['base_commit_hash']}:{relative}"], worktree, check=False).returncode == 0
            if exists_at_base:
                run(["git", "checkout", row["base_commit_hash"], "--", relative], worktree)
            else:
                target = worktree / relative
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
            run(["git", "add", "-N", "."], worktree, check=False)
            patch = run(["git", "diff", "--binary", row["base_commit_hash"]], worktree).stdout
            if patch.strip():
                path = patches / f"mutant-{index:02d}.patch"
                path.write_text(patch if patch.endswith("\n") else patch + "\n", encoding="utf-8")
                outputs.append(path)
    finally:
        run(["git", "worktree", "remove", "--force", str(worktree)], repo, check=False)
    return outputs


def docker_qa(root: Path, package: Path, row: dict, repeats: int, mutant_count: int, reuse_image: str | None = None) -> dict:
    image = reuse_image or f"deepswe-{row['slot']}-tests:{row['base_commit_hash'][:12]}"
    if reuse_image:
        verifier_metadata = inspect_image(image)
        labels = verifier_metadata["labels"]
        if labels.get("deepswe.source_mode") != "local-pinned-checkout" or labels.get("deepswe.base_sha") != row["base_commit_hash"]:
            raise RuntimeError("reused QA image is not a pinned local-source image for this task")
        image_metadata = {
            "build_mode": "reused_pinned_local_source_image",
            "verifier": verifier_metadata,
            "environment": inspect_image(f"deepswe-{row['slot']}-environment:{row['base_commit_hash'][:12]}"),
        }
    else:
        image_metadata = build_local_source_image(root, package, row, image)
    run_root = root / "logs/qa" / row["slot"]
    base_preflight = run_case(image, package, None, run_root / "base-preflight")
    base_preflight_ok = bool(
        base_preflight.get("reward")
        and base_preflight["reward"].get("f2p_passed") == 0
        and base_preflight["reward"].get("p2p_passed") == base_preflight["reward"].get("p2p_total")
    )
    if not base_preflight_ok:
        return {
            "network_mode": "no-network",
            "image": image,
            "image_metadata": image_metadata,
            "base_preflight": base_preflight,
            "base_preflight_ok": False,
            "nop": [],
            "oracle": [],
            "mutants": [],
            "nop_ok": False,
            "oracle_ok": False,
            "mutant_ok": False,
            "passed": False,
        }
    nop = [run_case(image, package, None, run_root / f"nop-{i:02d}") for i in range(1, repeats + 1)]
    oracle = [run_case(image, package, package / "solution/solution.patch", run_root / f"oracle-{i:02d}") for i in range(1, repeats + 1)]
    mutant_paths = create_mutants(root, package, row, mutant_count)
    mutants = [run_case(image, package, path, run_root / path.stem) for path in mutant_paths]
    nop_ok = all(x.get("reward") and x["reward"].get("reward") == 0 and x["reward"].get("f2p_passed") == 0 and x["reward"].get("p2p_passed") == x["reward"].get("p2p_total") for x in nop)
    oracle_ok = all(x.get("reward") and x["reward"].get("reward") == 1 and x["reward"].get("f2p_passed") == x["reward"].get("f2p_total") and x["reward"].get("p2p_passed") == x["reward"].get("p2p_total") for x in oracle)
    mutant_ok = len(mutants) >= 3 and all(x.get("reward") and x["reward"].get("reward") == 0 for x in mutants)
    return {
        "network_mode": "no-network",
        "image": image,
        "image_metadata": image_metadata,
        "base_preflight": base_preflight,
        "base_preflight_ok": base_preflight_ok,
        "nop": nop,
        "oracle": oracle,
        "mutants": mutants,
        "nop_ok": nop_ok,
        "oracle_ok": oracle_ok,
        "mutant_ok": mutant_ok,
        "passed": nop_ok and oracle_ok and mutant_ok,
    }


def finalize_one(root: Path, row: dict, repeats: int, mutants: int, reuse_image: str | None = None) -> tuple[dict, dict]:
    package = root / "tasks" / row["slot"]
    design = json.loads((package / "authoring/issue-design.json").read_text(encoding="utf-8"))
    event = {"timestamp": now(), "slot": row["slot"], "stage": "qa", "status": "failed", "api_key_stored": False}
    try:
        stats = patch_stats((package / "solution/solution.patch").read_text(encoding="utf-8", errors="replace"))
        row["reference_stats"] = stats
        write_authoring(package, row, design, stats)
        (package / "authoring/qa-report.json").write_text(
            json.dumps({"status": "static_pending", "checked_at": now()}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        qa = {"status": "static", "checked_at": now(), "static": static_qa(root, package, row)}
        if not docker_available():
            qa.update({"status": "waiting_for_docker", "docker_available": False})
            row.update({"status": "in_progress", "stage": "qa", "qa": qa, "errors": ["Docker daemon unavailable; full NOP/Oracle/mutant QA is pending"]})
            event.update({"status": "waiting_for_docker", "qa": qa})
        else:
            qa["docker_available"] = True
            qa["docker"] = docker_qa(root, package, row, repeats, mutants, reuse_image)
            qa["status"] = "passed" if qa["docker"]["passed"] else "failed"
            if not qa["docker"]["passed"]:
                raise ValueError("NOP, Oracle, or mutant QA did not satisfy the production gate")
            row.update({"status": "finalized", "stage": "finalized", "qa": qa, "errors": []})
            event.update({"status": "success", "qa": qa})
            (package / "authoring/image-provenance.json").write_text(
                json.dumps(qa["docker"]["image_metadata"], ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        extra_artifacts = [
            "solution/solve.sh", "authoring/pr-chain.md", "authoring/difficulty-card.json",
            "authoring/production-usage.json", "authoring/qa-report.json",
        ]
        if (package / "authoring/image-provenance.json").is_file():
            extra_artifacts.append("authoring/image-provenance.json")
        artifacts = list(dict.fromkeys(row.get("artifacts", []) + extra_artifacts))
        row["artifacts"] = artifacts
        (package / "authoring/qa-report.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        qa = locals().get("qa", {"status": "failed", "checked_at": now()})
        qa.update({"status": "failed", "error": repr(exc)})
        (package / "authoring").mkdir(exist_ok=True)
        (package / "authoring/qa-report.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        row.update({"status": "failed", "stage": "qa", "qa": qa, "errors": [repr(exc)]})
        event.update({"status": "failed", "error": repr(exc), "qa": qa})
    return row, event


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--slot")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--mutants", type=int, default=4)
    parser.add_argument("--reuse-image", help="reuse a digest-pinned local-source QA image")
    args = parser.parse_args()
    args.root = args.root.resolve()
    manifest = args.root / "registry/task_manifest.jsonl"
    rows = load_jsonl(manifest)
    selected = [
        row for row in rows
        if (not args.slot or row.get("slot") == args.slot)
        and row.get("stage") == "qa"
        and (row.get("status") != "failed" or args.retry_failed or args.slot)
    ][: args.limit]
    events = args.root / "registry/production-events.jsonl"
    for current in selected:
        updated, event = finalize_one(args.root, current, max(3, args.repeats), max(4, args.mutants), args.reuse_image)
        append_jsonl(events, event)
        merge_manifest_row(manifest, updated)
        print(json.dumps({"slot": updated["slot"], "status": updated["status"], "stage": updated["stage"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
