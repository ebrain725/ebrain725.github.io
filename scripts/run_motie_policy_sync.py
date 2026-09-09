#!/usr/bin/env python3
"""Run the industrial-ministry collector with throttled, resilient transport."""

from __future__ import annotations

import random
import time

import sync_motie_official_history as collector

_original_request_text = collector.request_text
_last_request_started = 0.0


def resilient_request_text(url: str, *, attempts: int = 6) -> str:
    global _last_request_started
    last_error: Exception | None = None
    for attempt in range(1, max(3, attempts) + 1):
        elapsed = time.monotonic() - _last_request_started
        minimum_interval = 1.4 if attempt == 1 else min(10.0, 2.5 * attempt)
        if elapsed < minimum_interval:
            time.sleep(minimum_interval - elapsed)
        if attempt > 1:
            time.sleep(min(20.0, attempt * 2.5) + random.uniform(0.2, 0.9))
        _last_request_started = time.monotonic()
        try:
            return _original_request_text(url, attempts=1)
        except Exception as exc:  # transient disconnects vary by Python/http stack
            last_error = exc
            print(
                f"산업부 연결 재시도 {attempt}/{max(3, attempts)}: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
    raise RuntimeError(f"산업부 페이지 수집 재시도 소진: {url}: {last_error}")


collector.request_text = resilient_request_text

if __name__ == "__main__":
    raise SystemExit(collector.main())
