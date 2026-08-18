#!/usr/bin/env python3
"""Export a portable, secret-free production state snapshot.

Only finalized rows whose task package exists in ``output/`` retain their
stage.  In-progress staging/worktrees are intentionally reset to repository
discovery because those transient files are not part of Git.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path


SECRET = re.compile(r"\b(?:sk|gh[opsu])[-_][A-Za-z0-9_-]{20,}\b")


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def scrub(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): scrub(item) for key, item in value.items() if key not in {"api_key", "api_key_stored"}}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    if isinstance(value, str):
        value = SECRET.sub("<redacted>", value)
        return value if not value.startswith("/") else Path(value).name
    return value


def compact_row(row: dict, root: Path) -> dict:
    slot = str(row.get("slot", ""))
    output_exists = (root / "output" / slot).is_dir()
    finalized = output_exists and row.get("status") == "finalized" and row.get("stage") == "finalized"
    if not finalized:
        return {
            "slot": slot,
            "language": row.get("language"),
            "status": "pending",
            "stage": "repository_discovery",
            "author_model": row.get("author_model", "claude-opus-4-8"),
            "test_model": row.get("test_model", "gpt-5.6-sol"),
            "rl_rollout_enabled": False,
            "artifacts": [],
            "usage": {},
            "qa": {},
            "errors": [],
            "pipeline_version": row.get("pipeline_version", "2.0"),
        }
    qa = row.get("qa") or {}
    docker = qa.get("docker") or {}
    compact_qa = {
        "status": qa.get("status"),
        "checked_at": qa.get("checked_at"),
        "docker": {
            "passed": docker.get("passed"),
            "nop_ok": docker.get("nop_ok"),
            "oracle_ok": docker.get("oracle_ok"),
            "mutant_ok": docker.get("mutant_ok"),
            "network_mode": docker.get("network_mode"),
        },
    }
    return scrub({
        "slot": slot,
        "language": row.get("language"),
        "status": "finalized",
        "stage": "finalized",
        "task_id": row.get("task_id"),
        "repository": row.get("repository"),
        "base_commit_hash": row.get("base_commit_hash"),
        "author_model": row.get("author_model", "claude-opus-4-8"),
        "test_model": row.get("test_model", "gpt-5.6-sol"),
        "rl_rollout_enabled": False,
        "artifacts": row.get("artifacts", []),
        "usage": row.get("usage", {}),
        "qa": compact_qa,
        "errors": [],
        "pipeline_version": row.get("pipeline_version", "2.0"),
    })


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    output_dir = (args.output_dir or root / "state").resolve()
    manifest_rows = load_jsonl(root / "registry/task_manifest.jsonl")
    if not manifest_rows:
        raise SystemExit("runtime manifest is missing or empty; import/initialize state first")
    rows = [compact_row(row, root) for row in sorted(manifest_rows, key=lambda item: str(item.get("slot")))]
    candidates = [scrub(row) for row in load_jsonl(root / "registry/repository-candidates.jsonl")]
    write_jsonl(output_dir / "manifest.seed.jsonl", rows)
    write_jsonl(output_dir / "repository-candidates.seed.jsonl", candidates)
    metadata = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest_rows": len(rows),
        "finalized_rows": sum(row.get("status") == "finalized" for row in rows),
        "candidate_rows": len(candidates),
        "portable": True,
        "transient_stages_reset": True,
        "source": "registry/task_manifest.jsonl",
    }
    (output_dir / "state.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), **metadata}, ensure_ascii=False))


if __name__ == "__main__":
    main()
