#!/usr/bin/env python3
"""Generate language-aware hidden tests, never exposing the Oracle."""

from __future__ import annotations

import argparse
import ast
import concurrent.futures
import json
import os
import re
import shlex
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from filelock import exclusive_lock

MODEL = "gpt-5.6-sol"
MAX_OUTPUT_TOKENS = 24000
DEFAULT_URL = "https://www.packyapi.com/v1/responses"
DEFAULT_API_MODE = "responses"
TEST_PATTERNS = {
    "typescript": (r"[._-](test|spec)(?:\.[^.]+)?\.(ts|tsx)$", "Vitest/Jest or the repository's existing TypeScript runner"),
    "javascript": (r"[._-](test|spec)(?:\.[^.]+)?\.(js|jsx|mjs|cjs)$", "the repository's existing JavaScript test runner"),
    "python": (r"(^|/)(test_[^/]+|[^/]+_test)\.py$", "pytest/unittest as already used by the repository"),
    "go": (r"_test\.go$", "go test"),
    "rust": (r"(^|/)(tests?/.*\.rs|[^/]+_test\.rs)$", "cargo test using integration tests where possible"),
}


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
    with exclusive_lock(lock_path):
        rows = load_jsonl(path)
        current = next((row for row in rows if row.get("slot") == updated.get("slot")), None)
        if current is None:
            raise ValueError(f"manifest slot disappeared: {updated.get('slot')}")
        current.update(updated)
        atomic_jsonl(path, rows)


def append_jsonl(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def cumulative_qwen_usage(root: Path, slot: str, current_event: dict) -> tuple[dict, int]:
    events_path = root / "registry" / "production-events.jsonl"
    events = load_jsonl(events_path) if events_path.is_file() else []
    relevant = [
        item
        for item in events
        if item.get("slot") == slot
        and item.get("stage") == "qwen_hidden_tests"
        and item.get("model") == MODEL
        and not item.get("replayed_response")
    ]
    if not current_event.get("replayed_response"):
        relevant.append(current_event)
    totals = {"input": 0, "cache": 0, "output": 0}
    for item in relevant:
        usage = item.get("usage") or {}
        for key in totals:
            totals[key] += int(usage.get(key) or 0)
    return totals, len(relevant)


def git(args: list[str], cwd: Path, timeout: int = 900) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, timeout=timeout)
    return result.stdout


def provider_name(api_url: str) -> str:
    return urllib.parse.urlparse(api_url).netloc or "openai-compatible"


def parse_responses_stream(raw: str) -> dict:
    completed = {}
    output_parts = []
    output_items = []
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        value = line.removeprefix("data:").strip()
        if not value or value == "[DONE]":
            continue
        event = json.loads(value)
        event_type = str(event.get("type") or "")
        if event_type == "response.output_text.delta" and event.get("delta"):
            output_parts.append(str(event["delta"]))
        elif event_type == "response.output_text.done" and event.get("text"):
            # Some gateways omit text deltas and emit only the completed text
            # event. Do not discard that response body.
            if not output_parts:
                output_parts.append(str(event["text"]))
        elif event_type == "response.output_item.done" and isinstance(event.get("item"), dict):
            output_items.append(event["item"])
        elif event_type == "response.completed" and isinstance(event.get("response"), dict):
            completed = event["response"]
        elif event_type in {"response.failed", "response.error", "error"}:
            raise RuntimeError(json.dumps(event.get("error") or event, ensure_ascii=False)[:1800])
    if output_parts:
        completed["output_text"] = "".join(output_parts)
    elif output_items:
        completed["output"] = output_items
    elif isinstance(completed.get("output_text"), str) and completed["output_text"].strip():
        # Preserve a non-streamed output_text field from the completed object.
        completed["output_text"] = completed["output_text"]
    if not completed:
        raise ValueError("Responses stream did not contain a completed response")
    return completed


def call_hidden_model(url: str, key: str, prompt_text: str, api_mode: str, timeout: int = 600) -> dict:
    if api_mode == "responses":
        payload = {
            "model": MODEL,
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt_text}],
                }
            ],
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "stream": True,
        }
    else:
        payload = {
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt_text}],
            "temperature": 0,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "response_format": {"type": "json_object"},
        }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.time()
    result = {"status": "failed", "http": None, "usage": {}, "elapsed_seconds": None}
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", "replace")
                body = parse_responses_stream(raw) if api_mode == "responses" else json.loads(raw)
                result.update({"status": "success", "http": response.status, "body": body})
                break
        except urllib.error.HTTPError as exc:
            result.update({"http": exc.code, "error": exc.read().decode("utf-8", "replace")[:1800]})
        except Exception as exc:
            result["error"] = repr(exc)
        if attempt < 2:
            time.sleep(2**attempt)
    result["elapsed_seconds"] = round(time.time() - started, 3)
    result["usage"] = (result.get("body") or {}).get("usage", {})
    return result


def response_text(result: dict) -> str:
    body = result.get("body") or {}
    choices = body.get("choices") or []
    if choices:
        return str((choices[0].get("message") or {}).get("content") or "")
    if body.get("output_text"):
        return str(body["output_text"])
    parts = []
    for item in body.get("output") or []:
        for content in item.get("content") or []:
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                parts.append(str(content["text"]))
    return "".join(parts)


def response_json(result: dict) -> dict:
    content = response_text(result)
    content = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.I)
    content = re.sub(r"\s*```$", "", content)
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as original_error:
        decoder = json.JSONDecoder()
        parsed = None
        for match in re.finditer(r"\{", content):
            try:
                candidate, _ = decoder.raw_decode(content, match.start())
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                parsed = candidate
                break
        if parsed is None:
            raise original_error
    if not isinstance(parsed, dict):
        raise ValueError("hidden-test model response must be an object")
    return parsed


def is_test_path(path: str, language: str) -> bool:
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        return False
    pattern, _ = TEST_PATTERNS[language]
    if language == "rust" and relative.name == "mod.rs":
        # Integration-test support modules are imported by test crates but do
        # not contain runnable test cases and must not be whitelisted as test
        # files themselves.
        return False
    return bool(re.search(pattern, path, re.I))


def is_test_support_path(path: str, language: str) -> bool:
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        return False
    if language in {"javascript", "typescript"}:
        return bool(re.search(r"(^|/)support/[^/]*Reporter\.(?:js|cjs|mjs|ts)$", path, re.I))
    if language == "rust":
        return bool(re.search(r"^tests/hidden_support/[^/]+\.rs$", path, re.I))
    return False


def is_regression_test_path(path: str, language: str) -> bool:
    """Allow generated P2P files while keeping them in test-only locations."""
    normalized = path.replace("\\", "/")
    if normalized.startswith("test_operations/"):
        return language in {"rust", "go"} and normalized.endswith(("_test.rs", "_test.go"))
    return is_test_path(path, language) or is_test_support_path(path, language)


