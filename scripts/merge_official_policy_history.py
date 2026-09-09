#!/usr/bin/env python3
"""Merge the complete official policy archive into the public dashboard payload.

The regular policy collector intentionally keeps a small, recent working set. This
post-processing step preserves every official item collected since 2015 while
leaving the rolling news set and the other policy metadata unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = ROOT / "public" / "data" / "policies.json"
DEFAULT_HISTORY_PATH = ROOT / "public" / "data" / "policy-official-history.json"
KST = ZoneInfo("Asia/Seoul")
VALID_SECTIONS = ("press", "notice", "krx_notice")
SECTION_LABELS = {
    "press": "기후부 보도자료",
    "notice": "기후부 공지사항",
    "krx_notice": "한국거래소 공지사항",
}


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _section(item: dict[str, Any]) -> str:
    explicit = _clean(item.get("section")).lower()
    if explicit in VALID_SECTIONS:
        return explicit
    source = _clean(item.get("source"))
    url = _clean(item.get("url"))
    if "한국거래소" in source or "ets.krx.co.kr" in url:
        return "krx_notice"
    if "보도자료" in source or re.search(
        r"(?:menuId=(?:286|10598)|boardMasterId=(?:1|939))(?:&|$)", url
    ):
        return "press"
    return "notice"


def _canonical_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r";jsessionid=[^?&#]+", "", text, flags=re.IGNORECASE)
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError:
        return text
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    query = [
        (key, val)
        for key, val in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in {
            "maxindexpages",
            "maxpageitems",
            "pageroffset",
            "searchkey",
            "searchvalue",
            "decorator",
        }
    ]
    # KRX uses the fragment as the stable detail identifier, so keep it.
    return urllib.parse.urlunsplit(
        ((parsed.scheme or "https").lower(), host, parsed.path, urllib.parse.urlencode(sorted(query)), parsed.fragment)
    )


def _normalized_title(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _clean(value).lower())


def _stable_key(item: dict[str, Any]) -> str:
    section = _section(item)
    if section == "krx_notice":
        source_id = _clean(item.get("sourceId"))
        if not source_id:
            identifier = _clean(item.get("id"))
            match = re.search(r"(?:krx-ets-|view=)(\d+)", identifier + " " + _clean(item.get("url")))
            source_id = match.group(1) if match else identifier
        if source_id:
            return f"{section}|{source_id}"
    url = _canonical_url(item.get("url"))
    if url:
        return f"{section}|url|{url}"
    return (
        f"{section}|date-title|{_clean(item.get('publishedAt'))}|"
        f"{_normalized_title(item.get('title'))}"
    )


def _merge_item(previous: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    previous_summary = _clean(previous.get("summary"))
    candidate_summary = _clean(candidate.get("summary"))
    richer = candidate if len(candidate_summary) >= len(previous_summary) else previous
    other = previous if richer is candidate else candidate
    merged = dict(other)
    merged.update(richer)
    merged["section"] = _section(merged)
    merged["sourceType"] = "official"
    keywords = []
    for item in (previous, candidate):
        values = item.get("matchedKeywords", [])
        if isinstance(values, list):
            keywords.extend(_clean(value) for value in values if _clean(value))
    merged["matchedKeywords"] = list(dict.fromkeys(keywords))
    for key in (
        "_trustedSearchMatch",
        "_bodyVerificationQueries",
        "_bodyCandidateScore",
        "_detailFetchFailed",
        "impact",
        "impactReason",
        "impactSource",
    ):
        merged.pop(key, None)
    return merged


def _normalize_official(item: Any, start_date: str) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    if _clean(item.get("sourceType")).lower() == "news":
        return None
    published = _clean(item.get("publishedAt"))[:10]
    title = _clean(item.get("title"))
    source = _clean(item.get("source"))
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", published):
        return None
    if published < start_date or not title or not source:
        return None
    normalized = dict(item)
    normalized["publishedAt"] = published
    normalized["title"] = title
    normalized["source"] = source
    normalized["sourceType"] = "official"
    normalized["section"] = _section(normalized)
    normalized["summary"] = _clean(normalized.get("summary")) or "원문에서 세부 내용을 확인하세요."
    normalized["url"] = _clean(normalized.get("url"))
    normalized["id"] = _clean(normalized.get("id")) or hashlib.sha1(
        _stable_key(normalized).encode("utf-8")
    ).hexdigest()[:16]
    for key in (
        "_trustedSearchMatch",
        "_bodyVerificationQueries",
        "_bodyCandidateScore",
        "_detailFetchFailed",
        "impact",
        "impactReason",
        "impactSource",
    ):
        normalized.pop(key, None)
    return normalized


def _coverage(items: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {section: 0 for section in VALID_SECTIONS}
    earliest: dict[str, str | None] = {section: None for section in VALID_SECTIONS}
    latest: dict[str, str | None] = {section: None for section in VALID_SECTIONS}
    for item in items:
        section = _section(item)
        date = _clean(item.get("publishedAt"))[:10]
        counts[section] += 1
        earliest[section] = date if earliest[section] is None else min(str(earliest[section]), date)
        latest[section] = date if latest[section] is None else max(str(latest[section]), date)
    return {
        "itemCount": len(items),
        "counts": counts,
        "earliest": earliest,
        "latest": latest,
        "labels": SECTION_LABELS,
    }


def _atomic_write(path: Path, document: dict[str, Any]) -> bool:
    content = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)
    return True


def merge_files(
    policy_path: Path = DEFAULT_POLICY_PATH,
    history_path: Path = DEFAULT_HISTORY_PATH,
    *,
    start_date: str = "2015-01-01",
) -> dict[str, Any]:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if not isinstance(policy, dict) or not isinstance(policy.get("items"), list):
        raise RuntimeError("policies.json 형식이 올바르지 않습니다.")

    if history_path.exists():
        history = json.loads(history_path.read_text(encoding="utf-8"))
        if not isinstance(history, dict) or not isinstance(history.get("items"), list):
            raise RuntimeError("policy-official-history.json 형식이 올바르지 않습니다.")
    else:
        history = {
            "schemaVersion": "1.0",
            "requestedStartDate": start_date,
            "items": [],
        }

    requested_start = _clean(history.get("requestedStartDate")) or start_date
    requested_start = min(requested_start, start_date)
    merged: dict[str, dict[str, Any]] = {}
    for raw in [*history.get("items", []), *policy.get("items", [])]:
        item = _normalize_official(raw, requested_start)
        if item is None:
            continue
        key = _stable_key(item)
        merged[key] = _merge_item(merged[key], item) if key in merged else item

    official = sorted(
        merged.values(),
        key=lambda item: (
            _clean(item.get("publishedAt")),
            _clean(item.get("title")),
            _clean(item.get("sourceId")),
        ),
        reverse=True,
    )
    current_news = [
        dict(item)
        for item in policy.get("items", [])
        if isinstance(item, dict) and _clean(item.get("sourceType")).lower() == "news"
    ]
    current_news.sort(
        key=lambda item: (_clean(item.get("publishedAt")), _clean(item.get("title"))),
        reverse=True,
    )

    generated_at = datetime.now(KST).isoformat(timespec="seconds")
    coverage = _coverage(official)
    history["schemaVersion"] = "1.0"
    history["requestedStartDate"] = requested_start
    history["generatedAt"] = generated_at
    history["coverage"] = coverage
    history["items"] = official

    policy["officialHistory"] = {
        "file": "data/policy-official-history.json",
        "requestedStartDate": requested_start,
        "generatedAt": generated_at,
        **coverage,
    }
    policy["items"] = sorted(
        [*official, *current_news],
        key=lambda item: (_clean(item.get("publishedAt")), _clean(item.get("title"))),
        reverse=True,
    )

    history_changed = _atomic_write(history_path, history)
    policy_changed = _atomic_write(policy_path, policy)
    return {
        "historyChanged": history_changed,
        "policyChanged": policy_changed,
        "officialCount": len(official),
        "newsCount": len(current_news),
        "coverage": coverage,
        "historyBytes": history_path.stat().st_size,
        "policyBytes": policy_path.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy-path", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--history-path", type=Path, default=DEFAULT_HISTORY_PATH)
    parser.add_argument("--start-date", default="2015-01-01")
    args = parser.parse_args()
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", args.start_date):
        raise SystemExit("--start-date는 YYYY-MM-DD 형식이어야 합니다.")
    result = merge_files(
        args.policy_path,
        args.history_path,
        start_date=args.start_date,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
