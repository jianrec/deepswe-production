#!/usr/bin/env python3
"""Record local repository test capacity before model-backed task authoring."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from author_tasks import repository_complexity, repository_test_capacity


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
    parser.add_argument("--repository", action="append", default=[])
    args = parser.parse_args()
    root = args.root.resolve()
    registry = root / "registry/repository-candidates.jsonl"
    candidates = load_jsonl(registry)
    checked_at = datetime.now(timezone.utc).isoformat()
    audited = 0
    for candidate in candidates:
        if args.repository and candidate.get("full_name") not in args.repository:
            continue
        repository = root / "workspaces/repositories" / candidate["full_name"].replace("/", "__")
        if not (repository / ".git").exists():
            continue
        try:
            candidate["test_capacity"] = repository_test_capacity(repository, candidate["language"])
            candidate.update(repository_complexity(repository))
            candidate["test_capacity_checked_at"] = checked_at
            audited += 1
        except Exception as exc:
            candidate["test_capacity_error"] = repr(exc)
    atomic_jsonl(registry, candidates)
    eligible = sum(int(row.get("test_capacity", 0)) >= 100 for row in candidates)
    print(json.dumps({"audited": audited, "eligible_local_repositories": eligible}, ensure_ascii=False))


if __name__ == "__main__":
    main()
