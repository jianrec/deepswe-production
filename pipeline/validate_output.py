#!/usr/bin/env python3
"""Validate every published Harbor task and reject transient or secret files."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


FORBIDDEN_PARTS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache"}
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[opsu]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--expected-count", type=int)
    args = parser.parse_args()
    root = args.root.resolve()
    output = root / "output"
    tasks = sorted(path for path in output.glob("task-*") if path.is_dir())
    errors: list[str] = []

    if args.expected_count is not None and len(tasks) != args.expected_count:
        errors.append(f"expected {args.expected_count} tasks, found {len(tasks)}")

    for task in tasks:
        result = subprocess.run(
            ["python3", str(root / "pipeline/validate_task.py"), str(task)],
            capture_output=True,
            text=True,
        )
        if result.returncode:
            errors.append(f"{task.name}: {result.stdout.strip() or result.stderr.strip()}")
        for path in task.rglob("*"):
            relative = path.relative_to(task)
            if any(part in FORBIDDEN_PARTS for part in relative.parts) or path.name == ".DS_Store":
                errors.append(f"{task.name}: forbidden transient path {relative}")
                continue
            if not path.is_file() or path.stat().st_size > 5_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if any(pattern.search(text) for pattern in SECRET_PATTERNS):
                errors.append(f"{task.name}: possible secret in {relative}")

    print(json.dumps({"valid": not errors, "task_count": len(tasks), "errors": errors}, ensure_ascii=False))
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
