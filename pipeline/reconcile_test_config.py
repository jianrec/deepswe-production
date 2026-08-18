#!/usr/bin/env python3
"""Re-materialize test IDs from the exact commands stored in a task package."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

from qwen_tests import collect_test_ids, command_test_ids, command_test_paths, public_tests


def run(command: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, check=check, capture_output=True, text=True, timeout=900)


def command_blocks(test_sh: str) -> tuple[str, str]:
    feature = re.search(
        r"set \+e\s*\n\(\s*\n(.*?)\n\) > /logs/verifier/feature-native\.log.*?feature_rc=\$\?",
        test_sh,
        re.S,
    )
    regression = re.search(
        r"feature_rc=\$\?.*?\n\(\s*\n(.*?)\n\) > /logs/verifier/regression-native\.log.*?regression_rc=\$\?",
        test_sh,
        re.S,
    )
    if not feature or not regression:
        raise ValueError("unable to locate feature/regression command blocks in tests/test.sh")
    return feature.group(1), regression.group(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("task", type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    package = args.task.resolve()
    slot = package.name
    rows = [json.loads(line) for line in (root / "registry/task_manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    row = next(item for item in rows if item["slot"] == slot)
    repository = root / "workspaces/repositories" / row["repository"].replace("/", "__")
    verifier = root / "workspaces/verifier" / f"{slot}-reconcile"
    if verifier.exists():
        run(["git", "worktree", "remove", "--force", str(verifier)], repository, check=False)
        if verifier.exists():
            shutil.rmtree(verifier)
    run(["git", "worktree", "prune"], repository)
    run(["git", "worktree", "add", "--detach", str(verifier), row["base_commit_hash"]], repository)
    try:
        run(["git", "apply", "--whitespace=nowarn", str(package / "tests/test.patch")], verifier)
        config_path = package / "tests/config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        feature_command, regression_command = command_blocks((package / "tests/test.sh").read_text(encoding="utf-8"))
        all_paths = public_tests(verifier, row["language"])
        configured_feature_paths = sorted({str(node_id).rsplit(".", 1)[0] for node_id in config.get("f2p_node_ids", [])})
        feature_paths = command_test_paths(configured_feature_paths, feature_command, row["language"])
        feature_ids = command_test_ids(
            collect_test_ids(verifier, feature_paths, row["language"], 10**9),
            feature_command,
            row["language"],
        )
        regression_paths = command_test_paths(
            [path for path in all_paths if path not in set(feature_paths)],
            regression_command,
            row["language"],
        )
        regression_ids = command_test_ids(
            collect_test_ids(verifier, regression_paths, row["language"], 10**9, set(feature_ids)),
            regression_command,
            row["language"],
        )
        if not 30 <= len(feature_ids) <= 150:
            raise ValueError(f"reconciled F2P count outside 30..150: {len(feature_ids)}")
        if not 100 <= len(regression_ids) <= 1500:
            raise ValueError(f"reconciled P2P count outside 100..1500: {len(regression_ids)}")
        config["f2p_node_ids"] = feature_ids[:150]
        config["p2p_node_ids"] = regression_ids[:1500]
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"slot": slot, "f2p": len(feature_ids[:150]), "p2p": len(regression_ids[:1500])}, ensure_ascii=False))
    finally:
        run(["git", "worktree", "remove", "--force", str(verifier)], repository, check=False)


if __name__ == "__main__":
    main()
