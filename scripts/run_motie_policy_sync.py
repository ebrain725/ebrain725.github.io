#!/usr/bin/env python3
"""Run the industrial-ministry collector with throttled curl transport."""

from __future__ import annotations

import random
import subprocess
import time

import sync_motie_official_history as collector

_last_request_started = 0.0


def resilient_request_text(url: str, *, attempts: int = 6) -> str:
    global _last_request_started
    last_error = ""
    total_attempts = max(3, attempts)
    for attempt in range(1, total_attempts + 1):
        elapsed = time.monotonic() - _last_request_started
        minimum_interval = 1.8 if attempt == 1 else min(12.0, 3.0 * attempt)
        if elapsed < minimum_interval:
            time.sleep(minimum_interval - elapsed)
        if attempt > 1:
            time.sleep(min(20.0, attempt * 2.5) + random.uniform(0.2, 1.0))
        _last_request_started = time.monotonic()

        command = [
            "curl",
            "-4",
            "--http1.1",
            "--location",
            "--compressed",
            "--fail-with-body",
            "--silent",
            "--show-error",
            "--connect-timeout",
            "20",
            "--max-time",
            "90",
            "--retry",
            "1",
            "--retry-delay",
            "2",
            "--retry-all-errors",
            "--user-agent",
            collector.USER_AGENT,
            "--header",
            "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "--header",
            "Accept-Language: ko-KR,ko;q=0.9,en;q=0.6",
            "--header",
            "Connection: close",
            url,
        ]
        completed = subprocess.run(command, capture_output=True, check=False)
        if completed.returncode == 0 and len(completed.stdout) >= 5_000:
            return completed.stdout.decode("utf-8", errors="replace")

        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        last_error = (
            f"curl={completed.returncode}, bytes={len(completed.stdout)}, "
            f"stderr={stderr[:500]}"
        )
        print(
            f"산업부 연결 재시도 {attempt}/{total_attempts}: {last_error}",
            flush=True,
        )

    raise RuntimeError(f"산업부 페이지 수집 재시도 소진: {url}: {last_error}")


collector.request_text = resilient_request_text

if __name__ == "__main__":
    raise SystemExit(collector.main())
