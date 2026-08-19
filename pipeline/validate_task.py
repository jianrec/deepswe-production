#!/usr/bin/env python3
"""Static validation for a Harbor task package; no model or network calls."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("task", type=Path)
    args = parser.parse_args()
    root = args.task
    errors: list[str] = []
    core_required = (
        "task.toml",
        "instruction.md",
        "environment/Dockerfile",
        "tests/Dockerfile",
        "tests/config.json",
        "tests/grader.py",
        "tests/test.patch",
        "tests/test.sh",
        "solution/solution.patch",
        "solution/solve.sh",
    )
    v2_required = (
        "environment/source.json",
        "authoring/pr-chain.md",
        "authoring/difficulty-card.json",
        "authoring/test-mapping.json",
        "authoring/qa-report.json",
        "authoring/production-usage.json",
    )
    v2_design_path = root / "authoring/issue-design.json"
    try:
        v2_design = json.loads(v2_design_path.read_text(encoding="utf-8")) if v2_design_path.is_file() else {}
    except Exception:
        v2_design = {}
    is_v2_package = str(v2_design.get("pipeline_version", "")).startswith("2")
    for relative in core_required + (v2_required if is_v2_package else ()):
        if not (root / relative).is_file():
            fail(errors, f"missing {relative}")

    try:
        config = json.loads((root / "tests/config.json").read_text(encoding="utf-8"))
        design_path = root / "authoring/issue-design.json"
        design = json.loads(design_path.read_text(encoding="utf-8")) if design_path.is_file() else {}
        is_v2 = str(design.get("pipeline_version", "")).startswith("2")
        f2p = len(config.get("f2p_node_ids", []))
        p2p = len(config.get("p2p_node_ids", []))
        minimum_f2p = 40 if is_v2 else 30
        if not minimum_f2p <= f2p <= 150:
            fail(errors, f"F2P count outside {minimum_f2p}..150: {f2p}")
        if not 100 <= p2p <= 1500:
            fail(errors, f"P2P count outside 100..1500: {p2p}")
    except Exception as exc:
        fail(errors, f"invalid tests/config.json: {exc}")

    instruction = (root / "instruction.md").read_text(encoding="utf-8") if (root / "instruction.md").exists() else ""
    if not 1200 <= len(instruction) <= 6000:
        fail(errors, f"instruction length outside 1200..6000 chars: {len(instruction)}")
    if "solution.patch" in instruction or "test.patch" in instruction:
        fail(errors, "instruction leaks internal patch names")
    dockerfile = (root / "environment/Dockerfile").read_text(encoding="utf-8") if (root / "environment/Dockerfile").exists() else ""
    if re.search(r"solution\.patch|test\.patch|reference", dockerfile, re.I):
        fail(errors, "agent Dockerfile appears to contain answer/test leakage")
    if locals().get("is_v2"):
        public_api = design.get("public_api_contract") or []
        if not isinstance(public_api, list) or len(public_api) < 3:
            fail(errors, "V2 task lacks a concrete public API contract")
        if re.search(r"git\s+(?:clone|fetch|pull)|github\.com", dockerfile, re.I):
            fail(errors, "V2 agent Dockerfile fetches remote repository content")
        if "COPY repo/ /app/" not in dockerfile:
            fail(errors, "V2 agent Dockerfile does not use the staged local repository")

    solution = root / "solution/solution.patch"
    if solution.exists():
        patch_text = solution.read_text(encoding="utf-8", errors="replace")
        changed_files = re.findall(r"^diff --git a/(.+?) b/(.+)$", patch_text, re.M)
        source_files = {b for _, b in changed_files if not re.search(r"(^|/)(tests?|testRunner|scripts?)(/|$)|\.(md|json|sh)$", b, re.I)}
        added = sum(1 for line in patch_text.splitlines() if line.startswith("+") and not line.startswith("+++") )
        deleted = sum(1 for line in patch_text.splitlines() if line.startswith("-") and not line.startswith("---") )
        if len(source_files) < 7:
            fail(errors, f"source file count below official difficulty gate: {len(source_files)}")
        if added + deleted < 600:
            fail(errors, f"solution changed lines below difficulty gate: {added + deleted}")

    if is_v2_package:
        try:
            card = json.loads((root / "authoring/difficulty-card.json").read_text(encoding="utf-8"))
            score = float(card.get("difficulty_score", 0))
            if score < 1.0:
                fail(errors, f"difficulty score below official baseline: {score}")
            if int(card.get("module_count", 0)) < 3:
                fail(errors, "fewer than 3 modules in difficulty card")
        except Exception as exc:
            fail(errors, f"invalid authoring/difficulty-card.json: {exc}")

    print(json.dumps({"task": str(root), "valid": not errors, "errors": errors}, ensure_ascii=False))
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
