#!/usr/bin/env python3
"""Check whether a freshly cloned checkout can run the DeepSWE production line.

The doctor never prints credentials and never mutates the repository.  It is
safe to run on a new machine before importing the portable state snapshot.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def command_version(command: str, args: list[str] | None = None) -> tuple[bool, str]:
    executable = shutil.which(command)
    if not executable:
        return False, "not found"
    try:
        result = subprocess.run(
            [executable, *(args or ["--version"])],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, type(exc).__name__
    output = (result.stdout or result.stderr).strip().splitlines()
    return result.returncode == 0, (output[0][:180] if output else f"exit {result.returncode}")


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def has_real_secret(value: str) -> bool:
    return bool(value) and not value.startswith("REPLACE_WITH_") and value not in {"changeme", "your-key-here"}


def check(root: Path, env_file: Path, require_docker: bool, require_harbor: bool) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []

    def add(name: str, ok: bool, detail: str, severity: str = "error") -> None:
        checks.append({"name": name, "status": "pass" if ok else severity, "detail": detail})

    add("git", *command_version("git"))
    python_ok, python_detail = command_version("python3", ["--version"])
    add("python3", python_ok and sys.version_info >= (3, 11), python_detail)

    docker_ok, docker_detail = command_version("docker", ["--version"])
    if docker_ok:
        try:
            daemon = subprocess.run([shutil.which("docker") or "docker", "info"], capture_output=True, text=True, timeout=30, check=False)
            docker_ok = daemon.returncode == 0
            if not docker_ok:
                docker_detail = "CLI present but Docker daemon is unavailable"
        except subprocess.TimeoutExpired:
            docker_ok = False
            docker_detail = "Docker daemon check timed out"
    add("docker", docker_ok, docker_detail, "error" if require_docker else "warn")

    harbor_ok, harbor_detail = command_version("harbor", ["--version"])
    add("harbor", harbor_ok, harbor_detail, "error" if require_harbor else "warn")

    add("repository root", (root / ".git").is_dir(), str(root))
    manifest = root / "registry" / "task_manifest.jsonl"
    candidates = root / "registry" / "repository-candidates.jsonl"
    add("runtime manifest", manifest.is_file() and manifest.stat().st_size > 0, str(manifest), "warn")
    add("repository candidates", candidates.is_file() and candidates.stat().st_size > 0, str(candidates), "warn")
    add("portable manifest seed", (root / "state" / "manifest.seed.jsonl").is_file(), "state/manifest.seed.jsonl", "warn")
    add("portable candidate seed", (root / "state" / "repository-candidates.seed.jsonl").is_file(), "state/repository-candidates.seed.jsonl", "warn")

    if not env_file.is_file():
        add("provider env", False, f"missing: {env_file}")
        values: dict[str, str] = {}
    else:
        values = read_env(env_file)
        mode = values.get("HIDDEN_TEST_API_MODE", "responses")
        strong_ok = has_real_secret(values.get("ANTHROPIC_API_KEY", ""))
        weak_ok = has_real_secret(values.get("PACKY_RESPONSES_API_KEY", ""))
        add("strong API key", strong_ok, "present (value hidden)" if strong_ok else "ANTHROPIC_API_KEY missing or placeholder")
        add("weak API key", weak_ok, "present (value hidden)" if weak_ok else "PACKY_RESPONSES_API_KEY missing or placeholder")
        add("strong endpoint", bool(values.get("ANTHROPIC_BASE_URL")), values.get("ANTHROPIC_BASE_URL", "missing"))
        add("weak endpoint", bool(values.get("PACKY_RESPONSES_BASE_URL")), values.get("PACKY_RESPONSES_BASE_URL", "missing"))
        add("hidden-test API mode", mode in {"responses", "chat_completions"}, mode)

    try:
        usage = shutil.disk_usage(root)
        free_gb = usage.free / (1024**3)
        add("disk space", free_gb >= 20, f"{free_gb:.1f} GiB free (20 GiB recommended minimum)", "warn")
    except OSError as exc:
        add("disk space", False, repr(exc), "warn")

    if manifest.is_file():
        try:
            rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
            slots = {str(row.get("slot")) for row in rows}
            add("manifest format", len(rows) == len(slots) and all(slot.startswith("task-") for slot in slots), f"{len(rows)} unique slots")
        except (OSError, json.JSONDecodeError) as exc:
            add("manifest format", False, repr(exc))

    return checks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--env-file", type=Path, help="provider env file outside the repository")
    parser.add_argument("--require-harbor", action="store_true")
    parser.add_argument("--no-docker", action="store_true", help="only check code/config; do not require a running Docker daemon")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    root = args.root.resolve()
    env_file = (args.env_file or Path(os.environ.get("DEEPSWE_ENV_FILE", root.parent / "packy.env"))).expanduser().resolve()
    checks = check(root, env_file, require_docker=not args.no_docker, require_harbor=args.require_harbor)
    failed = [item for item in checks if item["status"] == "error"]
    payload = {"root": str(root), "env_file": str(env_file), "ok": not failed, "checks": checks}
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in checks:
            print(f"[{item['status'].upper():5}] {item['name']}: {item['detail']}")
        print(f"doctor: {'PASS' if not failed else 'FAIL'} ({len(failed)} blocking checks)")
    raise SystemExit(0 if not failed else 1)


if __name__ == "__main__":
    main()
