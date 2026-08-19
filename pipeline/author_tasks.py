#!/usr/bin/env python3
"""Author V2 task designs through an Anthropic-compatible provider."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


LANGUAGES = ("typescript", "go", "python", "javascript", "rust")
MODEL = "claude-opus-4-8"
DEFAULT_API = "https://www.packyapi.com/v1/messages"
MAX_REPOSITORY_SIZE_KB = 60000
MAX_TRACKED_FILES = 12000
MAX_WORKSPACE_MANIFESTS = 40


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def atomic_jsonl(path: Path, rows: list[dict]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def provider_name(api_url: str) -> str:
    return urllib.parse.urlparse(api_url).netloc or "anthropic-compatible"


def git(args: list[str], cwd: Path | None = None, timeout: int = 180) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, timeout=timeout)
    return result.stdout.strip()


def remove_partial_clone(repo: Path) -> None:
    """Remove a failed clone before retrying, with an actionable error on Windows."""
    if not repo.exists():
        return
    try:
        shutil.rmtree(repo)
    except OSError as exc:
        raise RuntimeError(f"unable to remove partial clone {repo}: {exc}") from exc


def checkout_repo(root: Path, candidate: dict) -> tuple[Path, str]:
    name = candidate["full_name"].replace("/", "__")
    repo = root / "workspaces" / "repositories" / name
    repo.parent.mkdir(parents=True, exist_ok=True)
    preflight_commit = str((candidate.get("runtime_preflight") or {}).get("base_commit_hash") or "").strip()
    clone_required = not (repo / ".git").exists()
    if not clone_required:
        existing_head = subprocess.run(
            ["git", "cat-file", "-e", "HEAD^{commit}"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        clone_required = existing_head.returncode != 0
    if clone_required:
        clone_errors: list[str] = []
        for attempt in range(1, 4):
            remove_partial_clone(repo)
            clone = subprocess.run(
                [
                    "git",
                    "-c",
                    "http.version=HTTP/1.1",
                    "clone",
                    "--depth",
                    "1",
                    "--no-tags",
                    "--single-branch",
                    candidate["clone_url"],
                    str(repo),
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=900,
            )
            if clone.returncode == 0 and (repo / ".git").exists():
                break
            clone_errors.append(
                f"attempt {attempt}: exit={clone.returncode}; "
                f"{(clone.stderr or clone.stdout)[-1000:].strip()}"
            )
            if attempt < 3:
                time.sleep(2 ** (attempt - 1))
        else:
            remove_partial_clone(repo)
            raise RuntimeError(
                f"unable to clone {candidate['full_name']} after 3 attempts: "
                + " | ".join(clone_errors)
            )
    # A portable candidate snapshot carries the commit used by its offline
    # preflight.  A fresh clone may point at a newer default-branch HEAD, so
    # materialize and pin that exact commit before authoring.  This keeps task
    # generation deterministic across machines.
    if preflight_commit:
        present = subprocess.run(
            ["git", "cat-file", "-e", f"{preflight_commit}^{{commit}}"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        if present.returncode:
            fetched = subprocess.run(
                ["git", "-c", "http.version=HTTP/1.1", "fetch", "--no-tags", "--depth", "1", "origin", preflight_commit],
                cwd=repo,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=900,
                check=False,
            )
            if fetched.returncode:
                raise RuntimeError(
                    f"cannot materialize preflight commit {preflight_commit} for {candidate['full_name']}: "
                    f"{(fetched.stdout + fetched.stderr)[-1000:]}"
                )
        checked_out = subprocess.run(
            ["git", "checkout", "--detach", preflight_commit],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if checked_out.returncode:
            raise RuntimeError(
                f"cannot checkout preflight commit {preflight_commit} for {candidate['full_name']}: "
                f"{(checked_out.stdout + checked_out.stderr)[-1000:]}"
            )
    # Blob-filtered clones previously caused repeated lazy fetches and corrupt
    # temporary packs. Keep the shallow checkout fully materialized and stop
    # background maintenance from racing task worktrees during production.
    git(["config", "maintenance.auto", "false"], repo)
    git(["config", "gc.auto", "0"], repo)
    commit = git(["rev-parse", "HEAD"], repo)
    if preflight_commit and commit != preflight_commit:
        raise RuntimeError(
            f"repository pin mismatch for {candidate['full_name']}: expected {preflight_commit}, got {commit}"
        )
    return repo, commit


def read_file(path: Path, limit: int = 20000) -> str:
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return data[:limit]


def repository_context(repo: Path, language: str) -> str:
    tracked = git(["ls-files"], repo, timeout=120).splitlines()
    extensions = {
        "typescript": (".ts", ".tsx"),
        "go": (".go",),
        "python": (".py",),
        "javascript": (".js", ".jsx", ".mjs", ".cjs"),
        "rust": (".rs",),
    }[language]

    def is_test(path: str) -> bool:
        lowered = path.lower()
        return (
            lowered.endswith("_test.go")
            or re.search(r"\.(?:test|spec)\.(?:ts|tsx|js|jsx|mjs|cjs)$", lowered) is not None
            or re.search(r"(?:^|/)(?:test_[^/]+|[^/]+_test)\.py$", lowered) is not None
            or "/tests/" in f"/{lowered}/"
        )

    source_files = [item for item in tracked if item.lower().endswith(extensions) and not is_test(item)]
    test_files = [item for item in tracked if item.lower().endswith(extensions) and is_test(item)]
    manifests = [
        item for item in tracked
        if any(token in item.lower() for token in ("readme", "package.json", "pyproject", "setup.py", "go.mod", "cargo.toml", "build.gradle", "pom.xml", "makefile"))
    ]
    files = "\n".join(source_files + test_files[:200] + manifests[:20])
    snippets: list[str] = []
    snippet_candidates = list(dict.fromkeys(manifests[:4] + source_files[:16] + test_files[:4]))
    for item in snippet_candidates:
        text = read_file(repo / item, 3500)
        if text:
            snippets.append(f"\n--- {item} ---\n{text}")
    log = git(["log", "-8", "--pretty=format:%h %s"], repo, timeout=120)
    return (
        f"LANGUAGE SOURCE AND TEST FILES:\n{files}\n\nRECENT COMMITS:\n{log}"
        f"\n\nREPRESENTATIVE FILE CONTENTS:\n{''.join(snippets)}"
    )[:80000]


def repository_source_paths(repo: Path, language: str) -> list[str]:
    extensions = {
        "typescript": (".ts", ".tsx"),
        "go": (".go",),
        "python": (".py",),
        "javascript": (".js", ".jsx", ".mjs", ".cjs"),
        "rust": (".rs",),
    }[language]
    test_patterns = {
        "typescript": r"\.(?:test|spec)\.(?:ts|tsx)$|(?:^|/)tests?/",
        "go": r"_test\.go$|(?:^|/)testdata/",
        "python": r"(?:^|/)(?:test_[^/]+|[^/]+_test)\.py$|(?:^|/)tests?/",
        "javascript": r"\.(?:test|spec)\.(?:js|jsx|mjs|cjs)$|(?:^|/)tests?/",
        "rust": r"(?:^|/)tests?/",
    }
    return [
        path
        for path in git(["ls-files"], repo, timeout=120).splitlines()
        if path.lower().endswith(extensions) and not re.search(test_patterns[language], path, re.I)
    ]


def repository_test_capacity(repo: Path, language: str) -> int:
    tracked = git(["ls-files"], repo, timeout=120).splitlines()
    path_patterns = {
        "typescript": r"\.(?:test|spec)\.(?:ts|tsx)$",
        "javascript": r"\.(?:test|spec)\.(?:js|jsx|mjs|cjs)$",
        "python": r"(?:^|/)(?:test_[^/]+|[^/]+_test)\.py$",
        "go": r"_test\.go$",
        "rust": r"\.rs$",
    }
    name_patterns = {
        "typescript": r"\b(?:it|test)\s*\(\s*['\"]",
        "javascript": r"\b(?:it|test)\s*\(\s*['\"]",
        "python": r"^\s*(?:async\s+)?def\s+test_[A-Za-z0-9_]+\s*\(",
        "go": r"^\s*func\s+Test[A-Za-z0-9_]+\s*\(",
        "rust": r"#\[(?:tokio::)?test\]\s*(?:async\s+)?fn\s+[A-Za-z0-9_]+",
    }
    total = 0
    for path in tracked:
        if not re.search(path_patterns[language], path, re.I):
            continue
        text = read_file(repo / path, 2_000_000)
        total += len(re.findall(name_patterns[language], text, re.M))
        if total >= 100:
            return total
    return total


def repository_complexity(repo: Path) -> dict[str, int]:
    tracked = git(["ls-files"], repo, timeout=120).splitlines()
    workspace_manifests = sum(
        Path(path).name.lower()
        in {"package.json", "pyproject.toml", "setup.py", "go.mod", "cargo.toml", "pom.xml", "build.gradle"}
        for path in tracked
    )
    return {"tracked_files": len(tracked), "workspace_manifests": workspace_manifests}


def is_lightweight_candidate(candidate: dict) -> bool:
    preflight = candidate.get("runtime_preflight") or {}
    capacity = candidate.get("test_capacity")
    size_kb = candidate.get("size_kb")
    tracked_files = candidate.get("tracked_files")
    workspace_manifests = candidate.get("workspace_manifests")
    return (
        capacity is not None
        and int(capacity) >= 100
        and preflight.get("status") == "passed"
        and size_kb is not None
        and int(size_kb) <= MAX_REPOSITORY_SIZE_KB
        and tracked_files is not None
        and int(tracked_files) <= MAX_TRACKED_FILES
        and workspace_manifests is not None
        and int(workspace_manifests) <= MAX_WORKSPACE_MANIFESTS
    )


def decode_opus_body(raw: bytes, content_type: str) -> dict:
    text = raw.decode("utf-8", "replace")
    if not text.strip():
        raise ValueError(f"empty HTTP response body: content_type={content_type!r}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        if "text/event-stream" not in content_type and not any(
            line.startswith("data:") for line in text.splitlines()
        ):
            raise ValueError(
                f"invalid JSON response: content_type={content_type!r}, bytes={len(raw)}"
            ) from exc

    message: dict = {"content": [], "usage": {}}
    text_parts: list[str] = []
    event_types: list[str] = []
    block_types: list[str] = []
    delta_types: list[str] = []
    tool_json_parts: dict[int, list[str]] = {}
    tool_inputs: dict[int, object] = {}
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line.removeprefix("data:").strip()
        if not payload or payload == "[DONE]":
            continue
        event = json.loads(payload)
        event_type = event.get("type")
        if event_type:
            event_types.append(event_type)
        if event_type == "error":
            error = event.get("error") or {}
            raise RuntimeError(
                f"Anthropic event stream error: {error.get('type') or error.get('code') or 'unknown'}: "
                f"{error.get('message', '')}"
            )
        if event_type == "message_start":
            message.update(event.get("message") or {})
        elif event_type == "content_block_start":
            block = event.get("content_block") or {}
            block_index = int(event.get("index") or 0)
            block_types.append(block.get("type", "unknown"))
            if block.get("type") == "text" and block.get("text"):
                text_parts.append(block["text"])
            elif block.get("type") == "tool_use":
                tool_json_parts.setdefault(block_index, [])
                if block.get("input") not in (None, {}):
                    tool_inputs[block_index] = block["input"]
        elif event_type == "content_block_delta":
            delta = event.get("delta") or {}
            block_index = int(event.get("index") or 0)
            delta_types.append(delta.get("type", "unknown"))
            if delta.get("type") == "text_delta":
                text_parts.append(delta.get("text", ""))
            elif delta.get("type") == "input_json_delta":
                tool_json_parts.setdefault(block_index, []).append(delta.get("partial_json", ""))
        elif event_type == "message_delta":
            message.update(event.get("delta") or {})
            message.setdefault("usage", {}).update(event.get("usage") or {})
    if "message_stop" not in event_types:
        raise ValueError(
            "incomplete Anthropic event stream: "
            f"events={sorted(set(event_types))}, bytes={len(raw)}"
        )
    if text_parts:
        message["content"] = [{"type": "text", "text": "".join(text_parts)}]
    elif tool_json_parts or tool_inputs:
        tool_values: list[object] = []
        for block_index in sorted(set(tool_json_parts) | set(tool_inputs)):
            partial_json = "".join(tool_json_parts.get(block_index, [])).strip()
            if partial_json:
                tool_values.append(json.loads(partial_json))
            elif block_index in tool_inputs:
                tool_values.append(tool_inputs[block_index])
        structured = tool_values[0] if len(tool_values) == 1 else tool_values
        message["content"] = [
            {"type": "text", "text": json.dumps(structured, ensure_ascii=False)}
        ]
    if not message.get("content"):
        raise ValueError(
            "Anthropic event stream returned no text content: "
            f"events={sorted(set(event_types))}, blocks={sorted(set(block_types))}, "
            f"deltas={sorted(set(delta_types))}, bytes={len(raw)}"
        )
    return message


def call_opus(api_url: str, api_key: str, prompt: str, timeout: int = 300, retries: int = 3) -> dict:
    payload = {
        "model": MODEL,
        "max_tokens": 8000,
        "thinking": {"type": "disabled"},
        "stream": True,
        "system": (
            "Return only the complete JSON object requested by the user. Never return analysis, a plan, "
            "progress text, a markdown fence, or an explanation. You cannot use tools or ask follow-up questions."
        ),
        "messages": [{"role": "user", "content": prompt}],
    }
    started = time.time()
    result: dict = {"status": "failed", "http": None, "usage": {}, "elapsed_seconds": None}
    for attempt in range(retries):
        request = urllib.request.Request(
            api_url,
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result["http"] = response.status
                body = decode_opus_body(response.read(), response.headers.get("Content-Type", ""))
                result.update({"status": "success", "body": body})
                break
        except urllib.error.HTTPError as exc:
            result.update({"http": exc.code, "error": exc.read().decode("utf-8", "replace")[:2000]})
        except Exception as exc:
            result["error"] = repr(exc)
        if attempt + 1 < retries:
            time.sleep(2 ** attempt)
    result["elapsed_seconds"] = round(time.time() - started, 3)
    body = result.get("body") or {}
    result["usage"] = body.get("usage", {})
    return result


def response_text(result: dict) -> str:
    return "".join(item.get("text", "") for item in (result.get("body") or {}).get("content", []) if item.get("type") == "text").strip()


def parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as original_error:
        decoder = json.JSONDecoder()
        value = None
        for match in re.finditer(r"\{", text):
            try:
                candidate, _ = decoder.raw_decode(text, match.start())
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                value = candidate
                break
        if value is None:
            raise original_error
    if not isinstance(value, dict):
        raise ValueError("author response must be a JSON object")
    return value


def slugify(value: str, fallback: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (value or fallback)[:44].rstrip("-")


def validate_design(design: dict) -> None:
    issue = design.get("issue", "")
    chain = design.get("pr_chain", [])
    files = design.get("affected_source_files", [])
    public_api = design.get("public_api_contract", [])
    card = design.get("difficulty_card") or {}
    missing = [key for key in ("title", "issue", "acceptance_criteria", "public_api_contract", "pr_chain", "affected_source_files", "module_count", "difficulty_card", "reference_implementation_plan") if key not in design]
    if missing:
        raise ValueError(f"design missing keys: {missing}")
    if not 1200 <= len(issue) <= 3600:
        raise ValueError(f"issue length outside 1200..3600: {len(issue)}")
    if not 3 <= len(chain) <= 5:
        raise ValueError(f"PR chain length outside 3..5: {len(chain)}")
    if len(files) < 7:
        raise ValueError(f"affected source files below 7: {len(files)}")
    if not isinstance(public_api, list) or len(public_api) < 3 or any(not str(item).strip() for item in public_api):
        raise ValueError("public API contract must contain at least 3 concrete entries")
    if int(design.get("module_count", 0)) < 3:
        raise ValueError("module count below 3")
    if float(card.get("difficulty_score", 0)) < 1.0:
        raise ValueError("difficulty score below 1.0")
    if int(card.get("estimated_changed_lines", 0)) < 600:
        raise ValueError("estimated changed lines below 600")


def validate_design_against_repo(design: dict, repo: Path) -> None:
    files = [str(item) for item in design.get("affected_source_files", [])]
    invalid = [
        path for path in files
        if Path(path).is_absolute()
        or ".." in Path(path).parts
        or re.search(r"(^|/)(tests?|testdata)(/|$)|\.(md|yml|yaml|json)$", path, re.I)
    ]
    if invalid:
        raise ValueError(f"affected_source_files contains non-source paths: {invalid}")
    existing = [path for path in files if (repo / path).is_file()]
    # Keep the benchmark grounded in the pinned checkout. New files can make
    # a legitimate feature task harder to validate and often indicate that
    # the author model invented a package path, so require every declared
    # affected source file to exist at the base commit.
    required_existing = len(files)
    if len(existing) < required_existing:
        raise ValueError(
            f"only {len(existing)}/{len(files)} affected files exist at the base commit; "
            f"requires {required_existing}"
        )
    declared = set(files)
    for index, stage in enumerate(design.get("pr_chain", []), 1):
        stage_files = {str(path) for path in stage.get("files", []) if str(path)}
        if not stage_files:
            raise ValueError(f"PR-chain stage {index} declares no files")
        outside = sorted(stage_files - declared)
        if outside:
            raise ValueError(f"PR-chain stage {index} references undeclared files: {outside}")


def author_prompt(
    candidate: dict,
    commit: str,
    context: str,
    slot: str,
    language: str,
    existing_titles: list[str],
) -> str:
    prior = "\n".join(f"- {title}" for title in existing_titles) or "- none"
    return f"""You are the strong task-authoring agent for an ORIGINAL DeepSWE/Harbor coding benchmark.
