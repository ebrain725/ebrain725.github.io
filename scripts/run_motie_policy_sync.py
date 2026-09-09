#!/usr/bin/env python3
"""Run the industrial-ministry collector with throttled curl transport."""

from __future__ import annotations

import random
import subprocess
import time
import urllib.parse

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


def validate_audited_history(document: dict, start_date: str) -> dict:
    """Allow a verified zero-match notice board while requiring press results.

    The collector still executes all title/content queries for both official
    boards. A zero notice count is therefore a valid result, not a skipped
    source, when its source audit is complete.
    """
    items = document.get("items", [])
    if not isinstance(items, list):
        raise RuntimeError("산업부 이력 items가 배열이 아닙니다.")
    seen: set[str] = set()
    counts = {section: 0 for section in collector.MOTIE_SECTIONS}
    for index, raw in enumerate(items):
        item = collector.normalize_item(raw, start_date)
        if item is None:
            raise RuntimeError(f"산업부 이력 items[{index}] 형식 오류")
        key = collector.item_key(item)
        if key in seen:
            raise RuntimeError(f"산업부 이력 중복: {key}")
        seen.add(key)
        counts[item["section"]] += 1
        host = (urllib.parse.urlsplit(item["url"]).hostname or "").lower()
        if host not in {"www.motir.go.kr", "motir.go.kr"}:
            raise RuntimeError(f"산업부 공식 도메인이 아닌 URL: {item['url']}")
    if counts.get("motie_press", 0) <= 0:
        raise RuntimeError(f"산업부 보도자료 수집 결과가 비었습니다: {counts}")
    return {
        "itemCount": len(items),
        "counts": counts,
        "duplicateKeys": 0,
        "zeroMatchSections": [
            section for section, value in counts.items() if value == 0
        ],
    }


collector.request_text = resilient_request_text
collector.validate_history = validate_audited_history

if __name__ == "__main__":
    raise SystemExit(collector.main())
