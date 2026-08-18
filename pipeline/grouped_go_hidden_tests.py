#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import json
import sys
from pathlib import Path

import qwen_tests


GROUPS = [
    ("hiddenrange/parse_valid_test.go", "ParseRange valid start-end, open-ended, suffix, clamping, mixed satisfiable and out-of-window ranges, and FormatContentRange"),
    ("hiddenrange/parse_invalid_test.go", "ParseRange malformed prefixes, empty specs, invalid numbers, inverted ranges, zero suffix, zero-size content, and ErrNoOverlap"),
    ("hiddenrange/reader_single_test.go", "ReaderRange single-range status, headers, exact bytes, custom headers, boundary conditions, and invalid ReaderRange inputs"),
    ("hiddenrange/reader_multi_test.go", "ReaderRange multipart status, boundary, MIME parts, per-part headers, ordering, exact payloads, and computed Content-Length"),
    ("hiddenrange/context_core_test.go", "DataFromReaderRange no-Range parity, Accept-Ranges, single/open/suffix ranges, 416 response, return value, and custom headers"),
    ("hiddenrange/context_conditional_test.go", "DataFromReaderRange If-Range strong ETag/date match and mismatch, weak ETag rejection, MaxRanges default/custom limits, and over-limit 200 fallback"),
    ("hiddenrange/file_fs_test.go", "FileFromFSRange with os.DirFS for single and multiple ranges, 416, missing files, MIME type, Last-Modified, and exact bytes"),
]


def load_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    return values


def generate(group: tuple[str, str], url: str, key: str, api_mode: str) -> tuple[dict, dict]:
    path, concern = group
    prefix = Path(path).stem.replace("_test", "")
    prompt = f"""Return ONLY compact JSON with keys path and content.
Create exactly one Go test file at {path}, package hiddenrange_test, containing exactly six independent top-level Test functions with unique names prefixed Test{prefix.title().replace('_', '')}.
Concern: {concern}.

Test the public APIs only:
- github.com/gin-gonic/gin/render: Range{{Start int64, Length int64}}, ParseRange(string,int64), FormatContentRange(int64,int64,int64), ErrNoOverlap, ReaderRange{{ContentType string, ContentLength int64, ReadSeeker io.ReadSeeker, Ranges []render.Range, Boundary string, Headers map[string]string}}.
- github.com/gin-gonic/gin: (*Context).DataFromReaderRange(int,int64,string,io.ReadSeeker,map[string]string) bool, (*Context).FileFromFSRange(string,fs.FS), Engine.MaxRanges int.

Use httptest, bytes.NewReader, mime/multipart, errors.Is, os.DirFS, and standard-library helpers as appropriate. Tests must be deterministic, offline, behavioral, compile with Go on Linux, and assert exact statuses/headers/bodies. Do not use build tags, subtests, loops as substitutes for the six top-level tests, source-text assertions, skips, third-party dependencies, or production edits. Avoid helper-name collisions by prefixing every file-local helper with {prefix}."""
    result = qwen_tests.call_hidden_model(url, key, prompt, api_mode, timeout=240)
    if result.get("status") != "success":
        raise RuntimeError(result.get("error", "hidden-test request failed"))
    response = qwen_tests.response_json(result)
    if response.get("path") != path or not str(response.get("content", "")).strip():
        raise ValueError(f"invalid grouped response for {path}")
    return response, result.get("usage") or {}


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: grouped_go_hidden_tests.py ENV_FILE OUTPUT_JSON")
    env = load_env(Path(sys.argv[1]))
    qwen_tests.MODEL = env.get("HIDDEN_TEST_MODEL", qwen_tests.MODEL)
    qwen_tests.MAX_OUTPUT_TOKENS = 7000
    api_mode = env.get("HIDDEN_TEST_API_MODE", "responses").strip().lower()
    key = env.get("PACKY_RESPONSES_API_KEY") or env.get("OPENAI_API_KEY")
    base_url = (env.get("PACKY_RESPONSES_BASE_URL") or env.get("OPENAI_BASE_URL") or qwen_tests.DEFAULT_URL).rstrip("/")
    url = base_url if base_url.endswith("/responses") else base_url + ("/responses" if base_url.endswith("/v1") else "/v1/responses")
    if not key:
        raise SystemExit("hidden-test API key is missing")

    operations = []
    usage = {"input": 0, "cache": 0, "output": 0}
    with concurrent.futures.ThreadPoolExecutor(max_workers=7) as executor:
        futures = [executor.submit(generate, group, url, key, api_mode) for group in GROUPS]
        for future in concurrent.futures.as_completed(futures):
            response, response_usage = future.result()
            operations.append({"path": response["path"], "bucket": "f2p", "mode": "create", "content": response["content"]})
            usage["input"] += int(response_usage.get("input_tokens") or response_usage.get("prompt_tokens") or 0)
            usage["output"] += int(response_usage.get("output_tokens") or response_usage.get("completion_tokens") or 0)

    operations.sort(key=lambda operation: operation["path"])
    generated = {
        "test_operations": operations,
        "test_command": "go test -json ./hiddenrange",
        "regression_command": "go test -json . ./binding ./ginS ./internal/bytesconv ./internal/fs ./render -skip '^TestSaveUploadedFileWithPermissionFailed$'",
        "mapping": {"grouped_f2p": [operation["path"] for operation in operations]},
        "grouped_usage": usage,
    }
    Path(sys.argv[2]).write_text(json.dumps(generated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"files": len(operations), "usage": usage}))


if __name__ == "__main__":
    main()
