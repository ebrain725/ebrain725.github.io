#!/usr/bin/env python3
"""Shared 38-event taxonomy for official K-ETS policy radar materials."""

from __future__ import annotations

import argparse
import html
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "config" / "policy_event_taxonomy.json"


def clean_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(str(value or ""))).lower()
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[\u200b-\u200d\u2060\ufeff]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def compact_text(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", clean_text(value))


def dedupe(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def load_taxonomy(path: Path | str = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    config_path = Path(path)
    document = json.loads(config_path.read_text(encoding="utf-8"))
    events = document.get("events")
    if not isinstance(events, list) or len(events) != 38:
        raise RuntimeError(f"정책 이벤트 분류는 38개여야 합니다: {len(events or [])}")
    ids = [str(event.get("id", "")).strip() for event in events]
    labels = [str(event.get("label", "")).strip() for event in events]
    if any(not value for value in ids + labels):
        raise RuntimeError("정책 이벤트 ID 또는 표시명이 비어 있습니다.")
    if len(ids) != len(set(ids)) or len(labels) != len(set(labels)):
        raise RuntimeError("정책 이벤트 ID 또는 표시명이 중복됩니다.")
    for event in events:
        clauses = event.get("clauses")
        if not isinstance(clauses, list) or not clauses:
            raise RuntimeError(f"이벤트 판정절이 없습니다: {event.get('label')}")
        for clause in clauses:
            if clause.get("scope") not in {"direct", "related"}:
                raise RuntimeError(f"직접성 값 오류: {event.get('label')}")
            fields = clause.get("fields")
            terms = clause.get("terms")
            if not isinstance(fields, list) or not fields or not isinstance(terms, list) or not terms:
                raise RuntimeError(f"이벤트 판정 필드 또는 키워드가 없습니다: {event.get('label')}")
    return document


def _ascii_pattern(term: str) -> re.Pattern[str] | None:
    normalized = clean_text(term)
    if not normalized or re.search(r"[가-힣]", normalized):
        return None
    tokens = re.findall(r"[a-z0-9]+", normalized)
    if not tokens:
        return None
    separator = r"[\s\-‐‑‒–—−_./]*"
    expression = separator.join(re.escape(token) for token in tokens)
    return re.compile(rf"(?<![a-z0-9]){expression}(?![a-z0-9])", re.IGNORECASE)


def term_found(term: str, value: Any) -> bool:
    visible = clean_text(value)
    if not visible:
        return False
    ascii_pattern = _ascii_pattern(term)
    if ascii_pattern is not None:
        return bool(ascii_pattern.search(visible))
    needle = compact_text(term)
    return bool(needle and needle in compact_text(visible))


def _field_values(item: dict[str, Any]) -> dict[str, str]:
    evidence_values = item.get("matchedKeywords", [])
    if isinstance(evidence_values, str):
        evidence_values = [evidence_values]
    if not isinstance(evidence_values, list):
        evidence_values = []
    return {
        "title": str(item.get("title") or ""),
        "content": str(item.get("summary") or item.get("content") or ""),
        "evidence": " ".join(str(value) for value in evidence_values if str(value).strip()),
        "source": f"{item.get('source', '')} {item.get('section', '')}",
    }


def _clause_match(clause: dict[str, Any], fields: dict[str, str]) -> dict[str, Any] | None:
    allowed_fields = [str(field) for field in clause.get("fields", []) if str(field) in fields]
    matched_terms: list[str] = []
    matched_fields: list[str] = []

    for term in clause.get("terms", []):
        found_fields = [field for field in allowed_fields if term_found(str(term), fields[field])]
        if not found_fields:
            continue
        matched_terms.append(str(term))
        matched_fields.extend(found_fields)

    if not matched_terms:
        return None

    context_text = " ".join(fields.values())
    context_any = [str(value) for value in clause.get("contextAny", []) if str(value).strip()]
    if context_any and not any(term_found(term, context_text) for term in context_any):
        return None

    for group in clause.get("contextAllGroups", []):
        alternatives = [str(value) for value in group if str(value).strip()]
        if alternatives and not any(term_found(term, context_text) for term in alternatives):
            return None

    exclude_any = [str(value) for value in clause.get("excludeAny", []) if str(value).strip()]
    if exclude_any and any(term_found(term, context_text) for term in exclude_any):
        return None

    return {
        "scope": str(clause["scope"]),
        "matchedTerms": dedupe(matched_terms),
        "matchedFields": dedupe(matched_fields),
    }


def classify_item(
    item: dict[str, Any],
    taxonomy: dict[str, Any],
    *,
    dedicated_ets_board: bool = False,
) -> dict[str, Any]:
    fields = _field_values(item)
    matches: list[dict[str, Any]] = []

    for event in sorted(
        taxonomy["events"],
        key=lambda value: (int(value.get("priority", 999)), str(value.get("label", ""))),
    ):
        event_terms: list[str] = []
        event_fields: list[str] = []
        scopes: list[str] = []
        for clause in event.get("clauses", []):
            result = _clause_match(clause, fields)
            if result is None:
                continue
            scopes.append(result["scope"])
            event_terms.extend(result["matchedTerms"])
            event_fields.extend(result["matchedFields"])
        if not scopes:
            continue
        matches.append(
            {
                "id": str(event["id"]),
                "label": str(event["label"]),
                "scope": "direct" if "direct" in scopes else "related",
                "matchedTerms": dedupe(event_terms),
                "matchedFields": dedupe(event_fields),
            }
        )

    if not matches and dedicated_ets_board:
        generic = next(event for event in taxonomy["events"] if event["id"] == "ets_system")
        matches = [{
            "id": str(generic["id"]),
            "label": str(generic["label"]),
            "scope": "direct",
            "matchedTerms": ["배출권시장 전용 게시판"],
            "matchedFields": ["source"],
        }]

    enriched = dict(item)
    if not matches:
        for key in (
            "eventMatches", "eventIds", "eventTypes", "eventPrimary",
            "keywordScope", "taxonomyVersion",
        ):
            enriched.pop(key, None)
        return enriched

    enriched["eventMatches"] = matches
    enriched["eventIds"] = [match["id"] for match in matches]
    enriched["eventTypes"] = [match["label"] for match in matches]
    enriched["eventPrimary"] = matches[0]["label"]
    enriched["keywordScope"] = (
        "direct" if any(match["scope"] == "direct" for match in matches) else "related"
    )
    enriched["matchedKeywords"] = dedupe(
        term for match in matches for term in match["matchedTerms"]
    )
    enriched["matchedFields"] = dedupe(
        field for match in matches for field in match["matchedFields"]
    )
    enriched["taxonomyVersion"] = str(taxonomy.get("version", ""))
    return enriched


def is_classified(item: dict[str, Any]) -> bool:
    return bool(item.get("eventTypes") and item.get("eventPrimary"))


def climate_candidate_terms(taxonomy: dict[str, Any]) -> tuple[list[str], list[str]]:
    title_and_content: list[str] = []
    title_only: list[str] = []
    for event in taxonomy["events"]:
        for clause in event.get("clauses", []):
            fields = set(str(value) for value in clause.get("fields", []))
            terms = [str(value) for value in clause.get("terms", [])]
            if "content" in fields or "evidence" in fields:
                title_and_content.extend(terms)
            else:
                title_only.extend(terms)
    direct = dedupe(title_and_content)
    broad = [term for term in dedupe(title_only) if term not in set(direct)]
    return direct, broad


def industry_query_terms(taxonomy: dict[str, Any]) -> tuple[list[str], list[str]]:
    collection = taxonomy.get("collection") or {}
    title = dedupe(collection.get("industryDeltaTitleQueries", []))
    content = dedupe(collection.get("industryDeltaContentQueries", []))
    if not title or not content:
        raise RuntimeError("산업부 증분 검색어 설정이 비어 있습니다.")
    return title, content


def event_counts(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for item in items:
        for label in item.get("eventTypes", []) or []:
            counter[str(label)] += 1
    return dict(sorted(counter.items(), key=lambda pair: (-pair[1], pair[0])))


def scope_counts(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    counter = Counter(
        str(item.get("keywordScope", ""))
        for item in items
        if str(item.get("keywordScope", "")) in {"direct", "related"}
    )
    return {"direct": counter["direct"], "related": counter["related"]}


def self_test() -> None:
    taxonomy = load_taxonomy()
    samples = [
        (
            {"title": "2027년도 배출권 유상경매 물량과 일정 공고", "summary": ""},
            "유상경매 물량·일정", "direct",
        ),
        (
            {"title": "석탄발전 감축을 반영한 전력수급기본계획 확정", "summary": ""},
            "석탄발전", "related",
        ),
        (
            {"title": "석유화학 공장 화재로 생산시설 가동중단", "summary": ""},
            "화재·폭발·침수·가동중단", "related",
        ),
        (
            {"title": "산불 화재 예방 캠페인", "summary": ""},
            None, None,
        ),
        (
            {"title": "EU ETS 개편과 EUA 공급조정", "summary": ""},
            "EU ETS·EUA", "direct",
        ),
    ]
    for item, expected_event, expected_scope in samples:
        result = classify_item(item, taxonomy)
        if expected_event is None:
            assert not is_classified(result), result
        else:
            assert expected_event in result["eventTypes"], result
            assert result["keywordScope"] == expected_scope, result
    direct, broad = climate_candidate_terms(taxonomy)
    title_queries, content_queries = industry_query_terms(taxonomy)
    assert len(taxonomy["events"]) == 38
    assert len(direct) > 20 and len(broad) > 20
    assert len(title_queries) >= 40 and len(content_queries) >= 10
    print(
        "POLICY_EVENT_TAXONOMY_SELF_TEST=PASS "
        f"events={len(taxonomy['events'])} climate={len(direct)}/{len(broad)} "
        f"industry={len(title_queries)}/{len(content_queries)}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    taxonomy = load_taxonomy(args.config)
    print(json.dumps({
        "version": taxonomy["version"],
        "eventCount": len(taxonomy["events"]),
        "labels": [event["label"] for event in taxonomy["events"]],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