Design one difficult, implementable feature task for slot {slot}. The target language is {language}.
Repository: {candidate['html_url']}
Pinned base commit: {commit}
Stars: {candidate.get('stars')}; license: {candidate.get('license')}; latest pushed_at: {candidate.get('pushed_at')}
Verified Linux/offline test preflight: {json.dumps(candidate.get('runtime_preflight', {}), ensure_ascii=False)}

You have no tools, shell, filesystem access, or follow-up turns. Do not announce exploration, request
more files, or describe what you plan to inspect. Use only the repository evidence included in this
message and return the complete final JSON in this response.

Read the repository evidence below. Do not ask questions or offer scope choices; make all authoring
decisions yourself and return the complete JSON in this response. Do not reuse a known benchmark issue and do not invent APIs that
are absent from the repository. Choose a real user-facing feature or cross-cutting maintenance change. Every affected_source_files
path must be copied verbatim from the LANGUAGE SOURCE AND TEST FILES list above and must already exist at the pinned base commit;
do not propose new packages, directories, or source files. The task must require exploration, 7-9 source files, at least 3 modules/packages, and roughly 600-900
changed lines in a reference implementation. It must have non-trivial regression risk and enough public
behavior to support 30-150 feature tests and 100-1500 property/regression tests later.

Return ONLY JSON with these keys:
title, task_id_slug, issue (1200-3600 characters), acceptance_criteria (array),
public_api_contract (array with exact public symbols/signatures, configuration keys, CLI flags,
event payloads, or observable entry points that independent tests may rely on),
pr_chain (array of 3-5 stages; each has stage, depends_on, modules, files, behavior),
affected_source_files (array of 7-9 repository-relative paths),
module_count (integer), feature_test_plan (array), regression_test_plan (array),
difficulty_card (object with difficulty_score >= 1.0, exploration_points, cross_module_points,
integration_points, regression_risk_points, estimated_changed_lines, estimated_changed_files),
reference_implementation_plan (array of concrete edits), build_and_test_commands (array).
The issue must state behavior and compatibility requirements, but must not reveal hidden test code or
an answer patch. Every new symbol a test may import must be named with its exact signature in
public_api_contract. Keep paths and commands grounded in the evidence.

