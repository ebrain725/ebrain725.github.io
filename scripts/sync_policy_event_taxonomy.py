#!/usr/bin/env python3
"""Apply the shared 38-event taxonomy to climate, industry and KRX official history."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import urllib.parse
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import sync_climate_policy_expanded as climate
import sync_motie_policy_expanded as motie
from policy_event_taxonomy import (
    classify_item,
    climate_candidate_terms,
    dedupe,
    event_counts,
    industry_query_terms,
    is_classified,
    load_taxonomy,
    scope_counts,
)

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_HISTORY_PATH = ROOT / "public" / "data" / "policy-official-history.json"
MOTIE_HISTORY_PATH = ROOT / "public" / "data" / "motie-policy-history.json"
POLICY_PATH = ROOT / "public" / "data" / "policies.json"
SUMMARY_PATH = ROOT / "public" / "data" / "policy-event-taxonomy-summary.json"
LEGACY_OFFICIAL_SUMMARY_PATH = ROOT / "public" / "data" / "policy-official-history-summary.json"
TAXONOMY_PATH = ROOT / "config" / "policy_event_taxonomy.json"
KST = ZoneInfo("Asia/Seoul")
OFFICIAL_SECTIONS = {"press", "notice", "krx_notice"}
MOTIE_SECTIONS = {"motie_press", "motie_notice"}
ALL_OFFICIAL_SECTIONS = OFFICIAL_SECTIONS | MOTIE_SECTIONS
SOURCE_HOSTS = {
    "press": {"mcee.go.kr", "www.mcee.go.kr"},
    "notice": {"mcee.go.kr", "www.mcee.go.kr"},
    "krx_notice": {"ets.krx.co.kr"},
    "motie_press": {"motir.go.kr", "www.motir.go.kr"},
    "motie_notice": {"motir.go.kr", "www.motir.go.kr"},
}


def atomic_write(path: Path, document: dict[str, Any]) -> bool:
    content = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)
    return True


def load_document(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("items"), list):
        raise RuntimeError(f"JSON 형식 오류: {path}")
    return document


def record_key(item: dict[str, Any]) -> str:
    section = str(item.get("section", "")).strip().lower()
    board = str(item.get("sourceBoard", "")).strip()
    source_id = str(item.get("sourceId", "")).strip()
    if source_id:
        return f"{section}|{board}|{source_id}"
    url = str(item.get("url", "")).strip()
    if url:
        return f"{section}|url|{url}"
    title = re.sub(r"[^0-9a-z가-힣]+", "", str(item.get("title", "")).lower())
    return f"{section}|{str(item.get('publishedAt', ''))[:10]}|{title}"


def merge_record(
    previous: dict[str, Any],
    candidate: dict[str, Any],
    taxonomy: dict[str, Any],
    *,
    dedicated_ets_board: bool = False,
) -> dict[str, Any]:
    old_summary = str(previous.get("summary", ""))
    new_summary = str(candidate.get("summary", ""))
    richer = candidate if len(new_summary) >= len(old_summary) else previous
    other = previous if richer is candidate else candidate
    merged = dict(richer)
    for key, value in other.items():
        if key not in merged or merged.get(key) in (None, "", [], {}):
            merged[key] = value
    merged["matchedKeywords"] = dedupe([
        *(previous.get("matchedKeywords") or []),
        *(candidate.get("matchedKeywords") or []),
    ])
    merged["matchedFields"] = dedupe([
        *(previous.get("matchedFields") or []),
        *(candidate.get("matchedFields") or []),
    ])
    return classify_item(merged, taxonomy, dedicated_ets_board=dedicated_ets_board)


def section_scope_counts(items: Iterable[dict[str, Any]]) -> dict[str, dict[str, int]]:
    result = {
        section: {"direct": 0, "related": 0}
        for section in sorted(ALL_OFFICIAL_SECTIONS)
    }
    for item in items:
        section = str(item.get("section", "")).strip().lower()
        scope = str(item.get("keywordScope", "")).strip().lower()
        if section in result and scope in result[section]:
            result[section][scope] += 1
    return result


def coverage(items: list[dict[str, Any]], sections: set[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for section in sorted(sections):
        selected = [item for item in items if item.get("section") == section]
        dates = [str(item.get("publishedAt", ""))[:10] for item in selected]
        result[section] = {
            "records": len(selected),
            "earliest": min(dates) if dates else None,
            "latest": max(dates) if dates else None,
            "scopeCounts": scope_counts(selected),
            "eventCounts": event_counts(selected),
        }
    return result


def enrich_existing(
    raw_items: Iterable[Any],
    taxonomy: dict[str, Any],
    *,
    sections: set[str],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        section = str(raw.get("section", "")).strip().lower()
        if section not in sections:
            continue
        enriched = classify_item(
            raw,
            taxonomy,
            dedicated_ets_board=section == "krx_notice",
        )
        if is_classified(enriched):
            result.append(enriched)
    return result


def run_climate(
    taxonomy: dict[str, Any],
    *,
    start_date: str,
    full: bool,
    lookback_days: int,
) -> tuple[dict[str, Any], dict[str, int]]:
    history = load_document(OFFICIAL_HISTORY_PATH)
    previous_counts = Counter(
        str(item.get("section", "")).strip().lower()
        for item in history["items"]
        if isinstance(item, dict)
    )
    today = datetime.now(KST).date()
    query_start = (
        start_date
        if full
        else max(date.fromisoformat(start_date), today - timedelta(days=lookback_days)).isoformat()
    )
    direct_terms, title_terms = climate_candidate_terms(taxonomy)
    collected: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []

    for source in climate.load_climate_sources():
        items, audit = climate.collect_source(source, direct_terms, title_terms, query_start)
        classified = []
        for item in items:
            enriched = classify_item(item, taxonomy)
            if is_classified(enriched):
                classified.append(enriched)
        audit = dict(audit)
        audit["taxonomyVersion"] = taxonomy["version"]
        audit["taxonomyMatchedCount"] = len(classified)
        audit["taxonomyEventCounts"] = event_counts(classified)
        collected.extend(classified)
        audits.append(audit)

    merged: dict[str, dict[str, Any]] = {}
    for item in enrich_existing(history["items"], taxonomy, sections=OFFICIAL_SECTIONS):
        section = str(item.get("section", "")).strip().lower()
        if full and section in {"press", "notice"}:
            continue
        merged[record_key(item)] = item

    for item in collected:
        key = record_key(item)
        merged[key] = merge_record(merged[key], item, taxonomy) if key in merged else item

    final_items = sorted(
        merged.values(),
        key=lambda item: (
            str(item.get("publishedAt", "")),
            str(item.get("title", "")),
            str(item.get("sourceId", "")),
        ),
        reverse=True,
    )
    counts = Counter(str(item.get("section", "")) for item in final_items)
    for section in OFFICIAL_SECTIONS:
        if counts[section] <= 0:
            raise RuntimeError(f"기후부·KRX 구분이 비었습니다: {section}")
        if counts[section] < previous_counts[section]:
            raise RuntimeError(
                f"정책 이벤트 확대 후 {section} 건수가 감소했습니다: "
                f"{counts[section]} < {previous_counts[section]}"
            )

    preserved_krx_audits = [
        audit for audit in history.get("sourceAudit", [])
        if isinstance(audit, dict)
        and (
            str(audit.get("section", "")).lower() == "krx_notice"
            or "한국거래소" in str(audit.get("source", ""))
        )
    ]
    generated_at = datetime.now(KST).isoformat(timespec="seconds")
    updated = dict(history)
    updated["items"] = final_items
    updated["generatedAt"] = generated_at
    updated["collectionMode"] = "full" if full else "incremental"
    updated["queryStartDate"] = query_start
    updated["queryEndDate"] = today.isoformat()
    updated["sourceAudit"] = [*audits, *preserved_krx_audits]
    updated["coverage"] = coverage(final_items, OFFICIAL_SECTIONS)
    updated["policyEventTaxonomy"] = {
        "version": taxonomy["version"],
        "configFile": "config/policy_event_taxonomy.json",
        "eventCount": len(taxonomy["events"]),
        "collectionMode": updated["collectionMode"],
        "queryStartDate": query_start,
        "queryEndDate": today.isoformat(),
        "climateRule": "전체 게시판을 순회해 direct는 제목·본문, related는 제목＋필수 문맥으로 판정",
        "krxRule": "배출권시장 전용 공지 전체를 direct로 보존하고 세부 이벤트를 태깅",
    }
    atomic_write(OFFICIAL_HISTORY_PATH, updated)
    return updated, dict(previous_counts)


def run_industry(
    taxonomy: dict[str, Any],
    *,
    start_date: str,
    full: bool,
    lookback_days: int,
) -> tuple[dict[str, Any], dict[str, int]]:
    history = load_document(MOTIE_HISTORY_PATH)
    previous_counts = Counter(
        str(item.get("section", "")).strip().lower()
        for item in history["items"]
        if isinstance(item, dict)
    )
    today = datetime.now(KST).date()
    query_start = (
        start_date
        if full
        else max(date.fromisoformat(start_date), today - timedelta(days=lookback_days)).isoformat()
    )
    title_queries, content_queries = industry_query_terms(taxonomy)
    content_set = set(content_queries)
    broad_queries = [query for query in title_queries if query not in content_set]

    collected: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    for source in motie.SOURCES:
        items, audit = motie.collect_source(
            source,
            content_queries,
            broad_queries,
            query_start,
            today.isoformat(),
        )
        classified = []
        for item in items:
            enriched = classify_item(item, taxonomy)
            if is_classified(enriched):
                classified.append(enriched)
        audit = dict(audit)
        audit["taxonomyVersion"] = taxonomy["version"]
        audit["taxonomyMatchedCount"] = len(classified)
        audit["taxonomyEventCounts"] = event_counts(classified)
        collected.extend(classified)
        audits.append(audit)

    merged: dict[str, dict[str, Any]] = {}
    for item in enrich_existing(history["items"], taxonomy, sections=MOTIE_SECTIONS):
        merged[record_key(item)] = item

    for item in collected:
        key = record_key(item)
        merged[key] = merge_record(merged[key], item, taxonomy) if key in merged else item

    final_items = sorted(
        merged.values(),
        key=lambda item: (
            str(item.get("publishedAt", "")),
            str(item.get("title", "")),
            str(item.get("sourceBoard", "")),
        ),
        reverse=True,
    )
    counts = Counter(str(item.get("section", "")) for item in final_items)
    for section in MOTIE_SECTIONS:
        if counts[section] <= 0:
            raise RuntimeError(f"산업부 구분이 비었습니다: {section}")
        if counts[section] < previous_counts[section]:
            raise RuntimeError(
                f"정책 이벤트 확대 후 {section} 건수가 감소했습니다: "
                f"{counts[section]} < {previous_counts[section]}"
            )

    generated_at = datetime.now(KST).isoformat(timespec="seconds")
    updated = dict(history)
    updated["schemaVersion"] = "2.0"
    updated["items"] = final_items
    updated["generatedAt"] = generated_at
    updated["collectionMode"] = "full-delta-search" if full else "incremental-delta-search"
    updated["queryStartDate"] = query_start
    updated["queryEndDate"] = today.isoformat()
    updated["sourceAudit"] = audits
    updated["coverage"] = coverage(final_items, MOTIE_SECTIONS)
    updated["policyEventTaxonomy"] = {
        "version": taxonomy["version"],
        "configFile": "config/policy_event_taxonomy.json",
        "eventCount": len(taxonomy["events"]),
        "collectionMode": updated["collectionMode"],
        "queryStartDate": query_start,
        "queryEndDate": today.isoformat(),
        "titleQueries": title_queries,
        "contentQueries": content_queries,
        "rule": "기존 30개 이력＋추가 검색. 제목은 38개 이벤트 문맥판정, 본문은 배출권 직접어만 검색",
    }
    atomic_write(MOTIE_HISTORY_PATH, updated)
    return updated, dict(previous_counts)


def rebuild_policy(
    taxonomy: dict[str, Any],
    official: dict[str, Any],
    industry: dict[str, Any],
) -> dict[str, Any]:
    policy = load_document(POLICY_PATH)
    others = [
        item for item in policy["items"]
        if isinstance(item, dict)
        and str(item.get("section", "")).strip().lower() not in ALL_OFFICIAL_SECTIONS
    ]
    combined = [*others, *official["items"], *industry["items"]]
    deduped: dict[str, dict[str, Any]] = {}
    for item in combined:
        deduped[record_key(item)] = item
    items = sorted(
        deduped.values(),
        key=lambda item: (str(item.get("publishedAt", "")), str(item.get("title", ""))),
        reverse=True,
    )
    generated_at = datetime.now(KST).isoformat(timespec="seconds")
    policy["items"] = items
    policy["officialHistory"] = {
        "file": "data/policy-official-history.json",
        "requestedStartDate": official.get("requestedStartDate", "2015-01-01"),
        "generatedAt": official.get("generatedAt"),
        "coverage": official.get("coverage"),
        "itemCount": len(official["items"]),
    }
    policy["motieOfficialHistory"] = {
        "file": "data/motie-policy-history.json",
        "requestedStartDate": industry.get("requestedStartDate", "2015-01-01"),
        "generatedAt": industry.get("generatedAt"),
        "coverage": industry.get("coverage"),
        "itemCount": len(industry["items"]),
    }
    official_items = [*official["items"], *industry["items"]]
    policy["policyEventTaxonomy"] = {
        "version": taxonomy["version"],
        "configFile": "config/policy_event_taxonomy.json",
        "summaryFile": "data/policy-event-taxonomy-summary.json",
        "eventCount": len(taxonomy["events"]),
        "generatedAt": generated_at,
        "officialItemCount": len(official_items),
        "scopeCounts": scope_counts(official_items),
        "eventCounts": event_counts(official_items),
    }
    atomic_write(POLICY_PATH, policy)
    return policy


def validate_all(
    taxonomy: dict[str, Any],
    official: dict[str, Any],
    industry: dict[str, Any],
    policy: dict[str, Any],
    previous_counts: dict[str, int],
) -> dict[str, Any]:
    official_items = [*official["items"], *industry["items"]]
    seen: set[str] = set()
    duplicates = 0
    invalid_dates = 0
    invalid_domains = 0
    unclassified = 0
    counts = Counter()

    for item in official_items:
        section = str(item.get("section", "")).strip().lower()
        counts[section] += 1
        key = record_key(item)
        if key in seen:
            duplicates += 1
        seen.add(key)
        published = str(item.get("publishedAt", ""))[:10]
        if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", published) or published < "2015-01-01":
            invalid_dates += 1
        host = (urllib.parse.urlsplit(str(item.get("url", ""))).hostname or "").lower()
        if host not in SOURCE_HOSTS.get(section, set()):
            invalid_domains += 1
        if not is_classified(item):
            unclassified += 1

    if duplicates or invalid_dates or invalid_domains or unclassified:
        raise RuntimeError(
            "정책 이벤트 전체검증 실패: "
            f"중복={duplicates}, 날짜={invalid_dates}, 도메인={invalid_domains}, 미분류={unclassified}"
        )
    for section in ALL_OFFICIAL_SECTIONS:
        if counts[section] <= 0:
            raise RuntimeError(f"공식자료 구분이 비었습니다: {section}")
        if counts[section] < int(previous_counts.get(section, 0)):
            raise RuntimeError(
                f"공식자료 건수가 감소했습니다: {section} "
                f"{counts[section]} < {previous_counts.get(section, 0)}"
            )

    policy_official = [
        item for item in policy["items"]
        if str(item.get("section", "")).strip().lower() in ALL_OFFICIAL_SECTIONS
    ]
    if len(policy_official) != len(official_items):
        raise RuntimeError(
            f"대시보드 공식자료와 이력 건수가 다릅니다: {len(policy_official)} != {len(official_items)}"
        )
    if len(taxonomy["events"]) != 38:
        raise RuntimeError("정책 이벤트 분류 수가 38개가 아닙니다.")

    return {
        "officialItemCount": len(official_items),
        "sectionCounts": {section: counts[section] for section in sorted(ALL_OFFICIAL_SECTIONS)},
        "scopeCounts": section_scope_counts(official_items),
        "eventCounts": event_counts(official_items),
        "duplicateKeys": duplicates,
        "invalidDates": invalid_dates,
        "invalidDomains": invalid_domains,
        "unclassified": unclassified,
        "dashboardOfficialCount": len(policy_official),
    }


def write_summary(
    taxonomy: dict[str, Any],
    validation: dict[str, Any],
    official: dict[str, Any],
    industry: dict[str, Any],
    previous_counts: dict[str, int],
    *,
    full: bool,
) -> dict[str, Any]:
    counts = validation["sectionCounts"]
    generated_at = datetime.now(KST).isoformat(timespec="seconds")
    now = datetime.now(KST)
    summary = {
        "schemaVersion": "1.0",
        "taxonomyVersion": taxonomy["version"],
        "generatedAt": generated_at,
        "requestedStartDate": taxonomy.get("requestedStartDate", "2015-01-01"),
        "collectionMode": "full" if full else "incremental",
        "eventCount": len(taxonomy["events"]),
        "events": [
            {"id": event["id"], "label": event["label"], "priority": event.get("priority")}
            for event in sorted(
                taxonomy["events"],
                key=lambda value: (int(value.get("priority", 999)), str(value.get("label", ""))),
            )
        ],
        "sectionCounts": counts,
        "previousSectionCounts": {
            section: int(previous_counts.get(section, 0))
            for section in sorted(ALL_OFFICIAL_SECTIONS)
        },
        "addedCounts": {
            section: counts[section] - int(previous_counts.get(section, 0))
            for section in sorted(ALL_OFFICIAL_SECTIONS)
        },
        "scopeCounts": validation["scopeCounts"],
        "eventCounts": validation["eventCounts"],
        "sourceCoverage": {**official.get("coverage", {}), **industry.get("coverage", {})},
        "validation": validation,
        "lastRunDateKst": now.date().isoformat(),
        "lastRunSlot": f"{now.date().isoformat()}-{'AM' if now.hour < 15 else 'PM'}",
    }
    atomic_write(SUMMARY_PATH, summary)

    legacy = {}
    if LEGACY_OFFICIAL_SUMMARY_PATH.exists():
        try:
            legacy = json.loads(LEGACY_OFFICIAL_SUMMARY_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            legacy = {}
    climate_coverage = official.get("coverage", {})
    legacy_counts = {
        section: int((climate_coverage.get(section) or {}).get("records", 0))
        for section in ("press", "notice", "krx_notice")
    }
    legacy.update({
        "verifiedAt": generated_at,
        "officialItemCount": sum(legacy_counts.values()),
        "counts": legacy_counts,
        "earliest": {
            section: (climate_coverage.get(section) or {}).get("earliest")
            for section in legacy_counts
        },
        "latest": {
            section: (climate_coverage.get(section) or {}).get("latest")
            for section in legacy_counts
        },
        "directCounts": {
            section: int(((climate_coverage.get(section) or {}).get("scopeCounts") or {}).get("direct", 0))
            for section in ("press", "notice")
        },
        "relatedCounts": {
            section: int(((climate_coverage.get(section) or {}).get("scopeCounts") or {}).get("related", 0))
            for section in ("press", "notice")
        },
        "policyEventTaxonomy": {
            "version": taxonomy["version"],
            "eventCount": len(taxonomy["events"]),
            "summaryFile": "data/policy-event-taxonomy-summary.json",
            "eventCounts": event_counts(official.get("items", [])),
        },
    })
    atomic_write(LEGACY_OFFICIAL_SUMMARY_PATH, legacy)
    return summary


def self_test() -> None:
    taxonomy = load_taxonomy(TAXONOMY_PATH)
    samples = [
        {"title": "배출권 유상경매 응찰률과 낙찰가 공개", "summary": ""},
        {"title": "폭염으로 전력수요 역대 최대", "summary": ""},
        {"title": "일반 주택 화재 예방대책", "summary": ""},
        {"title": "철강업계 감산과 가동률 조정", "summary": ""},
        {"title": "EU ETS 소송 판결", "summary": ""},
    ]
    expected = [
        "낙찰가·응찰배수",
        "폭염·한파·전력수요",
        None,
        "감산·가동률 조정",
        "EU ETS·EUA",
    ]
    for sample, label in zip(samples, expected):
        result = classify_item(sample, taxonomy)
        if label is None:
            assert not is_classified(result), result
        else:
            assert label in result["eventTypes"], result
    print("POLICY_EVENT_SYNC_SELF_TEST=PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default="2015-01-01")
    parser.add_argument("--lookback-days", type=int, default=60)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", args.start_date):
        raise SystemExit("--start-date는 YYYY-MM-DD 형식이어야 합니다.")
    if not 1 <= args.lookback_days <= 366:
        raise SystemExit("--lookback-days는 1~366이어야 합니다.")

    taxonomy = load_taxonomy(TAXONOMY_PATH)
    previous_official = load_document(OFFICIAL_HISTORY_PATH)
    previous_motie = load_document(MOTIE_HISTORY_PATH)
    previous_counts = Counter(
        str(item.get("section", "")).strip().lower()
        for item in [*previous_official["items"], *previous_motie["items"]]
        if isinstance(item, dict)
    )

    official, _ = run_climate(
        taxonomy,
        start_date=args.start_date,
        full=args.full,
        lookback_days=args.lookback_days,
    )
    industry, _ = run_industry(
        taxonomy,
        start_date=args.start_date,
        full=args.full,
        lookback_days=args.lookback_days,
    )
    policy = rebuild_policy(taxonomy, official, industry)
    validation = validate_all(taxonomy, official, industry, policy, dict(previous_counts))
    summary = write_summary(
        taxonomy,
        validation,
        official,
        industry,
        dict(previous_counts),
        full=args.full,
    )
    result = {
        "taxonomyVersion": taxonomy["version"],
        "collectionMode": summary["collectionMode"],
        "eventCount": summary["eventCount"],
        "sectionCounts": summary["sectionCounts"],
        "addedCounts": summary["addedCounts"],
        "scopeCounts": summary["scopeCounts"],
        "topEventCounts": dict(list(summary["eventCounts"].items())[:15]),
        "summaryFile": "public/data/policy-event-taxonomy-summary.json",
        "policyBytes": POLICY_PATH.stat().st_size,
    }
    print("POLICY_EVENT_TAXONOMY_RESULT=" + json.dumps(result, ensure_ascii=False))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
