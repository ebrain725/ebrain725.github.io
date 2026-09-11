#!/usr/bin/env python3
"""Run the policy collector and restore the append-only news archive."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(script: str, *arguments: str) -> None:
    command = [sys.executable, str(ROOT / "scripts" / script), *arguments]
    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode != 0:
        raise SystemExit(f"실행 실패({completed.returncode}): {' '.join(command)}")


def main() -> int:
    run("sync_policies.py")
    run("news_retention.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
