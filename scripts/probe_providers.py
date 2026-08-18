#!/usr/bin/env python3
"""Safely probe configured strong/weak providers without printing secrets."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"\'')
    return values


def probe(name: str, url: str, headers: dict[str, str], payload: dict[str, object]) -> None:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read(512)
            print(f"{name}: HTTP {response.status}, elapsed={time.monotonic() - started:.2f}s, bytes={len(body)}")
    except urllib.error.HTTPError as exc:
        body = exc.read(512).decode("utf-8", "replace").replace("\n", " ")
        print(f"{name}: HTTP {exc.code}, elapsed={time.monotonic() - started:.2f}s, error={body[:180]}")
    except Exception as exc:  # noqa: BLE001 - probe must report infrastructure errors
        print(f"{name}: {type(exc).__name__}, elapsed={time.monotonic() - started:.2f}s, error={str(exc)[:180]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    args = parser.parse_args()
    env = load_env(args.env_file.resolve())
    strong_key = env.get("ANTHROPIC_API_KEY", "")
    weak_key = env.get("PACKY_RESPONSES_API_KEY", "")
    strong_payload = {
        "model": env.get("ANTHROPIC_MODEL", "claude-opus-4-8"),
        "max_tokens": 8,
        "messages": [{"role": "user", "content": "Reply with OK."}],
    }
    weak_payload = {
        "model": env.get("HIDDEN_TEST_MODEL", "gpt-5.6-sol"),
        "max_tokens": 8,
        "messages": [{"role": "user", "content": "Reply with OK."}],
    }
    providers = {
        "moli": ("https://moliapi.com/v1", "https://moliapi.com/v1/messages", "https://moliapi.com/v1/chat/completions"),
        "packy": ("https://www.packyapi.com/v1", "https://www.packyapi.com/v1/messages", "https://www.packyapi.com/v1/chat/completions"),
    }
    configured = env.get("ANTHROPIC_BASE_URL", "")
    configured_host = configured.split("/v1", 1)[0].rstrip("/")
    for name, (_, default_strong, default_weak) in providers.items():
        if configured_host and name == "moli":
            strong_url = configured if configured.endswith("/messages") else configured_host + "/v1/messages"
        else:
            strong_url = default_strong
        weak_base = env.get("PACKY_RESPONSES_BASE_URL", "") if name == "moli" else ""
        weak_url = (weak_base.rstrip("/") + "/chat/completions") if weak_base else default_weak
        probe(f"{name} strong", strong_url, {"x-api-key": strong_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}, strong_payload)
        probe(f"{name} weak", weak_url, {"Authorization": f"Bearer {weak_key}", "Content-Type": "application/json"}, weak_payload)


if __name__ == "__main__":
    main()