Tasks already authored for this repository are listed below. Your task must be materially different in
feature surface, affected modules, acceptance criteria, and implementation approach. A renamed or lightly
varied version of any listed task is invalid.

EXISTING TASK TITLES:
{prior}

REPOSITORY EVIDENCE:
{context}
"""


def path_repair_prompt(design: dict, validation_error: Exception, source_paths: list[str]) -> str:
    return f"""You are repairing repository paths in an otherwise complete DeepSWE task design.
You have no tools, shell, filesystem access, or follow-up turns. Return ONLY the complete repaired JSON.

Preserve the feature exactly: do not change title, issue behavior, acceptance criteria, public API contract,
feature or regression test plans, PR-stage behavior, difficulty, or build commands. Only repair
affected_source_files and the synchronized path/module references in pr_chain, reference_implementation_plan,
and difficulty_card.estimated_changed_files. Keep 7-9 affected source files across at least 3 real
modules/packages. Every affected path MUST be copied verbatim from REAL LANGUAGE SOURCE PATHS below and
must already exist at the pinned commit. Do not create any new file or directory. Do not use
tests, testdata, documentation, manifests, generated files, or paths outside the repository. Every
pr_chain[].files entry must also appear in affected_source_files, every PR stage must declare files, and
affected_source_files must equal the deduplicated union of all pr_chain[].files entries. Before returning,
check this invariant for every stage and remove or replace any stale undeclared path left by the repair.

