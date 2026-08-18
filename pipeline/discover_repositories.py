#!/usr/bin/env python3
"""Discover popular GitHub repositories for task authoring.

The collector intentionally makes only one search request per language.  A
repository is used directly by the authoring agent; release metadata is not a
prerequisite and is therefore not fetched with one request per repository.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

LANGUAGES = ("typescript", "go", "python", "javascript", "rust")


def get_json(url: str, *, retries: int = 3) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "deepswe-task-author"})
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(2 ** attempt)
    assert last_error is not None
    raise last_error


def write_rows(output: Path, rows: list[dict]) -> None:
    """Persist progress after each language so discovery can resume safely."""
    temporary = output.with_suffix(".jsonl.tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--per-language", type=int, default=12)
    parser.add_argument("--max-size-kb", type=int, default=30000)
    parser.add_argument("--append", action="store_true")
    parser.add_argument("--since", default=(date.today() - timedelta(days=180)).isoformat())
    args = parser.parse_args()

    output = args.root / "registry/repository-candidates.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    if args.append and output.is_file():
        rows = [
            json.loads(line)
            for line in output.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    for language in LANGUAGES:
        query = (
            f"language:{language} stars:>=1000 pushed:>={args.since} "
            f"size:100..{args.max_size_kb} archived:false fork:false"
        )
        params = urllib.parse.urlencode({"q": query, "sort": "stars", "order": "desc", "per_page": args.per_language})
        try:
            search = get_json(f"https://api.github.com/search/repositories?{params}")
        except Exception as exc:
            rows.append({"language": language, "search_query": query, "search_error": str(exc)})
            write_rows(output, rows)
            continue
        for item in search.get("items", []):
            row = {
                "language": language,
                "full_name": item["full_name"],
                "html_url": item["html_url"],
                "clone_url": item["clone_url"],
                "default_branch": item.get("default_branch"),
                "stars": item.get("stargazers_count"),
                "forks": item.get("forks_count"),
                "open_issues": item.get("open_issues_count"),
                "size_kb": item.get("size"),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "pushed_at": item.get("pushed_at"),
                "license": (item.get("license") or {}).get("spdx_id"),
                "description": item.get("description"),
                "search_query": query,
            }
            rows.append(row)
            time.sleep(0.1)
        write_rows(output, rows)

    deduped: dict[str, dict] = {}
    for row in rows:
        full_name = row.get("full_name")
        if not full_name:
            continue
        deduped[full_name] = {**deduped.get(full_name, {}), **row}
    write_rows(output, list(deduped.values()))
    print(json.dumps({"candidates": len(deduped), "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
