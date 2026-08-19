"""Generate the repository-local production rules after the canary milestone."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

try:
    from .filelock import exclusive_lock
except ImportError:  # pragma: no cover - supports direct script execution
    from filelock import exclusive_lock


DEFAULT_THRESHOLD = 15
GUIDE_NAME = "AGENTS.md"
TASK_NAME = re.compile(r"^task-\d+$")


def published_task_slots(root: Path) -> list[str]:
    """Return complete task directories currently present in ``output``.

    Historical baseline tasks do not all contain a QA report, so the
    published artifact shape, rather than the report, is the count source.
    """
    output = root / "output"
    if not output.is_dir():
        return []
    required = ("task.toml", "instruction.md", "solution/solution.patch", "tests/test.sh")
    slots = []
    for task in output.iterdir():
        if not task.is_dir() or not TASK_NAME.fullmatch(task.name):
            continue
        if all((task / relative).is_file() for relative in required):
            slots.append(task.name)
    return sorted(slots)


def _guide_text(count: int, threshold: int) -> str:
    return f"""# DeepSWE Production Agent Rules

This repository produces original DeepSWE/Harbor software-engineering tasks.
The guide was generated after {count} complete tasks reached `output/`; keep it
with the pipeline when merging work from another production computer.

## Non-negotiable task contract

- A task is counted only when it is a complete directory under `output/task-NNNN/`.
- The task must be an original coding issue, not a copy of an open-source PR.
- The reference implementation should touch 7-16 source files and 600-1500
  changed source lines across at least three modules or packages.
- Hidden tests must be public-interface tests: 40-150 F2P cases and at least
  100 real P2P regression cases. Tests must run, not be skipped or made
  vacuous with `|| true`, `exit 0`, network calls, or answer leakage.
- Only a finalized task with passing static validation, NOP, Oracle, and
  mutant QA may be copied into `output/`.

## Required stage order

1. Run `scripts/doctor.py` with an env file outside the repository.
2. Restore the portable registry with `scripts/import_state.py` when needed.
3. Run authoring, reference implementation, and hidden-test stages in order.
4. Run `pipeline/finalize_task.py` with repeated Oracle and mutant QA.
5. Publish with `pipeline/publish_task.py`; validate before cleanup.
6. Commit only source, pipeline code, documentation, and `output/task-NNNN/`.

The strong model (`claude-opus-4-8`) writes the issue and reference patch.
The weak model (`gpt-5.6-sol`) writes hidden tests. Never put either API key
in Git, task artifacts, logs, prompts, or this guide.

## Stable production defaults

- Start with `--batch-size 1 --workers 1`; increase only after a full canary
  task passes on the target computer.
- Treat GitHub, model, and Docker failures as infrastructure failures. Keep
  the manifest state, clean partial clones, retry transient failures, and
  move to another repository candidate instead of retrying one broken source
  forever.
- Keep each task's Docker, worktree, and log cleanup scoped to that task.
  Never run an unbounded global Docker prune during production.
- Use atomic manifest writes and merge published task directories between
  computers. Do not commit credentials or ignored runtime registries.

## Windows and WSL

WSL 2 and Docker Desktop must be running before QA. `HypervisorPresent=True`
is the reliable Windows check when Hyper-V/VBS is active; an old WMI
`VirtualizationFirmwareEnabled=False` value is not sufficient evidence that
BIOS VT-x is disabled.

## Milestone rule

This file is created once at the {threshold}-task milestone by the publish
pipeline. If it already exists, later publishes must leave its contents
unchanged; edit it deliberately in a reviewed commit when the production
contract changes.
"""


def ensure_agent_guide(root: Path, threshold: int = DEFAULT_THRESHOLD) -> dict[str, object]:
    """Create ``AGENTS.md`` once the published-task threshold is reached."""
    if threshold < 1:
        raise ValueError("threshold must be positive")
    root = root.resolve()
    slots = published_task_slots(root)
    guide = root / GUIDE_NAME
    result: dict[str, object] = {
        "count": len(slots),
        "threshold": threshold,
        "path": str(guide),
        "generated": False,
        "existing": guide.is_file(),
    }
    if len(slots) < threshold:
        return result

    lock_path = root / "registry/.agent-guide.lock"
    with exclusive_lock(lock_path):
        if guide.is_file():
            result["existing"] = True
            return result
        content = _guide_text(len(slots), threshold)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=".AGENTS-",
                suffix=".tmp",
                dir=root,
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, guide)
            result["generated"] = True
            result["existing"] = False
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
    return result


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    args = parser.parse_args()
    print(json.dumps(ensure_agent_guide(args.root, args.threshold), ensure_ascii=False))


if __name__ == "__main__":
    main()