STRICT VALIDATION ERROR:
{validation_error}

COMPLETE ORIGINAL DESIGN:
{json.dumps(design, ensure_ascii=False, indent=2)}

REAL LANGUAGE SOURCE PATHS AT THE PINNED COMMIT:
{chr(10).join(source_paths)}
"""


def result_usage(results: list[dict]) -> dict:
    return {
        "input": sum((result.get("usage") or {}).get("input_tokens", 0) for result in results),
        "cache": sum((result.get("usage") or {}).get("cache_read_input_tokens", 0) for result in results),
        "output": sum((result.get("usage") or {}).get("output_tokens", 0) for result in results),
    }


def parse_author_response(result: dict, label: str) -> dict:
    if result.get("status") != "success":
        raise RuntimeError(result.get("error", f"HTTP {result.get('http')}"))
    model_text = response_text(result)
    if not model_text:
        raise RuntimeError(f"{label} model returned an empty response")
    try:
        return parse_json(model_text)
    except json.JSONDecodeError as exc:
        preview = re.sub(r"\s+", " ", model_text[:300]).strip()
        raise ValueError(
            f"{label} model returned non-JSON text ({len(model_text)} chars): {preview!r}"
        ) from exc


def is_repository_failure(error: Exception) -> bool:
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "unable to clone",
            "cannot materialize preflight commit",
            "cannot checkout preflight commit",
            "repository pin mismatch",
        )
    )


def process_one(
    root: Path,
    row: dict,
    candidate: dict,
    api_url: str,
    api_key: str,
    existing_titles: list[str],
    reuse_response: bool = False,
) -> tuple[str, dict, dict]:
    slot = row["slot"]
    task_root = root / "tasks" / slot
    results: list[dict] = []
    repair_attempted = False
    try:
        repo, commit = checkout_repo(root, candidate)
        capacity = repository_test_capacity(repo, row["language"])
        if capacity < 100:
            raise ValueError(
                f"repository exposes fewer than 100 statically reportable tests for {row['language']}: {capacity}"
            )
        context = repository_context(repo, row["language"])
        response_dir = root / "workspaces" / "author-responses" / slot
        response_dir.mkdir(parents=True, exist_ok=True)
        response_path = response_dir / "author-response.txt"
        if reuse_response and response_path.is_file():
            result = {
                "status": "success",
                "http": None,
                "usage": {},
                "elapsed_seconds": 0,
                "body": {"content": [{"type": "text", "text": response_path.read_text(encoding="utf-8")}]},
            }
        else:
            result = call_opus(
                api_url,
                api_key,
                author_prompt(candidate, commit, context, slot, row["language"], existing_titles),
            )
            response_path.write_text(response_text(result), encoding="utf-8")
        results.append(result)
        event = {
            "timestamp": now(), "slot": slot, "stage": "author_issue_pr_chain",
            "provider": provider_name(api_url), "model": MODEL, "status": result.get("status"),
            "http": result.get("http"), "usage": result_usage(results),
            "elapsed_seconds": result.get("elapsed_seconds"), "api_key_stored": False,
        }
        if reuse_response and response_path.is_file():
            event["response_reused"] = True
        design = parse_author_response(result, "author")
        validate_design(design)
        try:
            validate_design_against_repo(design, repo)
        except ValueError as validation_error:
            repair_attempted = True
            repair_result = call_opus(
                api_url,
                api_key,
                path_repair_prompt(
                    design,
                    validation_error,
                    repository_source_paths(repo, row["language"]),
                ),
            )
            results.append(repair_result)
            (response_dir / "path-repair-response.txt").write_text(
                response_text(repair_result), encoding="utf-8"
            )
            design = parse_author_response(repair_result, "path-repair")
            validate_design(design)
            validate_design_against_repo(design, repo)
            event.update({
                "status": repair_result.get("status"),
                "http": repair_result.get("http"),
                "usage": result_usage(results),
                "elapsed_seconds": round(sum(result.get("elapsed_seconds") or 0 for result in results), 3),
                "path_repair_attempted": True,
            })
        design["repository"] = {"url": candidate["html_url"], "full_name": candidate["full_name"], "language": row["language"], "base_commit": commit, "stars": candidate.get("stars"), "license": candidate.get("license"), "runtime_preflight": candidate.get("runtime_preflight", {})}
        design["slot"] = slot
        design["author_model"] = MODEL
        design["pipeline_version"] = "2.0"
        slot_number = slot.rsplit("-", 1)[-1]
        design["task_id_slug"] = f"{slugify(design.get('title', ''), 'deep-swe-task')}-{slot_number}"
        (task_root / "authoring").mkdir(parents=True, exist_ok=True)
        (task_root / "authoring" / "issue-design.json").write_text(json.dumps(design, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        row.update({"status": "in_progress", "stage": "reference_implementation", "repository": candidate["full_name"], "base_commit_hash": commit, "task_id": design["task_id_slug"], "author_model": MODEL, "artifacts": ["authoring/issue-design.json"], "usage": {"author_issue_pr_chain": event["usage"]}, "errors": []})
        event["design_path"] = str(task_root / "authoring/issue-design.json")
        return slot, row, event
    except Exception as exc:
        update = {
            "status": "failed",
            "stage": "author_issue_pr_chain",
            "repository": candidate.get("full_name"),
            "errors": [repr(exc)],
        }
        if is_repository_failure(exc):
            excluded = set(str(item) for item in row.get("excluded_repositories", []))
            excluded.add(str(candidate.get("full_name") or ""))
            update["excluded_repositories"] = sorted(item for item in excluded if item)
        row.update(update)
        event = {
            "timestamp": now(), "slot": slot, "stage": "author_issue_pr_chain",
            "provider": provider_name(api_url), "model": MODEL, "status": "failed",
            "http": results[-1].get("http") if results else None,
            "usage": result_usage(results),
            "elapsed_seconds": round(sum(result.get("elapsed_seconds") or 0 for result in results), 3),
            "path_repair_attempted": repair_attempted,
            "error": repr(exc), "api_key_stored": False,
        }
        return slot, row, event


def main() -> None:
    global MODEL
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--slot", help="author one explicit manifest slot")
    parser.add_argument("--repository", help="use one explicit audited repository")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument(
        "--reuse-response",
        action="store_true",
        help="reuse an existing saved author response for the selected slot",
    )
    args = parser.parse_args()
    env = {}
    for line in args.env_file.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip("\"'")
    api_key = env.get("ANTHROPIC_API_KEY", "")
    MODEL = env.get("ANTHROPIC_MODEL", MODEL)
    base_url = env.get("ANTHROPIC_BASE_URL", DEFAULT_API).rstrip("/")
    api_url = base_url if base_url.endswith("/v1/messages") else base_url + ("/messages" if base_url.endswith("/v1") else "/v1/messages")
    if not api_key:
        raise SystemExit("env file must define ANTHROPIC_API_KEY")
    manifest_path = args.root / "registry/task_manifest.jsonl"
    rows = load_jsonl(manifest_path)
    if not rows:
        raise SystemExit(f"manifest is empty: {manifest_path}")
    candidates = load_jsonl(args.root / "registry/repository-candidates.jsonl")
    if args.repository and not any(candidate.get("full_name") == args.repository for candidate in candidates):
        raise SystemExit(f"unknown repository candidate: {args.repository}")
    by_language: dict[str, list[dict]] = {language: [] for language in LANGUAGES}
    for candidate in candidates:
        explicit_override = bool(args.repository and candidate.get("full_name") == args.repository)
        explicit_ready = explicit_override and (candidate.get("runtime_preflight") or {}).get("status") == "passed" and int(candidate.get("test_capacity") or 0) >= 100
        if (
            candidate.get("full_name")
            and candidate.get("language") in by_language
            and (is_lightweight_candidate(candidate) or explicit_ready)
        ):
            by_language[candidate["language"]].append(candidate)
    titles_by_repository: dict[str, list[str]] = {}
    assigned_by_repository: dict[str, int] = {}
    for current in rows:
        repository = current.get("repository")
        if repository:
            assigned_by_repository[repository] = assigned_by_repository.get(repository, 0) + 1
        design_path = args.root / "tasks" / current["slot"] / "authoring" / "issue-design.json"
        if repository and design_path.is_file():
            try:
                title = str(json.loads(design_path.read_text(encoding="utf-8")).get("title", "")).strip()
            except (OSError, json.JSONDecodeError):
                title = ""
            if title:
                titles_by_repository.setdefault(repository, []).append(title)
        for discarded in current.get("discarded_attempts", []):
            discarded_repository = discarded.get("repository")
            archive = discarded.get("archive")
            if not discarded_repository or not archive:
                continue
            discarded_design = args.root / archive / "authoring" / "issue-design.json"
            if not discarded_design.is_file():
                continue
            try:
                discarded_title = str(
                    json.loads(discarded_design.read_text(encoding="utf-8")).get("title", "")
                ).strip()
            except (OSError, json.JSONDecodeError):
                discarded_title = ""
            if discarded_title:
                titles_by_repository.setdefault(discarded_repository, []).append(discarded_title)

    selected = []
    repositories_in_batch: set[str] = set()
    for row in rows:
        if len(selected) >= args.limit:
            break
        if args.slot and row.get("slot") != args.slot:
            continue
        if row.get("stage") == "repository_discovery":
            pass
        elif args.retry_failed and row.get("stage") == "author_issue_pr_chain" and row.get("status") == "failed":
            pass
        else:
            continue
        pool = by_language.get(row["language"], [])
        if args.repository:
            pool = [candidate for candidate in pool if candidate.get("full_name") == args.repository]
        if not pool:
            row.update({"status": "failed", "errors": [f"no candidate repository for {row['language']}"]})
            continue
        excluded = {str(item) for item in row.get("excluded_repositories", [])}
        available = [
            candidate
            for candidate in pool
            if candidate["full_name"] not in repositories_in_batch
            and candidate["full_name"] not in excluded
        ]
        if not available:
            continue
        candidate = min(
            available,
            key=lambda item: (
                assigned_by_repository.get(item["full_name"], 0),
                int(item.get("size_kb") or 0),
                int(item.get("tracked_files") or 0),
                item["full_name"],
            ),
        )
        repository = candidate["full_name"]
        repositories_in_batch.add(repository)
        assigned_by_repository[repository] = assigned_by_repository.get(repository, 0) + 1
        selected.append((row, candidate, list(titles_by_repository.get(repository, []))))
    if not selected:
        atomic_jsonl(manifest_path, rows)
        print(json.dumps({"selected": 0, "message": "no pending slots"}, ensure_ascii=False))
        return
    event_path = args.root / "registry/production-events.jsonl"
    results: list[tuple[str, dict, dict]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [
            executor.submit(
                process_one,
                args.root,
                row,
                candidate,
                api_url,
                api_key,
                titles,
                args.reuse_response,
            )
            for row, candidate, titles in selected
        ]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            print(json.dumps({"slot": result[0], "status": result[1]["status"], "stage": result[1]["stage"]}, ensure_ascii=False), flush=True)
            append_jsonl(event_path, result[2])
            atomic_jsonl(manifest_path, rows)
    by_slot = {slot: row for slot, row, _ in results}
    for row in rows:
        if row["slot"] in by_slot:
            row.update(by_slot[row["slot"]])
    atomic_jsonl(manifest_path, rows)
    print(json.dumps({"selected": len(selected), "completed_designs": sum(r[1]["status"] == "in_progress" for r in results), "failed": sum(r[1]["status"] == "failed" for r in results)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
