#!/usr/bin/env python3
"""Policy collector compatibility module with append-only radar retention.

The core collector intentionally focuses on the current collection window. This
wrapper keeps the published radar append-only across routine runs:

* previously accepted news are retained through ``news-history.json``;
* accumulated official policy/notice records are retained from the current
  publication and the dedicated history files;
* institution schedules are merged conservatively and exact evidence duplicates
  are removed regardless of event-type routing.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from sync_policies_core import *  # noqa: F401,F403
import news_retention as _retention
import sync_policies_core as _core

POLICY_PATH = _retention.POLICY_PATH
POLICY_HISTORY_PATHS = (
    _retention.ROOT / "public" / "data" / "policy-official-history.json",
    _retention.ROOT / "public" / "data" / "motie-policy-history.json",
)
LIST_FIELDS = {
    "matchedKeywords",
    "matchedFields",
    "eventTypes",
    "eventTypeIds",
    "eventCategories",
    "eventCategoryIds",
    "topicIds",
    "matchedTopics",
    "topicGroups",
    "keywords",
    "topics",
    "sources",
    "sourceUrls",
    "sourceItemIds",
}
LONG_TEXT_FIELDS = {"summary", "description", "evidence", "content", "insight"}
REQUIRED_POLICY_FIELDS = ("title", "publishedAt", "source", "sourceType")


def _load_document(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return document if isinstance(document, dict) else {}


def _list_values(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_values = value
    elif value in (None, ""):
        raw_values = []
    else:
        raw_values = [value]
    result: list[str] = []
    for raw in raw_values:
        text = _retention.clean_text(raw)
        if text and text not in result:
            result.append(text)
    return result


def _merge_records(previous: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Merge two representations while preferring richer/newer candidate fields."""
    merged = dict(previous)
    for key, value in candidate.items():
        if key in LIST_FIELDS:
            merged[key] = list(
                dict.fromkeys([*_list_values(merged.get(key)), *_list_values(value)])
            )
            continue
        if key in LONG_TEXT_FIELDS:
            if len(_retention.clean_text(value)) > len(
                _retention.clean_text(merged.get(key))
            ):
                merged[key] = value
            continue
        if key == "duplicateCount":
            try:
                merged[key] = max(int(merged.get(key) or 1), int(value or 1))
            except (TypeError, ValueError):
                pass
            continue
        if value not in (None, "", [], {}):
            merged[key] = value
    return merged


def _valid_non_news(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and not _retention.is_news(item)
        and all(_retention.clean_text(item.get(field)) for field in REQUIRED_POLICY_FIELDS)
    )


def _non_news_rows(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in document.get("items", []) if _valid_non_news(item)]


def _policy_item_keys(item: dict[str, Any]) -> list[str]:
    section = _retention.clean_text(
        item.get("section") or item.get("sourceType") or "other"
    ).lower()
    source = _retention.clean_text(item.get("source")).lower()
    published = _retention.clean_text(
        item.get("publishedAt") or item.get("date")
    )[:10]
    title = _retention.normalized_title(item.get("title"))
    url = _retention.canonical_url(item.get("url"))
    source_id = _retention.clean_text(
        item.get("sourceItemId") or item.get("sourceId") or item.get("id")
    )
    keys: list[str] = []
    if source_id:
        keys.append(f"{section}|id|{source_id}")
    if url:
        keys.append(f"{section}|url|{url}")
    if published and title:
        keys.append(f"{section}|{source}|date-title|{published}|{title}")
    return list(dict.fromkeys(keys))


class _PolicyIndex:
    def __init__(self) -> None:
        self.items: list[dict[str, Any] | None] = []
        self.index: dict[str, int] = {}

    def add(self, raw: Any) -> bool:
        if not _valid_non_news(raw):
            return False
        item = dict(raw)
        keys = _policy_item_keys(item)
        matches = sorted({self.index[key] for key in keys if key in self.index})
        if not matches:
            position = len(self.items)
            self.items.append(item)
        else:
            position = matches[0]
            self.items[position] = _merge_records(self.items[position] or {}, item)
            for duplicate_position in matches[1:]:
                duplicate = self.items[duplicate_position]
                if duplicate is not None:
                    self.items[position] = _merge_records(
                        self.items[position] or {}, duplicate
                    )
                    self.items[duplicate_position] = None
            item = self.items[position] or item
        for key in _policy_item_keys(item):
            self.index[key] = position
        return True

    def values(self) -> list[dict[str, Any]]:
        return [item for item in self.items if item is not None]


