#!/usr/bin/env python3
"""Aggregate authoring and QA usage without counting cache tokens twice."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    events = args.root / "registry/production-events.jsonl"
    totals = {"events": 0, "input": 0, "cache": 0, "output": 0, "steps": 0, "cost_usd": 0.0}
    if events.exists():
        for line in events.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            usage = row.get("usage", {})
            totals["events"] += 1
            for key in ("input", "cache", "output", "steps"):
                value = usage.get(key)
                if isinstance(value, (int, float)):
                    totals[key] += value
            if isinstance(usage.get("cost_usd"), (int, float)):
                totals["cost_usd"] += usage["cost_usd"]
    totals["uncached_input"] = totals["input"] - totals["cache"]
    totals["total_context"] = totals["input"] + totals["output"]
    print(json.dumps(totals, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
