#!/usr/bin/env python3
"""Produce V2 reference implementations through Anthropic Messages."""

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

MODEL = "claude-opus-4-8"
DEFAULT_API = "https://www.packyapi.com/v1/messages"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def atomic_jsonl(path: Path, rows: list[dict]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def provider_name(api_url: str) -> str:
    return urllib.parse.urlparse(api_url).netloc or "anthropic-compatible"


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 900) -> str:
    result = subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True, timeout=timeout)
    # Preserve the trailing newline on patch output. `git apply` treats a
    # diff without its final newline as a truncated/corrupt patch.
    return result.stdout


def read(path: Path, limit: int = 12000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def decode_opus_body(raw: bytes, content_type: str) -> dict:
    value = raw.decode("utf-8", "replace")
    if not value.strip():
        raise ValueError(f"empty HTTP response body: content_type={content_type!r}")
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        if "text/event-stream" not in content_type and not any(
            line.startswith("data:") for line in value.splitlines()
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
    for line in value.splitlines():
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
        structured = next(
            (
                value for value in tool_values
                if isinstance(value, dict) and isinstance(value.get("operations"), list)
            ),
            tool_values[0] if len(tool_values) == 1 else tool_values,
        )
        message["content"] = [
            {"type": "text", "text": json.dumps(structured, ensure_ascii=False)}
        ]
    if not message.get("content"):
        raise ValueError(
            "Anthropic event stream returned no text content: "
            f"events={sorted(set(event_types))}, blocks={sorted(set(block_types))}, "
            f"deltas={sorted(set(delta_types))}, stop_reason={message.get('stop_reason')!r}, bytes={len(raw)}"
        )
    return message


def call_opus(
    api_url: str,
    key: str,
    prompt: str,
    timeout: int = 720,
    retries: int = 2,
    previous_response: str = "",
) -> dict:
    messages = [{"role": "user", "content": prompt}]
    if previous_response:
        messages.extend([
            {"role": "assistant", "content": previous_response},
            {
                "role": "user",
                "content": (
                    "Your previous response did not contain the requested JSON edit operations. "
                    "You have already completed the reasoning and have no tools to call. Return the complete "
                    "JSON operation object now, with no plan, progress text, markdown fence, or explanation."
                ),
            },
        ])
    payload = {
        "model": MODEL,
        "max_tokens": 12000,
        "stream": True,
        "system": "Return only the JSON edit operations requested by the user. Never return a plan or progress message. Implement from the supplied excerpts.",
        "messages": messages,
    }
    started = time.time()
    result = {"status": "failed", "http": None, "usage": {}, "elapsed_seconds": None}
    for attempt in range(retries):
        request = urllib.request.Request(api_url, data=json.dumps(payload, ensure_ascii=False).encode(), headers={"x-api-key": key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result.update({
                    "status": "success",
                    "http": response.status,
                    "body": decode_opus_body(response.read(), response.headers.get("Content-Type", "")),
                })
                break
        except urllib.error.HTTPError as exc:
            result.update({"http": exc.code, "error": exc.read().decode("utf-8", "replace")[:2000]})
        except Exception as exc:
            result["error"] = repr(exc)
        if attempt + 1 < retries:
            time.sleep(2 ** attempt)
    result["elapsed_seconds"] = round(time.time() - started, 3)
    result["usage"] = (result.get("body") or {}).get("usage", {})
    return result


def text(result: dict) -> str:
    parts: list[str] = []
    for item in (result.get("body") or {}).get("content", []):
        if item.get("type") == "text":
            parts.append(item.get("text", ""))
        elif item.get("type") == "tool_use" and item.get("input") is not None:
            parts.append(json.dumps(item["input"], ensure_ascii=False))
    return "".join(parts).strip()


def slugify(value: str, fallback: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (value or fallback)[:44].rstrip("-")


def instruction_text(design: dict) -> str:
    issue = design["issue"].strip()
    public_api = design.get("public_api_contract") or []
    contract = ""
    if public_api:
        contract = "\n\nPublic API contract:\n" + "\n".join(f"- {x}" for x in public_api)
    acceptance = "\n\nAcceptance criteria:\n" + "\n".join(f"- {x}" for x in design["acceptance_criteria"])
    suffix = contract + acceptance
    if len(issue) + len(suffix) <= 5000:
        return issue + suffix + "\n"
    budget = max(1200, 5000 - len(suffix) - 1)
    return issue[:budget].rstrip() + "..." + suffix + "\n"


def parse_operations(
    value: str,
    minimum: int = 7,
    target_files: list[str] | None = None,
) -> list[dict]:
    value = value.strip()
    value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
    value = re.sub(r"\s*```$", "", value)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as original_error:
        decoder = json.JSONDecoder()
        parsed = None
        for match in re.finditer(r"[\{\[]", value):
            try:
                candidate, _ = decoder.raw_decode(value, match.start())
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, list) or (
                isinstance(candidate, dict) and isinstance(candidate.get("operations"), list)
            ):
                parsed = candidate
                break
        if parsed is None:
            raise ValueError("model response did not contain JSON edit operations") from original_error
    def extract_operations(item: object) -> list[dict]:
        if isinstance(item, list):
            return [operation for value in item for operation in extract_operations(value)]
        if not isinstance(item, dict):
            return []
        if item.get("path"):
            return [item]
        if item.get("file_path") and "content" in item:
            return [{
                "path": item["file_path"],
                "mode": item.get("mode", "create"),
                "content": item["content"],
            }]
        for key in ("operations", "operation", "edits", "files", "input"):
            if key in item:
                nested = extract_operations(item[key])
                if nested:
                    return nested
        return []

    operations = extract_operations(parsed)
    if not operations:
        raise ValueError("edit response must contain operations array")
    if target_files:
        targets_by_name = {Path(path).name: path for path in target_files}
        for operation in operations:
            operation_path = str(operation.get("path", ""))
            if operation_path not in target_files:
                target = targets_by_name.get(Path(operation_path).name)
                if target:
                    operation["path"] = target
    if len(operations) < minimum:
        raise ValueError(f"edit operations below {minimum}: {len(operations)}")
    return operations


def repair_operation(api_url: str, key: str, design: dict, path: str, current: str, operation: dict, error: str) -> dict:
    repair_prompt = f"""Return ONLY JSON for one source-file edit operation: {{\"path\":\"{path}\",\"mode\":\"replace\",\"old\":\"exact text\",\"new\":\"replacement\"}}. The previous operation failed because: {error}. The file currently contains the exact text below. Preserve all unrelated content and implement the requested feature. Do not use a markdown fence or explanation.\n\nFeature issue:\n{design['issue']}\n\nPrevious operation:\n{json.dumps(operation, ensure_ascii=False)}\n\nCURRENT FILE {path}:\n{current}\n"""
    result = call_opus(api_url, key, repair_prompt, timeout=300)
    if result.get("status") != "success":
        raise RuntimeError(result.get("error", "repair request failed"))
    value = result.get("body") or {}
    output = "".join(x.get("text", "") for x in value.get("content", []) if x.get("type") == "text")
    output = re.sub(r"^```(?:json)?\s*", "", output.strip(), flags=re.I)
    output = re.sub(r"\s*```$", "", output)
    parsed = json.loads(output)
    if isinstance(parsed, dict) and "operation" in parsed:
        parsed = parsed["operation"]
    if not isinstance(parsed, dict):
        raise ValueError("repair response was not an operation object")
    return parsed


def context(repo: Path, design: dict) -> str:
    files = design.get("affected_source_files", [])
    snippets = []
    for item in files:
        value = read(repo / item, 30000)
        snippets.append(f"\n--- {item} ---\n{value if value else '[new or missing file]'}")
    return "\n".join(snippets)[:110000]


def prompt(
    design: dict,
    repo: Path,
    prior_patch: str = "",
    prior_error: str = "",
    target_files: list[str] | None = None,
    group_number: int = 1,
    group_count: int = 1,
) -> str:
    repair = ""
    if prior_patch:
        prior_paths = re.findall(r"^diff --git a/(.*?) b/", prior_patch, re.M)
        declared_paths = [str(path) for path in design.get("affected_source_files", [])]
        covered_paths = sorted(set(prior_paths).intersection(declared_paths))
        missing_paths = sorted(set(declared_paths) - set(prior_paths))
        required_coverage = max(7, (3 * len(declared_paths) + 3) // 4)
        repair = f"""

Previous rejected implementation patch:
{prior_patch[:60000]}

Previous rejection reason:
{prior_error or 'The implementation did not satisfy a production gate.'}

Previous declared-path coverage: {len(covered_paths)}/{len(declared_paths)}; required: {required_coverage}.
Previously missing declared paths: {json.dumps(missing_paths, ensure_ascii=False)}

Return a complete replacement operation set, preserving valid work from the rejected patch while implementing the missing behavior. Do not return an incremental patch against the rejected patch.
"""
    target_files = target_files or [str(path) for path in design.get("affected_source_files", [])]
    grouped = group_count > 1
    group_instruction = ""
    if grouped:
        group_instruction = f"""

This is implementation group {group_number} of {group_count}. Other calls implement the remaining
files. Return operations for EVERY file in TARGET FILES and for NO other file. Treat excerpts from
already-completed groups as authoritative integration context. The final combined patch, not this group
alone, must satisfy the complete issue. Keep this group focused and complete so it composes cleanly.
"""
    return f"""You are a deterministic patch generator. You do not have tools and cannot explore the repository. All relevant source excerpts are included below. Implement the following ORIGINAL feature in the pinned repository and output the requested part of the Oracle reference patch.

Issue:
{design['issue']}

Acceptance criteria:
{json.dumps(design['acceptance_criteria'], ensure_ascii=False)}

Public API contract:
{json.dumps(design.get('public_api_contract', []), ensure_ascii=False)}

PR chain:
{json.dumps(design['pr_chain'], ensure_ascii=False)}

Concrete implementation plan:
{json.dumps(design['reference_implementation_plan'], ensure_ascii=False)}

Affected files:
{json.dumps(design['affected_source_files'], ensure_ascii=False)}

TARGET FILES FOR THIS RESPONSE:
{json.dumps(target_files, ensure_ascii=False)}
{group_instruction}

Repository excerpts:
{context(repo, design)}
{repair}

Return ONLY JSON: {{"operations":[{{"path":"repository-relative/path","mode":"replace","old":"exact existing text","new":"replacement text"}}]}}. Use mode `create` with `content` for new files. Do not write a plan, progress update, apology, or explanation. Do not say that you need to inspect files. Preserve backward compatibility and implement every behavior assigned to the target files. Return at least one operation for every target file and never edit a path outside TARGET FILES. The final combined patch must contain 500-1800 changed lines across 7-16 source files; do not pad with comments or unrelated refactors. Do not omit handlers, adapters, persistence paths, or public exports named in the issue or PR chain. Each replace operation's old text must be copied exactly from the supplied excerpts. Do not modify CI or hidden verifier files.
Keep every `old` value to the smallest unique exact anchor needed for the edit (normally no more than 40 lines), and never repeat an entire existing source file."""


def operation_groups(repo: Path, design: dict) -> list[list[str]]:
    files = [str(path) for path in design.get("affected_source_files", [])]
    new_files = [path for path in files if not (repo / path).is_file()]
    existing_files = [path for path in files if (repo / path).is_file()]
    # A one-file create request is prone to placeholder responses from the
    # provider. Pair each new file with one existing source file so the model
    # must produce a concrete, integrated edit set for both targets.
    groups: list[list[str]] = []
    existing_index = 0
    for new_file in new_files:
        group = [new_file]
        if existing_index < len(existing_files):
            group.append(existing_files[existing_index])
            existing_index += 1
        groups.append(group)
    groups.extend(
        existing_files[index:index + 2]
        for index in range(existing_index, len(existing_files), 2)
    )
    groups = [group for group in groups if group]
    # Keep each provider request scoped to one coherent group.  A model may
    # otherwise append files from a later group when the final target list is
    # long, which is rejected as an out-of-scope edit.  Smaller groups also
    # make retries deterministic and reduce response truncation.
    return groups


def generate_operations(
    api_url: str,
    key: str,
    design: dict,
    worktree: Path,
    prior_patch: str,
    prior_error: str,
) -> tuple[list[dict], dict]:
    groups = operation_groups(worktree, design)
    all_operations: list[dict] = []
    checkpoint_dir = worktree.parent.parent / "reference-groups" / worktree.name
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    aggregate = {
        "status": "success",
        "http": 200,
        "usage": {"input": 0, "cache": 0, "output": 0},
        "elapsed_seconds": 0.0,
        "response_preview": "",
        "operation_groups": groups,
    }
    for group_number, target_files in enumerate(groups, 1):
        checkpoint = checkpoint_dir / f"group-{group_number}.json"
        repairing = bool(prior_patch or prior_error)
        repair_checkpoint = checkpoint_dir / f"group-{group_number}.repair-complete"
        reusable_checkpoint = not repairing or repair_checkpoint.is_file()
        if reusable_checkpoint and checkpoint.is_file():
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
            if saved.get("target_files") == target_files:
                saved_operations = saved.get("operations")
                if isinstance(saved_operations, list):
                    all_operations.extend(saved_operations)
                    continue
        raw_response = checkpoint_dir / f"group-{group_number}-response.txt"
        if reusable_checkpoint and raw_response.is_file():
            try:
                saved_operations = parse_operations(
                    raw_response.read_text(encoding="utf-8"),
                    minimum=len(target_files),
                    target_files=target_files,
                )
            except ValueError:
                pass
            else:
                operation_paths = {
                    str(operation.get("path", "")) for operation in saved_operations
                }
                if operation_paths == set(target_files):
                    all_operations.extend(saved_operations)
                    checkpoint.write_text(
                        json.dumps(
                            {"target_files": target_files, "operations": saved_operations},
                            ensure_ascii=False,
                            indent=2,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    continue
        group_prompt = prompt(
            design,
            worktree,
            prior_patch,
            prior_error,
            target_files,
            group_number,
            len(groups),
        )
        if all_operations:
            group_prompt += (
                "\n\nCOMPLETED OPERATIONS FROM EARLIER GROUPS (integration context only; do not repeat them):\n"
                + json.dumps(all_operations, ensure_ascii=False)
            )
        result = call_opus(api_url, key, group_prompt)
        if result.get("status") != "success":
            raise RuntimeError(result.get("error", f"HTTP {result.get('http')}"))
        model_text = text(result)
        raw_response = checkpoint_dir / f"group-{group_number}-response.txt"
        raw_response.write_text(model_text, encoding="utf-8")
        try:
            group_operations = parse_operations(
                model_text,
                minimum=len(target_files),
                target_files=target_files,
            )
        except ValueError as exc:
            retry_prompt = (
                group_prompt
                + "\n\nYour previous response was invalid or truncated: "
                + str(exc)
                + ". Return the complete compact JSON object now."
            )
            retry = call_opus(api_url, key, retry_prompt)
            if retry.get("status") != "success":
                raise RuntimeError(retry.get("error", f"HTTP {retry.get('http')}"))
            result_usage = retry.get("usage") or {}
            aggregate["usage"]["input"] += int(result_usage.get("input_tokens") or 0)
            aggregate["usage"]["cache"] += int(result_usage.get("cache_read_input_tokens") or 0)
            aggregate["usage"]["output"] += int(result_usage.get("output_tokens") or 0)
            aggregate["elapsed_seconds"] += float(retry.get("elapsed_seconds") or 0)
            model_text = text(retry)
            (checkpoint_dir / f"group-{group_number}-retry-response.txt").write_text(
                model_text,
                encoding="utf-8",
            )
            group_operations = parse_operations(
                model_text,
                minimum=len(target_files),
                target_files=target_files,
            )
        result_usage = result.get("usage") or {}
        aggregate["usage"]["input"] += int(result_usage.get("input_tokens") or 0)
        aggregate["usage"]["cache"] += int(result_usage.get("cache_read_input_tokens") or 0)
        aggregate["usage"]["output"] += int(result_usage.get("output_tokens") or 0)
        aggregate["elapsed_seconds"] += float(result.get("elapsed_seconds") or 0)
        aggregate["http"] = result.get("http")
        aggregate["response_preview"] += f"group {group_number}: {model_text[:600]}\n"
        operation_paths = {str(operation.get("path", "")) for operation in group_operations}
        outside = sorted(operation_paths - set(target_files))
        missing = sorted(set(target_files) - operation_paths)
        if outside and not missing:
            # A long response can spill one or more edits from the next group
            # into the current JSON object.  Since every required target in
            # this group is present, discard only those out-of-scope extras;
            # the next group will generate its own edits.  Never do this when
            # a target is missing, because that would hide an incomplete
            # implementation.
            group_operations = [
                operation for operation in group_operations
                if str(operation.get("path", "")) in set(target_files)
            ]
            operation_paths = {str(operation.get("path", "")) for operation in group_operations}
            outside = sorted(operation_paths - set(target_files))
        if outside or missing:
            shapes = [
                sorted(operation) if isinstance(operation, dict) else type(operation).__name__
                for operation in group_operations
            ]
            raise ValueError(
                f"operation group {group_number} path mismatch: outside={outside}, missing={missing}, "
                f"shapes={shapes}"
            )
        all_operations.extend(group_operations)
        checkpoint.write_text(
            json.dumps(
                {"target_files": target_files, "operations": group_operations},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if repairing:
            repair_checkpoint.write_text("complete\n", encoding="utf-8")
    aggregate["elapsed_seconds"] = round(aggregate["elapsed_seconds"], 3)
    return all_operations, aggregate


def task_toml(task_id: str, design: dict, language: str, repository: str, commit: str) -> str:
    return f'''schema_version = "1.3"
artifacts = ["/logs/artifacts/model.patch"]

[task]
name = "{task_id}"
description = "{design['title'].replace(chr(34), '')}"

[metadata]
task_id = "{task_id}"
language = "{language}"
repository = "{repository}"
repository_url = "https://github.com/{repository}.git"
base_commit_hash = "{commit}"
category = "feature_request"

[verifier]
network_mode = "no-network"
environment_mode = "separate"
timeout_sec = 1800.0

[agent]
network_mode = "no-network"
timeout_sec = 5400.0

[environment]
build_timeout_sec = 1800.0
cpus = 2
memory_mb = 8192
storage_mb = 20480
gpus = 0
'''


def process(
    root: Path,
    row: dict,
    api_url: str,
    key: str,
    repair_feedback: str = "",
    reuse_existing_patch: bool = False,
) -> tuple[dict, dict]:
    slot = row["slot"]
    package = root / "tasks" / slot
    design_path = package / "authoring" / "issue-design.json"
    event = {"timestamp": now(), "slot": slot, "stage": "reference_implementation", "provider": provider_name(api_url), "model": MODEL, "api_key_stored": False}
    try:
        design = json.loads(design_path.read_text(encoding="utf-8"))
        repo = root / "workspaces" / "repositories" / row["repository"].replace("/", "__")
        worktree = root / "workspaces" / "reference" / slot
        if worktree.exists():
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=repo,
                check=False,
                capture_output=True,
                text=True,
            )
            if worktree.exists():
                shutil.rmtree(worktree)
        worktree.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "worktree", "prune"], repo)
        run(["git", "worktree", "add", "--detach", str(worktree), row["base_commit_hash"]], repo)
        prior_patch_path = package / "solution" / "solution.patch"
        prior_patch = read(prior_patch_path, 60000) if prior_patch_path.is_file() else ""
        prior_errors = [str(item) for item in row.get("errors", [])]
        if repair_feedback:
            prior_errors.append(repair_feedback.strip())
        prior_error = "\n\n".join(item for item in prior_errors if item)
        if reuse_existing_patch:
            if not prior_patch_path.is_file():
                raise FileNotFoundError("cannot reuse a missing solution.patch")
            run(["git", "apply", "--index", "--whitespace=nowarn", str(prior_patch_path)], worktree)
            existing_usage = (row.get("usage") or {}).get("reference_implementation") or {}
            event.update({
                "status": "success",
                "http": None,
                "usage": {
                    "input": int(existing_usage.get("input") or 0),
                    "cache": int(existing_usage.get("cache") or 0),
                    "output": int(existing_usage.get("output") or 0),
                },
                "elapsed_seconds": 0,
                "reused_existing_patch": True,
            })
        else:
            operations, generation = generate_operations(
                api_url,
                key,
                design,
                worktree,
                prior_patch,
                prior_error,
            )
            event.update(generation)
            for operation in operations:
                relative = Path(str(operation.get("path", "")))
                if relative.is_absolute() or ".." in relative.parts or relative.suffix in {".md", ".yml", ".yaml"}:
                    raise ValueError(f"unsafe or non-source operation path: {relative}")
                target = worktree / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if operation.get("mode") == "create":
                    if target.exists():
                        current = target.read_text(encoding="utf-8")
                        try:
                            repaired = repair_operation(
                                api_url,
                                key,
                                design,
                                str(relative),
                                current,
                                operation,
                                "create target already exists; convert the intended edit to an exact replacement",
                            )
                            if repaired.get("path") != str(relative) or repaired.get("mode") != "replace":
                                raise ValueError(f"repair returned wrong path or mode for {relative}")
                            old = str(repaired.get("old", "")); new = str(repaired.get("new", ""))
                            if not old or old not in current:
                                raise ValueError(f"replacement context not found after create-mode repair: {relative}")
                            target.write_text(current.replace(old, new, 1), encoding="utf-8")
                        except (ValueError, RuntimeError, json.JSONDecodeError):
                            # Some providers return a complete replacement as
                            # `create` even when the target is present.  A
                            # bounded whole-file fallback is safe here only
                            # when the response includes substantial source
                            # content; otherwise preserve the strict failure.
                            content = str(operation.get("content", ""))
                            if len(content.strip()) < 80 or "\x00" in content:
                                raise
                            target.write_text(content, encoding="utf-8")
                    else:
                        target.write_text(str(operation.get("content", "")), encoding="utf-8")
                elif operation.get("mode") == "replace":
                    old = str(operation.get("old", "")); new = str(operation.get("new", ""))
                    current = target.read_text(encoding="utf-8")
                    if not old or old not in current:
                        repaired = repair_operation(api_url, key, design, str(relative), current, operation, f"replacement context not found; old={old[:180]!r}")
                        if repaired.get("path") != str(relative) or repaired.get("mode") != "replace":
                            raise ValueError(f"repair returned wrong path or mode for {relative}")
                        old = str(repaired.get("old", "")); new = str(repaired.get("new", ""))
                        if not old or old not in current:
                            raise ValueError(f"replacement context not found after repair: {relative}")
                    target.write_text(current.replace(old, new, 1), encoding="utf-8")
                else:
                    raise ValueError(f"unsupported edit mode for {relative}")
            # Providers occasionally preserve trailing spaces in generated
            # source content. Normalize only whitespace at line ends before
            # the strict git diff check; this cannot change runtime behavior.
            for operation in operations:
                path = worktree / Path(str(operation.get("path", "")))
                if path.is_file():
                    original = path.read_text(encoding="utf-8")
                    normalized = "\n".join(line.rstrip() for line in original.split("\n"))
                    if normalized != original:
                        path.write_text(normalized, encoding="utf-8")
        run(["git", "diff", "--check"], worktree)
        # Stage before exporting so newly-created source files are included in
        # the binary patch. An unstaged `git diff` silently omits untracked files.
        run(["git", "add", "-A"], worktree)
        patch_path = package / "solution" / "solution.patch"
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch = run(["git", "diff", "--cached", "--binary", row["base_commit_hash"]], worktree)
        if patch and not patch.endswith("\n"):
            patch += "\n"
        patch_path.write_text(patch, encoding="utf-8")
        changed = run(["git", "diff", "--cached", "--name-only", row["base_commit_hash"]], worktree).splitlines()
        source_changed = [
            path
            for path in changed
            if path
            and not re.search(
                r"(^|/)(tests?|testRunner|scripts?)(/|$)"
                r"|(^|/)[^/]*(?:_test|\.test|\.spec)\.[^/]+$"
                r"|\.(md|yml|yaml|json|sh)$",
                path,
                re.I,
            )
        ]
        if len(source_changed) < 7:
            raise ValueError(f"reference patch touches fewer than 7 source files: {len(source_changed)}")
        declared = {
            str(path) for path in design.get("affected_source_files", [])
            if str(path)
            and not re.search(
                r"(^|/)(tests?|testRunner|scripts?)(/|$)"
                r"|(^|/)[^/]*(?:_test|\.test|\.spec)\.[^/]+$"
                r"|\.(md|yml|yaml|json|sh)$",
                str(path),
                re.I,
            )
        }
        covered = declared.intersection(source_changed)
        required_coverage = max(7, (3 * len(declared) + 3) // 4)
        if len(covered) < required_coverage:
            missing = sorted(declared - covered)
            raise ValueError(
                f"reference patch covers only {len(covered)}/{len(declared)} declared source files; "
                f"requires {required_coverage}; missing={missing}"
            )
        missing_stages = []
        changed_set = set(source_changed)
        for index, stage in enumerate(design.get("pr_chain", []), 1):
            stage_files = {str(path) for path in stage.get("files", []) if str(path)}
            if stage_files and changed_set.isdisjoint(stage_files):
                missing_stages.append(str(stage.get("stage") or index))
        if missing_stages:
            raise ValueError(f"reference patch omits PR-chain stages: {missing_stages}")
        numstat = run(["git", "diff", "--cached", "--numstat", row["base_commit_hash"]], worktree)
        changed_lines = 0
        source_set = set(source_changed)
        for line in numstat.splitlines():
            added, deleted, path = line.split("\t", 2)
            if path in source_set and added.isdigit() and deleted.isdigit():
                changed_lines += int(added) + int(deleted)
        if changed_lines < 500:
            raise ValueError(f"reference patch changes fewer than 500 lines: {changed_lines}")
        if changed_lines > 1800:
            raise ValueError(f"reference patch exceeds 1800 changed lines: {changed_lines}")
        run(["git", "commit", "-m", "reference implementation"], worktree)
        task_id = f"{slugify(design.get('title', ''), 'deep-swe-task')}-{slot.rsplit('-', 1)[-1]}"
        design["task_id_slug"] = task_id
        design_path.write_text(json.dumps(design, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (package / "instruction.md").write_text(instruction_text(design), encoding="utf-8")
        (package / "task.toml").write_text(task_toml(task_id, design, row["language"], row["repository"], row["base_commit_hash"]), encoding="utf-8")
        (package / "pre_artifacts.sh").write_text("#!/bin/sh\nset -eu\ncd /app\ngit diff --binary " + row["base_commit_hash"] + " HEAD > /logs/artifacts/model.patch\n", encoding="utf-8")
        os.chmod(package / "pre_artifacts.sh", 0o755)
        (package / "solution" / "solve.sh").write_text("#!/bin/sh\nset -eu\ncd /app\ngit apply --whitespace=nowarn /solution/solution.patch\n", encoding="utf-8")
        os.chmod(package / "solution" / "solve.sh", 0o755)
        envdir = package / "environment"
        envdir.mkdir(exist_ok=True)
        (envdir / "Dockerfile").write_text("FROM ubuntu:24.04\nENV DEBIAN_FRONTEND=noninteractive\nRUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates build-essential python3 && rm -rf /var/lib/apt/lists/*\nWORKDIR /app\n", encoding="utf-8")
        row.update({"status": "in_progress", "stage": "qwen_hidden_tests", "task_id": task_id, "reference_stats": {"changed_files": changed, "source_files": source_changed, "source_file_count": len(source_changed), "source_changed_lines": changed_lines, "total_changed_lines": changed_lines}, "artifacts": ["authoring/issue-design.json", "instruction.md", "task.toml", "pre_artifacts.sh", "environment/Dockerfile", "solution/solution.patch", "solution/solve.sh"], "usage": {**row.get("usage", {}), "reference_implementation": event["usage"]}, "errors": []})
        event["patch_path"] = str(patch_path)
    except Exception as exc:
        row.update({"status": "failed", "stage": "reference_implementation", "errors": [repr(exc)]})
        event.update({"status": "failed", "error": repr(exc)})
    finally:
        repo_value = row.get("repository")
        if repo_value:
            repo = root / "workspaces" / "repositories" / repo_value.replace("/", "__")
            worktree = root / "workspaces" / "reference" / slot
            if worktree.exists() and (repo / ".git").exists():
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(worktree)],
                    cwd=repo,
                    check=False,
                    capture_output=True,
                    text=True,
                )
    return row, event


def main() -> None:
    global MODEL
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--slot", help="process one manifest slot explicitly")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument(
        "--repair-existing",
        action="store_true",
        help="regenerate an explicitly selected task that has advanced past reference implementation",
    )
    parser.add_argument("--repair-feedback-file", type=Path)
    parser.add_argument("--reuse-existing-patch", action="store_true")
    parser.add_argument("--import-patch-file", type=Path)
    args = parser.parse_args()
    args.root = args.root.resolve()
    if args.import_patch_file:
        if not args.slot:
            raise SystemExit("--import-patch-file requires --slot")
        imported_patch = args.import_patch_file.resolve()
        if not imported_patch.is_file():
            raise SystemExit(f"import patch is missing: {imported_patch}")
        destination = args.root / "tasks" / args.slot / "solution" / "solution.patch"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(imported_patch.read_bytes())
    env = {}
    for line in args.env_file.read_text(encoding="utf-8").splitlines():
        if line.strip() and "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1); env[k.strip()] = v.strip().strip("\"'")
    key = env.get("ANTHROPIC_API_KEY", "")
    MODEL = env.get("ANTHROPIC_MODEL", MODEL)
    base = env.get("ANTHROPIC_BASE_URL", DEFAULT_API).rstrip("/")
    api_url = base if base.endswith("/v1/messages") else base + ("/messages" if base.endswith("/v1") else "/v1/messages")
    if not key:
        raise SystemExit("env file must define ANTHROPIC_API_KEY")
    manifest = args.root / "registry/task_manifest.jsonl"
    rows = load_jsonl(manifest)
    repair_feedback = ""
    if args.repair_feedback_file:
        repair_feedback = args.repair_feedback_file.read_text(encoding="utf-8", errors="replace")
    selected = [
        row for row in rows
        if (not args.slot or row.get("slot") == args.slot)
        and (
            (
                row.get("stage") == "reference_implementation"
                and (row.get("status") != "failed" or args.retry_failed)
            )
            or (
                args.repair_existing
                and args.slot
                and row.get("slot") == args.slot
                and row.get("stage") in {"qwen_hidden_tests", "qa"}
            )
            or (
                args.retry_failed
                and args.slot
                and row.get("slot") == args.slot
                and row.get("stage") == "qa"
                and row.get("status") == "failed"
            )
        )
    ][: args.limit]
    events = args.root / "registry/production-events.jsonl"
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [
            executor.submit(
                process,
                args.root,
                row,
                api_url,
                key,
                repair_feedback,
                args.reuse_existing_patch,
            )
            for row in selected
        ]
        for future in concurrent.futures.as_completed(futures):
            updated, event = future.result()
            for row in rows:
                if row["slot"] == updated["slot"]:
                    row.update(updated)
                    break
            append_jsonl(events, event)
            atomic_jsonl(manifest, rows)
            print(json.dumps({"slot": updated["slot"], "status": updated["status"], "stage": updated["stage"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