def _unique_non_news(documents: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    index = _PolicyIndex()
    for document in documents:
        for item in _non_news_rows(document):
            index.add(item)
    return sorted(
        index.values(),
        key=lambda item: (
            _retention.clean_text(
                item.get("publishedAt") or item.get("date")
            )[:10],
            _retention.clean_text(item.get("title")),
        ),
        reverse=True,
    )


def _news_snapshot(documents: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    index = _retention.NewsIndex()
    for document in documents:
        for item in document.get("items", []):
            if _retention.is_valid_news(item):
                index.add(item)
    return _retention.sort_news(index.values())


def _normalized_schedule_evidence(value: Any) -> str:
    text = _retention.clean_text(value).lower()
    text = re.sub(r"(?:무단\s*전재|재배포\s*금지|기자\s*[가-힣]{2,5})", " ", text)
    return re.sub(r"[^0-9a-z가-힣]+", "", text)[:1200]


def _schedule_keys(item: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    source_id = _retention.clean_text(
        item.get("sourceItemId") or item.get("sourceId") or item.get("id")
    )
    url = _retention.canonical_url(item.get("url"))
    evidence = _normalized_schedule_evidence(item.get("evidence"))
    if source_id:
        keys.append(f"id|{source_id}")
    if url:
        keys.append(f"url|{url}")
    if len(evidence) >= 40:
        keys.append(f"evidence|{evidence}")
    date = _retention.clean_text(item.get("startDate"))[:10]
    time = _retention.clean_text(item.get("startTime"))
    organizer = _retention.normalized_title(item.get("organizer"))
    title = _retention.normalized_title(item.get("title"))
    if date and title:
        keys.append(f"event|{date}|{time}|{organizer}|{title}")
    return list(dict.fromkeys(keys))


def _dedupe_schedules(rows: Iterable[Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any] | None] = []
    index: dict[str, int] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        keys = _schedule_keys(item)
        matches = sorted({index[key] for key in keys if key in index})
        if not matches:
            position = len(items)
            items.append(item)
        else:
            position = matches[0]
            items[position] = _merge_records(items[position] or {}, item)
            for duplicate_position in matches[1:]:
                duplicate = items[duplicate_position]
                if duplicate is not None:
                    items[position] = _merge_records(items[position] or {}, duplicate)
                    items[duplicate_position] = None
            item = items[position] or item
        for key in _schedule_keys(item):
            index[key] = position

    result = [item for item in items if item is not None]
    result.sort(
        key=lambda item: (
            _retention.clean_text(item.get("startDate"))[:10],
            _retention.clean_text(item.get("startTime")),
            _retention.clean_text(item.get("title")),
        ),
        reverse=True,
    )
    seen_evidence: set[str] = set()
    for item in result:
        evidence = _normalized_schedule_evidence(item.get("evidence"))
        if len(evidence) >= 40 and evidence in seen_evidence:
            raise RuntimeError("기관일정 동일 본문 중복 제거에 실패했습니다.")
        if evidence:
            seen_evidence.add(evidence)
    return result


def _write_policy(document: dict[str, Any]) -> None:
    _retention.atomic_write(POLICY_PATH, document)


def main() -> int:
    current_before = _load_document(POLICY_PATH)
    history_documents = [_load_document(path) for path in POLICY_HISTORY_PATHS]
    news_history_before = _load_document(_retention.HISTORY_PATH)

    previous_non_news = _unique_non_news(
        [*history_documents, current_before]
    )
    previous_news = _news_snapshot([news_history_before, current_before])
    previous_schedules = [
        dict(item)
        for item in current_before.get("institutionSchedules", [])
        if isinstance(item, dict)
    ]

    result = _core.main()
    if result not in (None, 0):
        return int(result)

    generated = _load_document(POLICY_PATH)
    generated_non_news = _unique_non_news([generated])
    non_news_index = _PolicyIndex()
    for item in previous_non_news:
        non_news_index.add(item)
    for item in generated_non_news:
        non_news_index.add(item)
    final_non_news = sorted(
        non_news_index.values(),
        key=lambda item: (
            _retention.clean_text(
                item.get("publishedAt") or item.get("date")
            )[:10],
            _retention.clean_text(item.get("title")),
        ),
        reverse=True,
    )
    if len(final_non_news) < len(previous_non_news):
        raise RuntimeError(
            "append-only 정책자료 감소 감지: "
            f"최종 {len(final_non_news)}건 < 기존 {len(previous_non_news)}건"
        )

    generated_news = [
        dict(item)
        for item in generated.get("items", [])
        if _retention.is_valid_news(item)
    ]
    generated["items"] = [*final_non_news, *generated_news]
    generated["institutionSchedules"] = _dedupe_schedules(
        [*previous_schedules, *generated.get("institutionSchedules", [])]
    )
    generated["policyRetention"] = {
        "policy": "append-only",
        "status": "verified",
        "previousNonNewsCount": len(previous_non_news),
        "generatedNonNewsCount": len(generated_non_news),
        "finalNonNewsCount": len(final_non_news),
        "previousScheduleCount": len(previous_schedules),
        "finalScheduleCount": len(generated["institutionSchedules"]),
    }
    _write_policy(generated)

    audit = _retention.run(
        POLICY_PATH,
        _retention.HISTORY_PATH,
        _retention.AUDIT_PATH,
        _retention.SETTINGS_PATH,
        recover=False,
        maximum_commits=1,
    )
    final_document = _load_document(POLICY_PATH)
    final_news_count = int(audit["counts"]["finalNewsCount"])
    if final_news_count < len(previous_news):
        raise RuntimeError(
            "append-only 뉴스 감소 감지: "
            f"최종 {final_news_count}건 < 기존 {len(previous_news)}건"
        )
    final_non_news_count = sum(
        1 for item in final_document.get("items", []) if _valid_non_news(item)
    )
    if final_non_news_count < len(previous_non_news):
        raise RuntimeError(
            "최종 정책자료 검증 실패: "
            f"최종 {final_non_news_count}건 < 기존 {len(previous_non_news)}건"
        )
    final_schedules = _dedupe_schedules(
        final_document.get("institutionSchedules", [])
    )
    if len(final_schedules) != len(final_document.get("institutionSchedules", [])):
        final_document["institutionSchedules"] = final_schedules
        _write_policy(final_document)

    print(
        "APPEND_ONLY_POLICY_RADAR_RETAINED="
        f"news:{final_news_count} "
        f"non_news:{final_non_news_count} "
        f"schedules:{len(final_schedules)} "
        f"new_news:{max(0, final_news_count - len(previous_news))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
