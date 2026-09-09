#!/usr/bin/env python3
"""Run the industrial-ministry collector with throttled, redundant curl transport."""

from __future__ import annotations

import random
import subprocess
import time
import urllib.parse

import sync_motie_official_history as collector

_last_request_started = 0.0


def request_candidates(url: str) -> list[tuple[str, bool]]:
    """Try both ministry hostnames and both forced/default IP transports."""
    parsed = urllib.parse.urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    hosts = [hostname]
    if hostname == "www.motir.go.kr":
        hosts.append("motir.go.kr")
    elif hostname == "motir.go.kr":
        hosts.append("www.motir.go.kr")

    candidates: list[tuple[str, bool]] = []
    for force_ipv4 in (True, False):
        for host in hosts:
            netloc = host
            if parsed.port:
                netloc = f"{host}:{parsed.port}"
            candidate = urllib.parse.urlunsplit(
                (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
            )
            pair = (candidate, force_ipv4)
            if pair not in candidates:
                candidates.append(pair)
    return candidates or [(url, True), (url, False)]


def resilient_request_text(url: str, *, attempts: int = 8) -> str:
    global _last_request_started
    last_error = ""
    candidates = request_candidates(url)
    total_attempts = max(len(candidates), attempts)

    for attempt in range(1, total_attempts + 1):
        elapsed = time.monotonic() - _last_request_started
        minimum_interval = 1.8 if attempt == 1 else min(12.0, 2.5 * attempt)
        if elapsed < minimum_interval:
            time.sleep(minimum_interval - elapsed)
        if attempt > 1:
            time.sleep(min(16.0, attempt * 1.8) + random.uniform(0.2, 0.9))
        _last_request_started = time.monotonic()

        candidate_url, force_ipv4 = candidates[(attempt - 1) % len(candidates)]
        command = ["curl"]
        if force_ipv4:
            command.append("-4")
        command.extend(
            [
                "--http1.1",
                "--location",
                "--compressed",
                "--fail-with-body",
                "--silent",
                "--show-error",
                "--connect-timeout",
                "15",
                "--max-time",
                "75",
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
                candidate_url,
            ]
        )
        completed = subprocess.run(command, capture_output=True, check=False)
        if completed.returncode == 0 and len(completed.stdout) >= 5_000:
            return completed.stdout.decode("utf-8", errors="replace")

        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        mode = "IPv4" if force_ipv4 else "default-IP"
        host = urllib.parse.urlsplit(candidate_url).hostname or "unknown"
        last_error = (
            f"host={host}, mode={mode}, curl={completed.returncode}, "
            f"bytes={len(completed.stdout)}, stderr={stderr[:500]}"
        )
        print(
            f"산업부 연결 재시도 {attempt}/{total_attempts}: {last_error}",
            flush=True,
        )

    raise RuntimeError(f"산업부 페이지 수집 재시도 소진: {url}: {last_error}")


def validate_audited_history(document: dict, start_date: str) -> dict:
    """Allow a verified zero-match notice board while requiring press results."""
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
