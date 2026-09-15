#!/usr/bin/env python3
"""Collect current policy-radar data while retaining all previously accepted history."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from sync_policies_core import *  # noqa: F401,F403
import merge_official_policy_history as _climate_history
import news_retention as _news_retention
import sync_policies_core as _core

POLICY_PATH = _news_retention.POLICY_PATH
POLICY_HISTORY_PATHS = (
    _news_retention.ROOT / "public" / "data" / "policy-official-history.json",
    _news_retention.ROOT / "public" / "data" / "motie-policy-history.json",
)
LIST_FIELDS = {
    "matchedKeywords", "matchedFields", "eventTypes", "eventTypeIds",
    "eventCategories", "eventCategoryIds", "topicIds", "matchedTopics",
    "topicGroups", "keywords", "topics", "sources", "sourceUrls",
    "sourceItemIds",
}
LONG_FIELDS = {"summary", "description", "evidence", "content", "insight"}
REQUIRED_FIELDS = ("title", "publishedAt", "source", "sourceType")


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _values(value: Any) -> list[str]:
    raw = value if isinstance(value, list) else ([] if value in (None, "") else [value])
    return list(dict.fromkeys(
        _news_retention.clean_text(item)
        for item in raw
        if _news_retention.clean_text(item)
    ))


def _merge(previous: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    merged = dict(previous)
    for key, value in candidate.items():
        if key in LIST_FIELDS:
            merged[key] = list(dict.fromkeys([*_values(merged.get(key)), *_values(value)]))
        elif key in LONG_FIELDS:
            if len(_news_retention.clean_text(value)) > len(
                _news_retention.clean_text(merged.get(key))
            ):
                merged[key] = value
        elif key == "duplicateCount":
            try:
                merged[key] = max(int(merged.get(key) or 1), int(value or 1))
            except (TypeError, ValueError):
                pass
        elif value not in (None, "", [], {}):
            merged[key] = value
    return merged


def _valid_non_news(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and not _news_retention.is_news(item)
        and all(_news_retention.clean_text(item.get(field)) for field in REQUIRED_FIELDS)
    )


def _section(item: dict[str, Any]) -> str:
    explicit = _news_retention.clean_text(item.get("section")).lower()
    if explicit:
        return explicit
    source = _news_retention.clean_text(item.get("source")).lower()
    url = _news_retention.clean_text(item.get("url")).lower()
    if "한국거래소" in source or "ets.krx.co.kr" in url:
        return "krx_notice"
    if "산업부" in source or "motir.go.kr" in url:
        return "motie_press" if "보도" in source else "motie_notice"
    return "press" if "보도" in source else "notice"


def _item_key(item: dict[str, Any]) -> str:
    """Return one strong key only, avoiding cross-board and KRX URL collisions."""
    section = _section(item)
    published = _news_retention.clean_text(
        item.get("publishedAt") or item.get("date")
    )[:10]
    title = _news_retention.normalized_title(item.get("title"))
    source_id = _news_retention.clean_text(item.get("sourceId"))
    source_board = _news_retention.clean_text(item.get("sourceBoard"))

    if section == "krx_notice":
        # KRX detail URLs use fragments. The generic URL canonicalizer removes
        # fragments, so the dedicated stable-key implementation must be used.
        return _climate_history._stable_key(item)
    if section in {"motie_press", "motie_notice"} and source_board and source_id:
        return f"{section}|board-id|{source_board}|{source_id}"

    url = _news_retention.canonical_url(item.get("url"))
    if url:
        return f"{section}|url|{url}"
    return f"{section}|date-title|{published}|{title}"


def _unique_non_news(documents: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for document in documents:
        for raw in document.get("items", []):
            if not _valid_non_news(raw):
                continue
            item = dict(raw)
            item["section"] = _section(item)
            key = _item_key(item)
            merged[key] = _merge(merged[key], item) if key in merged else item
    return sorted(
        merged.values(),
        key=lambda item: (
            _news_retention.clean_text(item.get("publishedAt"))[:10],
            _news_retention.clean_text(item.get("title")),
        ),
        reverse=True,
    )


def _unique_news(documents: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    index = _news_retention.NewsIndex()
    for document in documents:
        for item in document.get("items", []):
            if _news_retention.is_valid_news(item):
                index.add(item)
    return _news_retention.sort_news(index.values())


def _normalized_evidence(value: Any) -> str:
    text = _news_retention.clean_text(value).lower()
    text = re.sub(r"(?:무단\s*전재|재배포\s*금지|기자\s*[가-힣]{2,5})", " ", text)
    return re.sub(r"[^0-9a-z가-힣]+", "", text)[:1200]


def _schedule_key(item: dict[str, Any]) -> str:
    evidence = _normalized_evidence(item.get("evidence"))
    if len(evidence) >= 40:
        return f"evidence|{evidence}"
    url = _news_retention.canonical_url(item.get("url"))
    if url:
        return f"url|{url}"
    date = _news_retention.clean_text(item.get("startDate"))[:10]
    clock = _news_retention.clean_text(item.get("startTime"))
    organizer = _news_retention.normalized_title(item.get("organizer"))
    title = _news_retention.normalized_title(item.get("title"))
    return f"event|{date}|{clock}|{organizer}|{title}"


def _unique_schedules(rows: Iterable[Any]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        key = _schedule_key(item)
        merged[key] = _merge(merged[key], item) if key in merged else item
    result = sorted(
        merged.values(),
        key=lambda item: (
            _news_retention.clean_text(item.get("startDate"))[:10],
            _news_retention.clean_text(item.get("startTime")),
            _news_retention.clean_text(item.get("title")),
        ),
        reverse=True,
    )
    seen: set[str] = set()
    for item in result:
        evidence = _normalized_evidence(item.get("evidence"))
        if len(evidence) >= 40 and evidence in seen:
            raise RuntimeError("기관일정 동일 본문 중복 제거 실패")
        if evidence:
            seen.add(evidence)
    return result


def _write(document: dict[str, Any]) -> None:
    _news_retention.atomic_write(POLICY_PATH, document)


def _retention_snapshot() -> list[dict[str, Any]]:
    return _unique_news([
        _load(_news_retention.HISTORY_PATH),
        _load(POLICY_PATH),
    ])


def main() -> int:
    before = _load(POLICY_PATH)
    histories = [_load(path) for path in POLICY_HISTORY_PATHS]
    archived_news = _load(_news_retention.HISTORY_PATH)

    previous_official = _unique_non_news([*histories, before])
    previous_news = _unique_news([archived_news, before])
    previous_schedules = [
        dict(item)
        for item in before.get("institutionSchedules", [])
        if isinstance(item, dict)
    ]

    result = _core.main()
    if result not in (None, 0):
        return int(result)

    generated = _load(POLICY_PATH)
    final_official = _unique_non_news([*histories, before, generated])
    generated_news = [
        dict(item)
        for item in generated.get("items", [])
        if _news_retention.is_valid_news(item)
    ]
    final_schedules = _unique_schedules([
        *previous_schedules,
        *generated.get("institutionSchedules", []),
    ])

    if len(final_official) < len(previous_official):
        raise RuntimeError(
            f"누적 정책자료 감소: {len(final_official)} < {len(previous_official)}"
        )

    generated["items"] = [*final_official, *generated_news]
    generated["institutionSchedules"] = final_schedules
    generated["policyRetention"] = {
        "policy": "append-only",
        "status": "verified",
        "previousNonNewsCount": len(previous_official),
        "generatedNonNewsCount": len(_unique_non_news([generated])),
        "finalNonNewsCount": len(final_official),
        "previousScheduleCount": len(previous_schedules),
        "finalScheduleCount": len(final_schedules),
    }
    _write(generated)

    audit = _news_retention.run(
        POLICY_PATH,
        _news_retention.HISTORY_PATH,
        _news_retention.AUDIT_PATH,
        _news_retention.SETTINGS_PATH,
        recover=False,
        maximum_commits=1,
    )
    final = _load(POLICY_PATH)
    final_news_count = int((audit.get("counts") or {}).get("finalNewsCount", -1))
    final_official_count = sum(1 for item in final.get("items", []) if _valid_non_news(item))
    if final_news_count < len(previous_news):
        raise RuntimeError(f"누적 뉴스 감소: {final_news_count} < {len(previous_news)}")
    if final_official_count < len(previous_official):
        raise RuntimeError(
            f"최종 정책자료 감소: {final_official_count} < {len(previous_official)}"
        )

    section_counts: dict[str, int] = {}
    for item in final.get("items", []):
        if _valid_non_news(item):
            section = _section(item)
            section_counts[section] = section_counts.get(section, 0) + 1
    print(
        "APPEND_ONLY_POLICY_RADAR_RETAINED="
        + json.dumps(
            {
                "news": final_news_count,
                "official": final_official_count,
                "schedules": len(final_schedules),
                "newNews": max(0, final_news_count - len(previous_news)),
                "sections": section_counts,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