def public_tests(repo: Path, language: str) -> list[str]:
    tracked = git(["ls-files"], repo, timeout=120).splitlines()
    if language == "python":
        paths = []
        for path in tracked:
            if not path.endswith(".py"):
                continue
            try:
                text = (repo / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if is_test_path(path, language) or re.search(r"^\s*>>>\s+", text, re.M):
                paths.append(path)
        return paths
    if language == "rust":
        paths = []
        for path in tracked:
            if not path.endswith(".rs"):
                continue
            try:
                text = (repo / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if re.search(r"#\[(?:tokio::)?test\]", text):
                paths.append(path)
        return paths
    return [path for path in tracked if is_test_path(path, language)]


def test_names(text: str, path: str, language: str) -> list[str]:
    names: list[str] = []
    if language in {"typescript", "javascript"}:
        # Match the same quote that opened the test name.  The previous
        # character-class form stopped at an apostrophe inside a double-quoted
        # name (for example `setStateName || 'anonymous'`), producing a
        # truncated whitelist ID that could never be matched by Vitest's
        # JUnit report.
        names.extend(
            match.group(2)
            for match in re.finditer(
                r"\b(?:it|test)\s*\(\s*(['\"])((?:\\.|(?!\1).)*)\1",
                text,
                re.S,
            )
        )
    elif language == "python":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            tree = ast.Module(body=[], type_ignores=[])
        names.extend(
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        )
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                names.extend(
                    method.name
                    for method in node.body
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and method.name.startswith("test_")
                )
        if ">>>" in text:
            names.extend(
                node.name
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and not node.name.startswith("test_")
                and ">>>" in (ast.get_docstring(node, clean=False) or "")
            )
    elif language == "go":
        names.extend(re.findall(r"^\s*func\s+(Test[A-Za-z0-9_]+)\s*\(", text, re.M))
    elif language == "rust":
        names.extend(re.findall(r"#\[(?:tokio::)?test\]\s*(?:async\s+)?fn\s+([A-Za-z0-9_]+)", text))
        # `test-case` parameterized tests are real runnable cases too.  Count
        # each function once so Rust integration suites such as fd's `tests`
        # target meet the 100-test P2P gate without asking the hidden model to
        # invent extra regression files.
        names.extend(
            re.findall(
                r"#\[test_case(?:[^\]]*)\]\s*(?:#\[[^\]]+\]\s*)*"
                r"(?:async\s+)?fn\s+([A-Za-z0-9_]+)",
                text,
                re.S,
            )
        )
    return [f"{path}.{name}" for name in names]


def collect_test_ids(repo: Path, paths: list[str], language: str, minimum: int, exclude: set[str] | None = None) -> list[str]:
    exclude = exclude or set()
    ids: list[str] = []
    for path in paths:
        try:
            ids.extend(item for item in test_names((repo / path).read_text(encoding="utf-8", errors="replace"), path, language) if item not in exclude)
        except OSError:
            continue
        if len(ids) >= minimum:
            break
    return ids


def command_test_paths(paths: list[str], command: str, language: str) -> list[str]:
    """Return public test files that the generated command can actually run."""
    normalized = command.replace("\\", "/")
    selected: list[str] = []
    if language == "rust":
        # Cargo's `--test NAME` runs only the named integration target.  A
        # broad `cargo test`/`--workspace` command covers all Rust test files,
        # while `--lib` is meaningful only for crates that actually declare a
        # library target.  Keep the whitelist aligned with what the command
        # can observe so unrun source-unit tests are not recorded as skipped.
        integration_targets = re.findall(
            r"(?:^|\s)--test(?:=|\s+)([A-Za-z0-9_-]+)", normalized
        )
        if integration_targets and "--lib" not in normalized:
            target_paths = {
                target: [
                    path for path in paths
                    if Path(path).name == f"{target}.rs"
                    or str(Path(path)).startswith(f"tests/{target}/")
                ]
                for target in integration_targets
            }
            selected = [path for target in integration_targets for path in target_paths[target]]
            return selected
        packages = re.findall(
            r"(?:^|\s)(?:-p|--package)(?:=|\s+)([A-Za-z0-9_-]+)",
            normalized,
        )
        package_paths = {
            "codex-cli": "codex-rs/cli/",
            "codex-config": "codex-rs/config/",
            "codex-exec": "codex-rs/exec/",
        }
        prefixes = [package_paths[package] for package in packages if package in package_paths]
        if prefixes:
            return [path for path in paths if any(path.startswith(prefix) for prefix in prefixes)]
    broad = (
        (language == "go" and re.search(r"\bgo\s+test(?:\s+-[^ ]+)*\s+\.\/\.\.\.", normalized))
        or (language == "rust" and ("--workspace" in normalized or re.search(r"\bcargo\s+test\b", normalized)))
        or (
            language in {"javascript", "typescript"}
            and re.search(r"\b(?:npm|pnpm|yarn)\s+(?:run\s+)?test\b", normalized)
            and "--runTestsByPath" not in normalized
        )
    )
    if broad:
        return paths
    for path in paths:
        if language == "go":
            parents = [str(Path(path).parent).replace("\\", "/")]
        else:
            parents = [str(parent).replace("\\", "/") for parent in Path(path).parents if str(parent) != "."]
        if path in normalized or any(
            re.search(
                rf"(?:^|[\s'\"])(?:\./)?{re.escape(parent)}(?:/\.\.\.)?(?:[\s'\"]|$)",
                normalized,
            )
            for parent in parents
        ):
            selected.append(path)
    return selected


def command_test_ids(ids: list[str], command: str, language: str) -> list[str]:
    if language != "go":
        return ids
    match = re.search(r"(?:^|\s)-run(?:=|\s+)(?:'([^']*)'|\"([^\"]*)\"|([^\s]+))", command)
    selected = ids
    if match:
        expression = next((value for value in match.groups() if value is not None), "")
        try:
            pattern = re.compile(expression)
        except re.error as exc:
            raise ValueError(f"invalid Go -run expression: {exc}") from exc
        selected = [node_id for node_id in selected if pattern.search(node_id.rsplit(".", 1)[-1])]
    skip = re.search(r"(?:^|\s)-skip(?:=|\s+)(?:'([^']*)'|\"([^\"]*)\"|([^\s]+))", command)
    if skip:
        expression = next((value for value in skip.groups() if value is not None), "")
        try:
            pattern = re.compile(expression)
        except re.error as exc:
            raise ValueError(f"invalid Go -skip expression: {exc}") from exc
        selected = [node_id for node_id in selected if not pattern.search(node_id.rsplit(".", 1)[-1])]
    return selected


def go_regression_suite(repo: Path, paths: list[str], preferred_command: str) -> tuple[str, list[str], list[str]]:
    """Select root-module Go packages that expose at least 100 real tests."""
    root_mod = repo / "go.mod"
    if not root_mod.is_file():
        return preferred_command, [], []
    by_directory: dict[str, list[str]] = {}
    for path in paths:
        target = repo / path
        parent = target.parent
        nearest = None
        for candidate in (parent, *parent.parents):
            if (candidate / "go.mod").is_file():
                nearest = candidate / "go.mod"
                break
            if candidate == repo:
                break
        if nearest != root_mod:
            continue
        directory = str(Path(path).parent).replace("\\", "/")
        by_directory.setdefault(directory, []).append(path)
    preferred = set(command_test_paths(paths, preferred_command, "go"))
    directories = sorted(
        by_directory,
        key=lambda directory: (
            not any(path in preferred for path in by_directory[directory]),
            -sum(len(test_names((repo / path).read_text(encoding="utf-8", errors="replace"), path, "go")) for path in by_directory[directory]),
            directory,
        ),
    )
    selected_paths: list[str] = []
    selected_ids: list[str] = []
    selected_directories: list[str] = []
    for directory in directories:
        directory_paths = by_directory[directory]
        directory_ids = collect_test_ids(repo, directory_paths, "go", 10**9)
        if not directory_ids:
            continue
        selected_directories.append(directory)
        selected_paths.extend(directory_paths)
        selected_ids.extend(directory_ids)
        if len(selected_ids) >= 120:
            break
    packages = ["." if directory == "." else "./" + directory for directory in selected_directories]
    command = "go test -json " + " ".join(packages)
    skip = re.search(r"(?:^|\s)-skip(?:=|\s+)(?:'([^']*)'|\"([^\"]*)\"|([^\s]+))", preferred_command)
    if skip:
        expression = next((value for value in skip.groups() if value is not None), "")
        command += " -skip " + json.dumps(expression)
    return command, selected_paths, command_test_ids(selected_ids, command, "go")


def go_feature_suite(repo: Path, paths: list[str]) -> tuple[str, list[str]]:
    """Build an exact Go command for every named test in generated F2P files."""
    ids = collect_test_ids(repo, paths, "go", 10**9)
    names = sorted({node_id.rsplit(".", 1)[-1] for node_id in ids})
    if not names:
        return "", []
    directories = sorted({str(Path(path).parent).replace("\\", "/") for path in paths})
    packages = ["." if directory == "." else "./" + directory for directory in directories]
    expression = "^(?:" + "|".join(re.escape(name) for name in names) + ")$"
    command = "go test -json " + " ".join(packages) + " -run " + json.dumps(expression)
    return command, command_test_ids(ids, command, "go")


def report_adapter() -> str:
    return r'''#!/usr/bin/env python3
import argparse, json, pathlib, re, xml.etree.ElementTree as ET

parser = argparse.ArgumentParser()
parser.add_argument("--bucket", choices=("f2p", "p2p"), required=True)
parser.add_argument("--rc", type=int, required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--log", required=True)
parser.add_argument("--config", default="/tests/config.json")
args = parser.parse_args()
config = json.loads(pathlib.Path(args.config).read_text())
ids = [str(x).strip() for x in config.get("f2p_node_ids" if args.bucket == "f2p" else "p2p_node_ids", []) if str(x).strip()]
output = pathlib.Path(args.output)
output.parent.mkdir(parents=True, exist_ok=True)

# Read a native JUnit file if the runner produced one.  Its framework-specific
# names are mapped back to the source-derived whitelist IDs below.
native = []
if output.is_file() and output.stat().st_size:
    try:
        candidate = ET.parse(output).getroot()
        for case in candidate.iter("testcase"):
            name = str(case.attrib.get("name") or "").strip()
            classname = str(case.attrib.get("classname") or "").strip()
            state = "passed"
            message = ""
            for child in case:
                tag = child.tag.rsplit("}", 1)[-1]
                if tag in {"failure", "error"}:
                    state = "failed"
                    message = str(child.get("message") or child.text or "")
                    break
                if tag == "skipped":
                    state = "skipped"
            native.append((classname, name, state, message))
    except Exception:
        native = []

statuses = {}
log_path = pathlib.Path(args.log)
if log_path.is_file():
    for line in log_path.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except Exception:
            continue
        name = str(event.get("Test") or "").strip()
        action = str(event.get("Action") or "").lower()
        if not name or action not in {"pass", "fail", "skip"}:
            continue
        state = "passed" if action == "pass" else ("skipped" if action == "skip" else "failed")
        for node_id in ids:
            if node_id == name or node_id.endswith("." + name) or node_id.endswith("/" + name):
                statuses[node_id] = state

    # Rust's stable human-readable harness output contains one line per test.
    for match in re.finditer(r"^test\s+(.+?)\s+\.\.\.\s+(ok|FAILED|ignored)\s*$", log_path.read_text(errors="replace"), re.M):
        name, action = match.groups()
        state = "passed" if action == "ok" else ("skipped" if action == "ignored" else "failed")
        for node_id in ids:
            leaf = node_id.rsplit(".", 1)[-1]
            if name == leaf or name.endswith("::" + leaf):
                statuses[node_id] = state

def split_node_id(node_id):
    # Test names may contain periods or ellipses.  Split at the source test
    # filename suffix, never at the final period in the human-readable name.
    match = re.match(r"^(.*?\.(?:test|spec)\.(?:tsx?|jsx?|mjs|cjs))\.(.*)$", node_id)
    if match:
        return match.group(1), match.group(2)
    match = re.match(r"^(.*?/(?:test_[^/]+|[^/]+_test)\.py)\.(.*)$", node_id)
    if match:
        return match.group(1), match.group(2)
    return node_id.rsplit(".", 1) if "." in node_id else (args.bucket, node_id)

root = ET.Element("testsuite", name=args.bucket, tests=str(len(ids)))
for node_id in ids:
    classname, name = split_node_id(node_id)
    case = ET.SubElement(root, "testcase", classname=classname, name=name)
    state = statuses.get(node_id)
    message = ""
    if state is None:
        matches = [
            item for item in native
            if item[1] == name
            or f"{item[0]}.{item[1]}" == node_id
            or node_id.endswith(f".{item[0]}.{item[1]}")
            or (
                node_id.startswith(item[0] + ".")
                and node_id[len(item[0]) + 1 :] == f"{item[1]}"
            )
            or item[1].startswith(name + "[")
            or item[1].endswith("." + name)
            or item[1].endswith(" " + name)
            or item[1].endswith("::" + name)
        ]
        if matches:
            state = "failed" if any(item[2] == "failed" for item in matches) else ("skipped" if any(item[2] == "skipped" for item in matches) else "passed")
            message = next((item[3] for item in matches if item[3]), "")
    if state == "failed" or (state is None and args.rc):
        failure = ET.SubElement(case, "failure", message="test case failed")
        failure.text = message or "The language-native test command failed; see the native log."
    elif state == "skipped" or state is None:
        skipped = ET.SubElement(case, "skipped", message="test result missing from native runner output")
        skipped.text = "The configured test ID was not observed in the native report."
ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)
'''


def source_context(repo: Path, design: dict, language: str) -> str:
    paths = public_tests(repo, language)
    chunks = ["\n--- PUBLIC TEST PATHS ---\n" + "\n".join(paths[:240])]
    tokens = {Path(item).stem.lower() for item in design.get("affected_source_files", [])}
    relevant = [path for path in paths if any(token and token in path.lower() for token in tokens)]
    for item in (relevant + paths)[:6]:
        path = repo / item
        try:
            value = path.read_text(encoding="utf-8", errors="replace")[:6000]
        except OSError:
            continue
        chunks.append(f"\n--- PUBLIC TEST EXCERPT {item} ---\n{value}")
    manifests = (
        "package.json",
        "pyproject.toml",
        "pytest.ini",
        "go.mod",
        "Cargo.toml",
        "codex-rs/Cargo.toml",
        "codex-rs/rust-toolchain.toml",
        "Makefile",
    )
    for name in manifests:
        path = repo / name
        if path.is_file():
            chunks.append(f"\n--- {name} ---\n{path.read_text(encoding='utf-8', errors='replace')[:6000]}")
    for item in design.get("affected_source_files", []):
        path = repo / item
        if path.is_file():
            chunks.append(f"\n--- BASE SOURCE {item} ---\n{path.read_text(encoding='utf-8', errors='replace')[:8000]}")
    return "".join(chunks)[:45000]


def prompt(design: dict, instruction: str, repo: Path, language: str, repair_feedback: str = "") -> str:
    _, runner = TEST_PATTERNS[language]
    base_test_paths = public_tests(repo, language)
    base_named_tests = collect_test_ids(repo, base_test_paths, language, 10**9)
    p2p_shortfall = max(0, 100 - len(base_named_tests))
    return f"""You are the independent hidden-test author for an original Harbor coding task.
You must not read, infer, request, or describe any reference implementation. Use only the pinned base
repository excerpts, public issue, acceptance criteria, and public test conventions below.

Language: {language}
Expected runner: {runner}
Base repository named public tests detected: {len(base_named_tests)}
Minimum additional top-level P2P tests required in test_operations: {p2p_shortfall}
Verified Linux/offline repository preflight: {json.dumps((design.get('repository') or {}).get('runtime_preflight', {}), ensure_ascii=False)}

Issue:
{instruction}

Acceptance criteria:
{json.dumps(design['acceptance_criteria'], ensure_ascii=False)}

Public API contract:
{json.dumps(design.get('public_api_contract', []), ensure_ascii=False)}

{repair_feedback}

    Return ONLY JSON with keys: test_operations, test_command, regression_command, mapping.
    test_operations MUST be an array containing at least 7 objects over at least 5 distinct test files.
    Use one create operation per test concern/file when possible; do not put the whole feature suite in one file.
Each operation is {{path, bucket:'f2p'|'p2p', mode, old, new}} for replace or
{{path, bucket:'f2p'|'p2p', mode:'create'|'append', content}}. `f2p` tests exercise only the requested new
feature and must fail on the base revision. `p2p` tests exercise existing behavior and must pass both the
base and a correct implementation. If the repository does not expose 100 runnable public regression
cases, add enough independent top-level `p2p` test functions to reach 100; do not merely duplicate
assertions or names. The numeric shortfall above is mandatory, not a suggestion.
All paths must be legitimate {language} test files following the repository's existing conventions.
	For Go, every hidden test file MUST be runnable on Linux: do not add any `//go:build` line, OS-specific
	build constraint, cgo-only requirement, or Windows/Darwin tag. The verifier is Linux and network-disabled.
	The Go regression command must not select any package directory containing an F2P test file because Go
	compiles all test files in a selected package before applying `-run`. When necessary, create a dedicated
	P2P-only test package with enough independent base-API cases to reach 100 P2P nodes.
	Do not modify production code, package manifests, lockfiles, runner configuration, CI, or documentation.

Do not invent production symbols, constructors, tool types, or method signatures. A hidden test may
reference a new symbol only when its exact name and signature are explicitly part of the Public API
contract above. Otherwise test through an existing public entry point shown in the pinned base excerpts.
Comments, source-text assertions, and compile failures caused only by guessed APIs are not behavioral tests.

The tests must be deterministic and offline. Include 40-150 top-level named fail-to-pass functions
(table rows and nested subtests do not count toward this minimum) covering public
behavior, edge conditions, invalid inputs, integration boundaries, and every acceptance criterion. Also
select or add regression tests that produce 100-1500 named pass-to-pass cases. Property cases may be
generated deterministically, but each graded case needs a stable report name. Do not skip when an import
or symbol is absent, catch missing-feature errors, return early, assert source text only, or reimplement
the feature algorithm inside tests.

Every configured case must actually be selected by its command. In Go, package paths must cover every
created hidden test file, and any `-run` expression is counted literally; do not use `-run` for the
regression command unless it matches at least 100 existing top-level `Test...` functions.

test_command and regression_command are shell commands run from /app after test.patch is applied. They
must execute only the intended feature/regression tests. JavaScript, TypeScript, and Python commands must
write JUnit XML reports to exactly /logs/verifier/feature-junit.xml and
/logs/verifier/regression-junit.xml. Go must use `go test -json`; Rust may use stable `cargo test` output.
When the Rust workspace manifest is nested (for example `codex-rs/Cargo.toml`), commands run from /app
must use the correct `--manifest-path`; do not assume the repository root contains Cargo.toml.
Use only installed repository tools; the verifier runs without network access. mapping maps every
acceptance criterion to concrete test names.

PINNED BASE REPOSITORY EXCERPTS:
{source_context(repo, design, language)}"""


def qa_repair_feedback(root: Path, slot: str) -> str:
    """Return prior Qwen output and outcome-only QA evidence, never Oracle contents."""
    response_dir = root / "logs" / "model-responses" / slot
    responses = sorted([*response_dir.glob("qwen-*.json"), *response_dir.glob("hidden-*.json")])
    previous = responses[-1].read_text(encoding="utf-8", errors="replace")[:70000] if responses else ""
    qa = root / "logs" / "qa" / slot / "base-preflight" / "verifier"
    logs = []
    for name in ("feature-native.log", "regression-native.log"):
        path = qa / name
        if path.is_file():
            logs.append(f"--- {name} ---\n" + path.read_text(encoding="utf-8", errors="replace")[-12000:])
    review_path = root / "workspaces" / "strong-artifacts" / slot / "hidden-test-gap-review.json"
    review = review_path.read_text(encoding="utf-8", errors="replace")[:50000] if review_path.is_file() else ""
    events_path = root / "registry" / "production-events.jsonl"
    validation_error = ""
    if events_path.is_file():
        failures = [
            item
            for item in load_jsonl(events_path)
            if item.get("slot") == slot
            and item.get("stage") == "qwen_hidden_tests"
            and item.get("status") == "failed"
        ]
        if failures:
            validation_error = str(failures[-1].get("error") or "")
    mutant_summary: list[dict] = []
    qa_report_path = root / "tasks" / slot / "authoring" / "qa-report.json"
    solution_path = root / "tasks" / slot / "solution" / "solution.patch"
    mutant_dir = root / "workspaces" / "mutants" / slot / "patches"
    if qa_report_path.is_file() and solution_path.is_file() and mutant_dir.is_dir():
        qa_report = json.loads(qa_report_path.read_text(encoding="utf-8"))
        mutant_results = ((qa_report.get("docker") or {}).get("mutants") or [])
        solution_paths = set(re.findall(
            r"^diff --git a/(.*?) b/",
            solution_path.read_text(encoding="utf-8", errors="replace"),
            re.M,
        ))
        for index, mutant_path in enumerate(sorted(mutant_dir.glob("mutant-*.patch"))):
            mutant_paths = set(re.findall(
                r"^diff --git a/(.*?) b/",
                mutant_path.read_text(encoding="utf-8", errors="replace"),
                re.M,
            ))
            reward = (mutant_results[index].get("reward") or {}) if index < len(mutant_results) else {}
            mutant_summary.append({
                "mutant": mutant_path.stem,
                "reverted_source_files": sorted(solution_paths - mutant_paths),
                "reward": reward.get("reward"),
                "f2p_passed": reward.get("f2p_passed"),
                "f2p_total": reward.get("f2p_total"),
            })
    if not previous and not logs:
        raise ValueError(f"no prior Qwen response or base-preflight QA evidence exists for {slot}")
    return f"""REPAIR MODE:
The prior hidden-test attempt failed NOP/base QA. Return a complete corrected JSON response, not a patch
to the previous JSON. Keep all valid behavioral coverage, but fix every package, existing API usage,
test command, and regression-isolation problem shown below. This feedback contains only the base
	revision run and your own previous tests; it contains no reference implementation.

	For Go, remember that `go test` compiles every `_test.go` file in each selected package before applying
	`-run`. The regression command MUST NOT select any package directory containing an F2P file. If disjoint
	existing packages expose fewer than 100 named tests, add at least 100 real independent P2P test
	functions under a dedicated new package/directory that exercises only base-revision APIs.

	STRONG-MODEL GAP REVIEW (read-only audit; use constraints and base-proven signatures, but do not infer
	or request any reference implementation):
	{review}

	PRIOR VALIDATION FAILURE:
	{validation_error}

	MUTANT QA OUTCOMES (file names and scores only; no Oracle code or output):
	{json.dumps(mutant_summary, ensure_ascii=False)}
	Any mutant with reward 1 survived and exposes a real behavioral coverage gap. Add deterministic F2P
	tests that distinguish the requested feature when that integration adapter is reverted, using only the
	public API contract and base-repository evidence. Keep all previously valid tests and commands.

	PRIOR QWEN RESPONSE:
{previous}

BASE-PREFLIGHT FAILURE LOGS:
{''.join(logs)}
"""


def native_command(command: str, language: str) -> str:
    command = re.sub(r"\s*\|\|\s*true\s*$", "", command, flags=re.I)
    command = re.sub(r"\s*;\s*exit\s+0\s*$", "", command, flags=re.I)
    forbidden = ("&& true", "; true", "exit 0", "pytest-benchmark")
    if any(token in command.lower() for token in forbidden):
        raise ValueError("test command suppresses failures or exits successfully unconditionally")
    if re.search(r"(?:^|[;\s])\|\|\s*(?:true|:)(?:\s|$)", command, re.I):
        raise ValueError("test command suppresses failures or exits successfully unconditionally")
    if language == "go" and re.search(r"\bgo\s+test\b", command) and "-json" not in command:
        command = re.sub(r"\bgo\s+test\b", "go test -json", command, count=1)
    if language == "go":
        command = re.sub(
            r"\s+(?:1?>|1?>>)\s*/logs/verifier/(?:feature|regression)-junit\.(?:xml|jsonl|json|txt)\s*$",
            "",
            command,
        )
    if language == "rust" and command.strip() == "cargo test --locked --lib --test tests":
        # fd-find is a binary-only crate with an integration target named
        # `tests`; Cargo rejects the combined --lib/--test invocation.  Run
        # the complete test graph instead, which covers the same whitelist.
        return "cargo test --locked --test tests"
    return command


def standard_test_sh(test_command: str, regression_command: str) -> str:
    return f"""#!/bin/bash
set -uo pipefail
trap 'if [ ! -f /logs/verifier/reward.json ]; then mkdir -p /logs/verifier; echo -1 > /logs/verifier/reward.txt; fi' EXIT
cd /app || exit 6
python3 /tests/grader.py prepare || exit $?
[ -f /logs/verifier/reward.json ] && exit 0
mkdir -p /logs/verifier
set +e
(
  cd /app || exit 6
  {test_command}
) > /logs/verifier/feature-native.log 2>&1
feature_rc=$?
(
  cd /app || exit 6
  {regression_command}
) > /logs/verifier/regression-native.log 2>&1
regression_rc=$?
set -e
python3 /tests/report_adapter.py --bucket f2p --rc "$feature_rc" --log /logs/verifier/feature-native.log --output /logs/verifier/feature-junit.xml
python3 /tests/report_adapter.py --bucket p2p --rc "$regression_rc" --log /logs/verifier/regression-native.log --output /logs/verifier/regression-junit.xml
printf 'feature_rc=%s regression_rc=%s\n' "$feature_rc" "$regression_rc"
python3 /tests/grader.py grade
"""


def original_dockerfile(repository: str, commit: str, language: str, with_tests: bool) -> str:
    base = {
        "typescript": "node:22-bookworm",
        "javascript": "node:22-bookworm",
        # The current TheAlgorithms/Python pin declares Python >=3.14 and
        # intentionally uses a flat multi-package layout.  Installing it as
        # an editable distribution makes setuptools reject the checkout;
        # hidden tests import the source tree directly, so install only the
        # test runner for this repository.
        "python": "python:3.14-bookworm",
        "go": "golang:1.26-bookworm",
        "rust": "rust:1.95-bookworm",
    }[language]
    install = {
        "typescript": "if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm install --frozen-lockfile --ignore-scripts; elif [ -f yarn.lock ]; then corepack enable && yarn install --frozen-lockfile --ignore-scripts; elif [ -f package-lock.json ]; then npm ci --ignore-scripts; fi",
        "javascript": "if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm install --frozen-lockfile --ignore-scripts; elif [ -f yarn.lock ]; then corepack enable && yarn install --frozen-lockfile --ignore-scripts; elif [ -f package-lock.json ]; then npm ci --ignore-scripts; fi",
        "python": (
            "python -m venv /opt/venv && . /opt/venv/bin/activate && "
            "if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; "
            "elif [ \"" + repository + "\" != \"TheAlgorithms/Python\" ] && [ -f pyproject.toml ]; "
            "then pip install --no-cache-dir -e .; fi && "
            "pip install --no-cache-dir pytest==9.1.1"
        ),
        "go": "go mod download",
        "rust": "if [ -f Cargo.toml ]; then cargo fetch --locked; elif [ -f codex-rs/Cargo.toml ]; then cargo fetch --locked --manifest-path codex-rs/Cargo.toml; fi",
    }[language]
    env = "ENV PATH=/opt/venv/bin:$PATH\n" if language == "python" else ""
    tail = "\nWORKDIR /tests\nCOPY tests/ /tests/\nRUN chmod +x /tests/test.sh /tests/grader.py\nCMD [\"bash\", \"/tests/test.sh\"]" if with_tests else "\nCMD [\"bash\"]"
    # Keep the image minimal and avoid an unnecessary cmake package download;
    # repository-specific build dependencies are installed by the language
    # toolchain below and the Harbor tests run offline after image creation.
    return f"""FROM {base}
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get -o Acquire::Retries=5 update && apt-get -o Acquire::Retries=5 install -y --no-install-recommends git ca-certificates python3 build-essential pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
{env}WORKDIR /app
ARG BASE_SHA={commit}
LABEL deepswe.source_mode="local-pinned-checkout" \\
      deepswe.repository="{repository}" \\
      deepswe.base_sha="{commit}"
COPY repo/ /app/
RUN test "$(git rev-parse HEAD)" = "$BASE_SHA" \\
 && (git remote remove origin 2>/dev/null || true)
RUN {install}{tail}
"""


def load_grader(root: Path) -> str:
    # V2 keeps the migrated task packages in the sibling Harbor export while
    # the archive root stores authoring artifacts.  Prefer an archive-local
    # canonical grader when present, but fall back to the exported task root so
    # hidden-test generation remains resumable after the V1/V2 split.
    candidates = (
        root / "tasks" / "task-0001" / "tests" / "grader.py",
        root / "output" / "task-0001" / "tests" / "grader.py",
        root.parent / "tasks" / "task-0001" / "tests" / "grader.py",
        root.parent / "output" / "task-0001" / "tests" / "grader.py",
    )
    canonical = next((path for path in candidates if path.is_file()), candidates[0])
    if not canonical.is_file():
        raise FileNotFoundError("canonical Harbor grader is missing")
    grader = canonical.read_text(encoding="utf-8")
    stale_cleanup = 'if rc != 0 and ref == "HEAD" and (APP_DIR / f).exists():'
    if stale_cleanup not in grader:
        raise ValueError("canonical Harbor grader cleanup hook is missing")
    return grader.replace(stale_cleanup, 'if rc != 0 and (APP_DIR / f).exists():')


def apply_operations(verifier: Path, operations: list[dict], language: str) -> list[str]:
    paths = []
    for op in operations:
        path = str(op.get("path", ""))
        if not is_test_path(path, language) and not is_test_support_path(path, language) and not is_regression_test_path(path, language):
            raise ValueError(f"unsafe or non-test path: {path}")
        paths.append(path)
        target = verifier / path
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = op.get("mode")
        if mode == "create":
            if target.exists():
                raise ValueError(f"create target already exists: {path}")
            target.write_text(str(op.get("content", "")), encoding="utf-8")
        elif mode == "replace":
            current = target.read_text(encoding="utf-8")
            old = str(op.get("old", ""))
            if not old or old not in current:
                raise ValueError(f"test context not found: {path}")
            target.write_text(current.replace(old, str(op.get("new", "")), 1), encoding="utf-8")
        elif mode == "append":
            if not target.exists():
                raise ValueError(f"append target does not exist: {path}")
            with target.open("a", encoding="utf-8") as handle:
                handle.write(str(op.get("content", "")))
        else:
            raise ValueError(f"unsupported test operation: {path}")
    return paths


def validate_go_test_packages(verifier: Path, paths: list[str]) -> None:
    for path in paths:
        target = verifier / path
        match = re.search(r"^\s*package\s+([A-Za-z_][A-Za-z0-9_]*)", target.read_text(encoding="utf-8", errors="replace"), re.M)
        if not match:
            raise ValueError(f"Go hidden test has no package declaration: {path}")
        declared = match.group(1)
        production_packages = set()
        for source in target.parent.glob("*.go"):
            if source.name.endswith("_test.go"):
                continue
            source_match = re.search(
                r"^\s*package\s+([A-Za-z_][A-Za-z0-9_]*)",
                source.read_text(encoding="utf-8", errors="replace"),
                re.M,
            )
            if source_match:
                production_packages.add(source_match.group(1))
        allowed = production_packages | {name + "_test" for name in production_packages}
        if allowed and declared not in allowed:
            raise ValueError(
                f"Go hidden test package does not match its directory: {path} declares {declared}; "
                f"expected one of {sorted(allowed)}"
            )


def normalize_go_operation_paths(repo: Path, generated: dict) -> None:
    operations = generated.get("test_operations") or []
    package_directories: dict[str, set[str]] = {}
    for path in git(["ls-files", "*.go"], repo, timeout=120).splitlines():
        if path.endswith("_test.go"):
            continue
        target = repo / path
        match = re.search(
            r"^\s*package\s+([A-Za-z_][A-Za-z0-9_]*)",
            target.read_text(encoding="utf-8", errors="replace"),
            re.M,
        )
        if match:
            package_directories.setdefault(match.group(1), set()).add(
                str(Path(path).parent).replace("\\", "/")
            )
    occupied = {str(operation.get("path", "")) for operation in operations}
    for operation in operations:
        path = str(operation.get("path", ""))
        # The hidden-test author uses test_operations/ as an intentional
        # package boundary so F2P files cannot poison base P2P compilation.
        if path.replace("\\", "/").startswith("test_operations/"):
            continue
        text = str(operation.get("content", "")) + str(operation.get("new", ""))
        match = re.search(r"^\s*package\s+([A-Za-z_][A-Za-z0-9_]*)", text, re.M)
        if not match or not path.endswith("_test.go"):
            continue
        declared = match.group(1)
        base_package = declared[:-5] if declared.endswith("_test") else declared
        current_directory = str(Path(path).parent).replace("\\", "/")
        directories = package_directories.get(base_package, set())
        if current_directory in directories or not directories:
            continue
        if len(directories) != 1:
            continue
        destination_directory = next(iter(directories))
        destination = Path(path).name if destination_directory == "." else f"{destination_directory}/{Path(path).name}"
        if destination in occupied or (repo / destination).exists():
            continue
        occupied.remove(path)
        occupied.add(destination)
        operation["path"] = destination
        generated.setdefault("_normalizations", []).append(
            {
                "kind": "relocated_go_test_to_declared_package",
                "from": path,
                "to": destination,
                "package": declared,
            }
        )


def validate_go_regression_isolation(feature_paths: list[str], command: str) -> None:
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise ValueError(f"invalid Go regression shell command: {exc}") from exc
    packages = {token.rstrip("/") or "." for token in tokens if token == "." or token.startswith("./")}
    if not packages:
        packages = {"."}
    feature_packages = {
        "." if str(Path(path).parent) == "." else "./" + str(Path(path).parent).replace("\\", "/")
        for path in feature_paths
    }
    conflicts = set()
    for feature_package in feature_packages:
        for package in packages:
            if package == "./..." or package == feature_package:
                conflicts.add(feature_package)
            elif package.endswith("/...") and feature_package.startswith(package[:-4] + "/"):
                conflicts.add(feature_package)
    if conflicts:
        raise ValueError(
            "Go regression command selects packages containing F2P tests; base/P2P compilation cannot "
            f"be isolated: {sorted(conflicts)}"
        )


def regression_coverage(
    verifier: Path,
    language: str,
    feature_paths: list[str],
    changed_paths: list[str],
    generated_paths: list[str],
    command: str,
    feature_ids: list[str],
) -> tuple[list[str], list[str], list[str]]:
    all_paths = public_tests(verifier, language)
    candidates = [
        path for path in all_paths
        if path not in set(feature_paths)
        and (path not in set(changed_paths) or path in set(generated_paths))
    ]
    covered = command_test_paths(candidates, command, language)
    missing = sorted(set(generated_paths) - set(covered))
    if missing:
        raise ValueError(f"regression command does not cover generated P2P files: {missing}")
    scan_minimum = 10**9 if language == "go" and re.search(r"(?:^|\s)-run(?:=|\s)", command) else 100
    ids = command_test_ids(
        collect_test_ids(verifier, covered, language, scan_minimum, set(feature_ids)),
        command,
        language,
    )
    return all_paths, covered, ids


def p2p_repair_prompt(
    verifier: Path,
    language: str,
    shortfall: int,
    generated_paths: list[str],
    regression_command: str,
) -> str:
    excerpts = []
    for relative in generated_paths:
        path = verifier / relative
        if path.is_file():
            excerpts.append(f"\n--- CURRENT P2P FILE {relative} ---\n{path.read_text(encoding='utf-8', errors='replace')[:24000]}")
    return f"""You are repairing an independently-authored hidden regression suite for a Harbor task.
Language: {language}
Current regression command: {regression_command}
The suite is short by {shortfall} statically named top-level P2P tests. Add at least {shortfall + 10}
independent P2P tests that exercise existing base-repository behavior only. They must pass before and
after the feature implementation. Do not duplicate names/assertions, test source text, use snapshots,
skip, catch missing imports, access the network, or modify production/configuration files.

Return ONLY JSON with keys `test_operations`, `regression_command`, and `mapping`.
Every operation must use bucket `p2p` and a legitimate {language} test path. Use append with `content`
for an existing file or create with `content` for a new file. `regression_command` is the complete
replacement command and must run every old and new P2P file; JavaScript/TypeScript/Python must produce
JUnit at /logs/verifier/regression-junit.xml. Go uses -json and Rust may use stable cargo test output.
{''.join(excerpts)[:70000]}"""


def validate_generated(generated: dict, language: str) -> tuple[list[dict], str, str]:
    operations = generated.get("test_operations") or []
    if len(operations) < 7:
        raise ValueError("Qwen produced fewer than 7 test operations")
    paths = [str(op.get("path", "")) for op in operations]
    test_paths = [path for path in paths if is_test_path(path, language)]
    rust_support_paths = {
        path for path in paths
        if language == "rust" and path.startswith("tests/hidden_support/")
    }
    if (
        len(set(test_paths)) < 5
        or any(
            not is_test_path(path, language)
            and not is_test_support_path(path, language)
            and path not in rust_support_paths
            for path in paths
        )
    ):
        raise ValueError("Qwen tests do not cover five valid language-specific test files")
    if language == "go" and any(
        re.search(r"_(?:windows|darwin|freebsd|openbsd|netbsd|solaris|aix|plan9)_test\.go$", path, re.I)
        for path in paths
    ):
        raise ValueError("Go hidden test uses an OS-specific filename")
    buckets_by_path: dict[str, set[str]] = {}
    for operation in operations:
        bucket = str(operation.get("bucket", "f2p")).lower()
        if bucket not in {"f2p", "p2p"}:
            raise ValueError(f"invalid hidden-test bucket: {bucket}")
        operation["bucket"] = bucket
        buckets_by_path.setdefault(str(operation.get("path", "")), set()).add(bucket)
    if any(len(buckets) != 1 for buckets in buckets_by_path.values()):
        raise ValueError("one hidden test file cannot mix F2P and P2P operations")
    feature_paths = {
        path for path, buckets in buckets_by_path.items()
        if "f2p" in buckets and is_test_path(path, language)
    }
    if language == "rust":
        # Support modules are shared by the five or more integration targets;
        # they are not counted as independent test files.
        pass
    if len(feature_paths) < 5:
        raise ValueError("Qwen F2P tests do not cover five distinct test files")
    if language == "go":
        for operation in operations:
            for key in ("content", "new"):
                value = str(operation.get(key, ""))
                tags = re.findall(r"^\s*//go:build\s+(.+)$", value, re.M)
                for expression in tags:
                    words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expression.lower()))
                    if not words <= {"linux", "unix", "windows", "darwin"}:
                        raise ValueError(f"Go hidden test uses an unknown build tag: {expression}")
                if tags:
                    value = re.sub(r"^\s*//go:build[^\n]*\n(?:\s*// \+build[^\n]*\n)?\s*", "", value, count=1)
                    operation[key] = value
                    generated.setdefault("_normalizations", []).append(
                        {"kind": "removed_go_build_constraint", "path": str(operation.get("path", ""))}
                    )
    test_text = "\n".join(str(op.get("content", "")) + str(op.get("new", "")) for op in operations)
    feature_text = "\n".join(
        str(op.get("content", "")) + str(op.get("new", ""))
        for op in operations if op.get("bucket") == "f2p"
    )
    named_cases = test_names(feature_text, "hidden", language)
    if len(named_cases) < 40:
        raise ValueError(f"Qwen hidden tests expose fewer than 40 named cases: {len(named_cases)}")
    if language == "go":
        build_tags = re.findall(r"^\s*//go:build\s+(.+)$", test_text, re.M)
        if build_tags:
            raise ValueError("Go hidden tests must not contain build constraints; verifier is Linux")
        if re.search(r'^\s*import\s+"C"\s*$', test_text, re.M):
            raise ValueError("Go hidden tests must not require cgo")
    if language == "rust" and "#[cfg(test)]" not in test_text and "#[test]" not in test_text and "#[test_case" not in test_text:
        raise ValueError("Rust hidden tests contain no runnable test module")
    forbidden = ("skip(", ".skip", "pytest.skip", "return; // skip", "if (!compute")
    if any(pattern.lower() in test_text.lower() for pattern in forbidden):
        raise ValueError("Qwen produced skipped or vacuous fallback tests")
    feature = str(generated.get("test_command", "")).strip()
    regression = str(generated.get("regression_command", "")).strip()
    if not feature or not regression:
        raise ValueError("Qwen omitted feature or regression command")
    feature = native_command(feature, language)
    regression = native_command(regression, language)
    for command in (feature, regression):
        if re.search(r"(?:rm\s+-rf|curl\s+|wget\s+|git\s+(?:clone|fetch|pull)|docker\s+|/dev/tcp/)", command, re.I):
            raise ValueError("test command performs network access or destructive setup")
        if "junit" not in command.lower() and language not in {"go", "rust"}:
            raise ValueError("JavaScript, TypeScript, and Python test commands must request a JUnit report")
    return operations, feature, regression


def process(
    root: Path,
    row: dict,
    url: str,
    key: str,
    api_mode: str,
    repair_feedback: str = "",
    response_file: Path | None = None,
) -> tuple[dict, dict]:
    package = root / "tasks" / row["slot"]
    design = json.loads((package / "authoring/issue-design.json").read_text(encoding="utf-8"))
    repo = root / "workspaces" / "repositories" / row["repository"].replace("/", "__")
    language = row["language"]
    event = {"timestamp": now(), "slot": row["slot"], "stage": "qwen_hidden_tests", "provider": provider_name(url), "model": MODEL, "api_key_stored": False}
    verifier = root / "workspaces" / "verifier" / row["slot"]
    try:
        if response_file:
            generated = json.loads(response_file.read_text(encoding="utf-8"))
            event.update(
                {
                    "status": "success",
                    "http": None,
                    "usage": {"input": 0, "cache": 0, "output": 0},
                    "elapsed_seconds": 0,
                    "replayed_response": str(response_file),
                }
            )
        else:
            result = call_hidden_model(
                url,
                key,
                prompt(
                    design,
                    (package / "instruction.md").read_text(encoding="utf-8"),
                    repo,
                    language,
                    repair_feedback,
                ),
                api_mode,
            )
            usage = result.get("usage", {})
            input_details = usage.get("input_tokens_details") or {}
            event.update({"status": result.get("status"), "http": result.get("http"), "usage": {"input": usage.get("prompt_tokens", usage.get("input_tokens", 0)), "cache": usage.get("prompt_cache_hit_tokens", usage.get("cache_read_input_tokens", input_details.get("cached_tokens", 0))), "output": usage.get("completion_tokens", usage.get("output_tokens", 0))}, "elapsed_seconds": result.get("elapsed_seconds")})
            if result.get("status") != "success":
                raise RuntimeError(result.get("error", f"HTTP {result.get('http')}"))
            generated = response_json(result)
        if language == "go":
            normalize_go_operation_paths(repo, generated)
        event["response_keys"] = sorted(generated)
        event["response_preview"] = json.dumps(generated, ensure_ascii=False)[:2000]
        response_dir = root / "logs" / "model-responses" / row["slot"]
        response_dir.mkdir(parents=True, exist_ok=True)
        response_path = response_dir / (datetime.now(timezone.utc).strftime("hidden-%Y%m%dT%H%M%SZ.json"))
        response_path.write_text(json.dumps(generated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        event["response_path"] = str(response_path.relative_to(root))
        operations, feature_command, regression_command = validate_generated(generated, language)
        event["normalizations"] = generated.get("_normalizations", [])
        subprocess.run(["git", "worktree", "prune"], cwd=repo, check=True, capture_output=True, text=True)
        if verifier.exists():
            subprocess.run(["git", "worktree", "remove", "--force", str(verifier)], cwd=repo, check=False, capture_output=True, text=True)
            if verifier.exists():
                shutil.rmtree(verifier)
        git(["worktree", "add", "--detach", str(verifier), row["base_commit_hash"]], repo)
        paths = apply_operations(verifier, operations, language)
        if language == "go":
            validate_go_test_packages(verifier, paths)
        tests = package / "tests"
        tests.mkdir(parents=True, exist_ok=True)
        git(["add", "-A", "--", *paths], verifier)
        test_patch = git(["diff", "--cached", "--binary", row["base_commit_hash"], "--", *paths], verifier)
        if test_patch and not test_patch.endswith("\n"):
            test_patch += "\n"
        if not test_patch:
            raise ValueError("hidden-test model produced an empty test patch")
        (tests / "test.patch").write_text(test_patch, encoding="utf-8")
        (tests / "grader.py").write_text(load_grader(root), encoding="utf-8")
        changed_test_paths = sorted(set(paths))
        feature_test_paths = sorted(
            {
                str(op["path"])
                for op in operations
                if op.get("bucket") == "f2p"
                and is_test_path(str(op["path"]), language)
            }
        )
        # Rust helper modules under tests/hidden_support are compiled by the
        # integration-test targets but are not themselves test files.  They
        # must be excluded from the "every hidden test file" coverage gate.
        feature_support_paths = sorted(
            {
                str(op["path"])
                for op in operations
                if op.get("bucket") == "f2p"
                and language == "rust"
                and not is_test_path(str(op["path"]), language)
            }
        )
        generated_regression_paths = sorted({str(op["path"]) for op in operations if op.get("bucket") == "p2p"})
        feature_scan_minimum = 10**9 if language == "go" and re.search(r"(?:^|\s)-run(?:=|\s)", feature_command) else 40
        feature_ids = command_test_ids(
            collect_test_ids(verifier, feature_test_paths, language, feature_scan_minimum),
            feature_command,
            language,
        )
        if len(feature_ids) < 40 and language == "go":
            normalized_command, normalized_ids = go_feature_suite(verifier, feature_test_paths)
            if len(normalized_ids) >= 40:
                feature_command = normalized_command
                feature_ids = normalized_ids
                event.setdefault("normalizations", []).append(
                    {
                        "kind": "rebuilt_go_feature_command",
                        "test_files": len(feature_test_paths),
                        "named_tests": len(feature_ids),
                    }
                )
        if language == "go":
            covered_feature_paths = command_test_paths(feature_test_paths, feature_command, language)
            if set(covered_feature_paths) != set(feature_test_paths):
                normalized_command, normalized_ids = go_feature_suite(verifier, feature_test_paths)
                if normalized_command and len(normalized_ids) >= 40:
                    feature_command = normalized_command
                    feature_ids = normalized_ids
                    event.setdefault("normalizations", []).append(
                        {
                            "kind": "rebuilt_go_feature_command_for_relocated_paths",
                            "test_files": len(feature_test_paths),
                            "named_tests": len(feature_ids),
                        }
                    )
        if len(feature_ids) < 40:
            raise ValueError(f"hidden feature tests expose fewer than 40 named cases: {len(feature_ids)}")
        covered_feature_paths = command_test_paths(feature_test_paths, feature_command, language)
        if set(covered_feature_paths) != set(feature_test_paths):
            missing = sorted(set(feature_test_paths) - set(covered_feature_paths))
            raise ValueError(f"feature command does not cover every hidden test file: {missing}")
        if language == "go":
            feature_directories = {str(Path(path).parent).replace("\\", "/") for path in feature_test_paths}
            regression_candidates = [
                path
                for path in public_tests(verifier, language)
                if path not in set(changed_test_paths)
                and str(Path(path).parent).replace("\\", "/") not in feature_directories
            ]
            normalized_regression, _, _ = go_regression_suite(
                verifier,
                regression_candidates,
                regression_command,
            )
            if normalized_regression:
                regression_command = normalized_regression
                event.setdefault("normalizations", []).append(
                    {
                        "kind": "rebuilt_go_regression_command_around_f2p_packages",
                        "excluded_feature_directories": sorted(feature_directories),
                    }
                )
            validate_go_regression_isolation(feature_test_paths, regression_command)
        all_test_paths, regression_paths, regression_ids = regression_coverage(
            verifier,
            language,
            feature_test_paths,
            changed_test_paths,
            generated_regression_paths,
            regression_command,
            feature_ids,
        )
        if len(regression_ids) < 100 and language == "go":
            regression_command, regression_paths, regression_ids = go_regression_suite(
                verifier,
                [path for path in all_test_paths if path not in set(changed_test_paths)],
                regression_command,
            )
            regression_ids = [node_id for node_id in regression_ids if node_id not in set(feature_ids)]
        if len(regression_ids) < 100:
            shortfall = 100 - len(regression_ids)
            repair = call_hidden_model(
                url,
                key,
                p2p_repair_prompt(verifier, language, shortfall, generated_regression_paths, regression_command),
                api_mode,
            )
            repair_usage = repair.get("usage", {})
            repair_usage_normalized = {
                "input": repair_usage.get("prompt_tokens", repair_usage.get("input_tokens", 0)),
                "cache": repair_usage.get("prompt_cache_hit_tokens", repair_usage.get("cache_read_input_tokens", 0)),
                "output": repair_usage.get("completion_tokens", repair_usage.get("output_tokens", 0)),
            }
            event.setdefault("repairs", []).append(
                {
                    "kind": "p2p_shortfall",
                    "shortfall": shortfall,
                    "status": repair.get("status"),
                    "http": repair.get("http"),
                    "usage": repair_usage_normalized,
                    "elapsed_seconds": repair.get("elapsed_seconds"),
                }
            )
            for key_name in ("input", "cache", "output"):
                event["usage"][key_name] = event["usage"].get(key_name, 0) + repair_usage_normalized[key_name]
            if repair.get("status") != "success":
                raise RuntimeError(repair.get("error", "P2P repair request failed"))
            repair_generated = response_json(repair)
            repair_operations = repair_generated.get("test_operations") or []
            if not repair_operations:
                raise ValueError("P2P repair returned no test operations")
            repair_text = "\n".join(
                str(op.get("content", "")) + str(op.get("new", "")) for op in repair_operations
            )
            if len(test_names(repair_text, "repair", language)) < shortfall:
                raise ValueError("P2P repair did not add enough statically named tests")
            for operation in repair_operations:
                if str(operation.get("bucket", "p2p")).lower() != "p2p":
                    raise ValueError("P2P repair attempted to add a non-P2P operation")
                operation["bucket"] = "p2p"
            repair_paths = apply_operations(verifier, repair_operations, language)
            paths.extend(repair_paths)
            operations.extend(repair_operations)
            changed_test_paths = sorted(set(paths))
            generated_regression_paths = sorted(set(generated_regression_paths) | set(repair_paths))
            regression_command = native_command(str(repair_generated.get("regression_command", "")).strip(), language)
            if not regression_command:
                raise ValueError("P2P repair omitted the replacement regression command")
            if "junit" not in regression_command.lower() and language not in {"go", "rust"}:
                raise ValueError("P2P repair regression command does not produce JUnit")
            git(["add", "-A", "--", *sorted(set(repair_paths))], verifier)
            test_patch = git(["diff", "--cached", "--binary", row["base_commit_hash"], "--", *sorted(set(paths))], verifier)
            if test_patch and not test_patch.endswith("\n"):
                test_patch += "\n"
            (tests / "test.patch").write_text(test_patch, encoding="utf-8")
            all_test_paths, regression_paths, regression_ids = regression_coverage(
                verifier,
                language,
                feature_test_paths,
                changed_test_paths,
                generated_regression_paths,
                regression_command,
                feature_ids,
            )
            generated.setdefault("mapping", {})["p2p_repair"] = repair_generated.get("mapping", {})
        if len(regression_ids) < 100:
            raise ValueError(
                f"regression command covers fewer than 100 named public cases: {len(regression_ids)}"
            )
        config = {
            "base_commit": row["base_commit_hash"],
            "f2p_node_ids": feature_ids[:150],
            "p2p_node_ids": regression_ids[:1500],
            "grade": {"format": "junit", "tool_label": "deepswe-native-test-wrapper", "reports": ["/logs/verifier/feature-junit.xml", "/logs/verifier/regression-junit.xml"]},
        }
        (tests / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # Rust's native harness can run several integration targets in one
        # invocation, but the generated feature command must not accidentally
        # include the repository's P2P `tests` target.  Keep the exact command
        # chosen by the hidden-test author after normalization.
        (tests / "test.sh").write_text(standard_test_sh(feature_command, regression_command), encoding="utf-8")
        (tests / "report_adapter.py").write_text(report_adapter(), encoding="utf-8")
        os.chmod(tests / "test.sh", 0o755)
        (tests / "Dockerfile").write_text(original_dockerfile(row["repository"], row["base_commit_hash"], language, True), encoding="utf-8")
        environment = package / "environment"
        environment.mkdir(exist_ok=True)
        (environment / "Dockerfile").write_text(original_dockerfile(row["repository"], row["base_commit_hash"], language, False), encoding="utf-8")
        (environment / "source.json").write_text(
            json.dumps(
                {
                    "mode": "local-pinned-checkout",
                    "repository": row["repository"],
                    "base_commit_hash": row["base_commit_hash"],
                    "cache_path": f"workspaces/repositories/{row['repository'].replace('/', '__')}",
                    "remote_fetch_in_dockerfile": False,
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        (package / "authoring" / "test-mapping.json").write_text(json.dumps(generated.get("mapping", {}), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        row.update({"status": "in_progress", "stage": "qa", "test_model": MODEL, "artifacts": row.get("artifacts", []) + ["tests/test.patch", "tests/grader.py", "tests/config.json", "tests/test.sh", "tests/report_adapter.py", "tests/Dockerfile", "environment/source.json", "authoring/test-mapping.json"], "usage": {**row.get("usage", {}), "qwen_hidden_tests": event["usage"]}, "errors": []})
    except Exception as exc:
        row.update({"status": "failed", "stage": "qwen_hidden_tests", "errors": [repr(exc)]})
        event.update({"status": "failed", "error": repr(exc)})
    finally:
        if verifier.exists() and (repo / ".git").exists():
            subprocess.run(["git", "worktree", "remove", "--force", str(verifier)], cwd=repo, check=False, capture_output=True, text=True)
    cumulative, calls = cumulative_qwen_usage(root, row["slot"], event)
    row["usage"] = {
        **row.get("usage", {}),
        "qwen_hidden_tests": cumulative,
        "qwen_hidden_test_calls": calls,
    }
    event["cumulative_usage"] = cumulative
    event["model_calls"] = calls
    return row, event


def main() -> None:
    global MODEL
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--slot")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--repair-from-qa", action="store_true")
    parser.add_argument("--response-file", type=Path, help="replay one saved Qwen JSON response without a model call")
    args = parser.parse_args()
    args.root = args.root.resolve()
    if args.response_file:
        args.response_file = args.response_file.resolve()
    env = {}
    for line in args.env_file.read_text(encoding="utf-8").splitlines():
        if line.strip() and "=" in line and not line.lstrip().startswith("#"):
            key_name, value = line.split("=", 1)
            env[key_name.strip()] = value.strip().strip("\"'")
    MODEL = env.get("HIDDEN_TEST_MODEL", MODEL)
    api_mode = env.get("HIDDEN_TEST_API_MODE", DEFAULT_API_MODE).strip().lower()
    if api_mode not in {"responses", "chat_completions"}:
        raise SystemExit("HIDDEN_TEST_API_MODE must be responses or chat_completions")
    key = (
        env.get("PACKY_RESPONSES_API_KEY")
        or env.get("OPENAI_API_KEY")
        or env.get("DASHSCOPE_API_KEY")
        or env.get("QWEN_API_KEY")
        or env.get("BAILIAN_API_KEY")
    )
    if not key:
        raise SystemExit("hidden-test env file must define PACKY_RESPONSES_API_KEY, OPENAI_API_KEY, or a legacy Qwen key")
    base_url = env.get("PACKY_RESPONSES_BASE_URL") or env.get("OPENAI_BASE_URL") or env.get("BAILIAN_BASE_URL") or DEFAULT_URL
    base_url = base_url.rstrip("/")
    if api_mode == "responses":
        url = base_url if base_url.endswith("/responses") else base_url + ("/responses" if base_url.endswith("/v1") else "/v1/responses")
    else:
        url = base_url if base_url.endswith("/chat/completions") else base_url + ("/chat/completions" if base_url.endswith("/v1") else "/v1/chat/completions")
    manifest = args.root / "registry" / "task_manifest.jsonl"
    rows = load_jsonl(manifest)
    selected = [
        row for row in rows
        if (not args.slot or row.get("slot") == args.slot)
        and (row.get("stage") == "qwen_hidden_tests" or (args.slot and row.get("slot") == args.slot and row.get("stage") == "qa"))
        and (row.get("status") != "failed" or args.retry_failed or args.slot)
    ][: args.limit]
    events = args.root / "registry" / "production-events.jsonl"
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        for updated, event in executor.map(
            lambda item: process(
                args.root,
                item,
                url,
                key,
                api_mode,
                qa_repair_feedback(args.root, item["slot"]) if args.repair_from_qa else "",
                args.response_file,
            ),
            selected,
        ):
            append_jsonl(events, event)
            merge_manifest_row(manifest, updated)
            print(json.dumps({"slot": updated["slot"], "status": updated["status"], "stage": updated["stage"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
