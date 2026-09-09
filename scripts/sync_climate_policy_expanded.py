#!/usr/bin/env python3
"""Expand and preserve climate-ministry policy history with the shared 30-keyword set.

The seven direct ETS keywords are matched against titles and RSS descriptions.
The 23 broader climate, industry, trade and power keywords are accepted only
when they appear in the title. This is the same keyword split used by the
industrial-ministry collector, while keeping false positives from broad body
text under control.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import tempfile
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import backfill_official_policy_history as legacy
import backfill_official_policy_history_v2 as archive
import merge_official_policy_history as merger
import sync_policies as policy_core

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "config" / "settings.json"
KEYWORD_PATH = ROOT / "config" / "motie_policy_keywords.json"
POLICY_PATH = ROOT / "public" / "data" / "policies.json"
HISTORY_PATH = ROOT / "public" / "data" / "policy-official-history.json"
SUMMARY_PATH = ROOT / "public" / "data" / "policy-official-history-summary.json"
START_DATE = "2015-01-01"
DEFAULT_LOOKBACK_DAYS = 60
EXPANSION_VERSION = "2026-09-09-v1-shared-30-keywords"
CLIMATE_SECTIONS = {"press", "notice"}
VALID_SECTIONS = {"press", "notice", "krx_notice"}
MCEE_HOSTS = {"mcee.go.kr", "www.mcee.go.kr"}
KRX_HOSTS = {"ets.krx.co.kr"}


def clean_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(str(value or "")))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalized_match(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", clean_text(value).lower())


def dedupe(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        cleaned = clean_text(value)
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [clean_text(item) for item in value if clean_text(item)]
    if isinstance(value, str) and clean_text(value):
        return [clean_text(value)]
    return []


def atomic_write(path: Path, document: dict[str, Any]) -> bool:
    content = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
        suffix=".tmp",
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)
    return True


def load_keywords() -> tuple[list[str], list[str]]:
    document = json.loads(KEYWORD_PATH.read_text(encoding="utf-8"))
    core = dedupe(document.get("titleAndContent", []))
    broad = [value for value in dedupe(document.get("titleOnly", [])) if value not in core]
    if len(core) != 7 or len(broad) != 23:
        raise RuntimeError(
            f"공유 공식자료 키워드는 핵심 7개·확대 23개여야 합니다: {len(core)}/{len(broad)}"
        )
    return core, broad


def load_climate_sources() -> list[dict[str, Any]]:
    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    sources = [
        source
        for source in settings.get("policySources", [])
        if isinstance(source, dict)
        and str(source.get("type", "")).lower() in CLIMATE_SECTIONS
    ]
    sections = {str(source.get("type", "")).lower() for source in sources}
    if sections != CLIMATE_SECTIONS:
        raise RuntimeError(f"기후부 보도자료·공지사항 출처 설정이 부족합니다: {sections}")
    return sources


def match_fields(
    title: str,
    description: str,
    core: list[str],
    broad: list[str],
) -> tuple[list[str], list[str], str]:
    title_norm = normalized_match(title)
    description_norm = normalized_match(description)
    keywords: list[str] = []
    fields: list[str] = []
    direct = False

    for keyword in core:
        key = normalized_match(keyword)
        in_title = bool(key and key in title_norm)
        in_content = bool(key and key in description_norm)
        if not (in_title or in_content):
            continue
        keywords.append(keyword)
        direct = True
        if in_title:
            fields.append("title")
        if in_content:
            fields.append("content")

    for keyword in broad:
        key = normalized_match(keyword)
        if key and key in title_norm:
            keywords.append(keyword)
            fields.append("title")

    return dedupe(keywords), dedupe(fields), "direct" if direct else "related"


def parse_page(
    root: ET.Element,
    source: dict[str, Any],
    core: list[str],
    broad: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_rows: list[dict[str, Any]] = []
    matched_rows: list[dict[str, Any]] = []
    section = policy_core.source_section(source)

    for xml_item in root.findall(".//item"):
        title = policy_core.clean_html(policy_core.child_text(xml_item, "title"), 240)
        description = policy_core.clean_html(
            policy_core.child_text(xml_item, "description"), 4_000
        )
        link = re.sub(
            r";jsessionid=[^?&#]+",
            "",
            html.unescape(policy_core.child_text(xml_item, "link")),
            flags=re.IGNORECASE,
        ).strip()
        published = legacy.strict_date(
            policy_core.child_text(xml_item, "pubDate")
            or policy_core.child_text(xml_item, "date")
        )
        if not title or not published:
            continue

        keywords, fields, scope = match_fields(title, description, core, broad)
        stable = link or f"{section}|{published}|{title}"
        item = {
            "id": hashlib.sha1(stable.encode("utf-8")).hexdigest()[:16],
            "publishedAt": published,
            "title": title,
            "category": policy_core.category_for(f"{title} {description}"),
            "summary": description or "원문에서 세부 내용을 확인하세요.",
            "source": str(source.get("name") or "기후부 공식자료"),
            "sourceType": "official",
            "section": section,
            "url": link,
            "matchedKeywords": keywords,
            "matchedFields": fields,
            "keywordScope": scope,
        }
        all_rows.append(item)
        if keywords:
            matched_rows.append(item)

    return all_rows, matched_rows


def merge_item(previous: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    previous_summary = clean_text(previous.get("summary"))
    candidate_summary = clean_text(candidate.get("summary"))
    richer = candidate if len(candidate_summary) >= len(previous_summary) else previous
    merged = dict(richer)
    merged["matchedKeywords"] = dedupe(
        [*string_list(previous.get("matchedKeywords")), *string_list(candidate.get("matchedKeywords"))]
    )
    merged["matchedFields"] = dedupe(
        [*string_list(previous.get("matchedFields")), *string_list(candidate.get("matchedFields"))]
    )
    merged["keywordScope"] = (
        "direct"
        if "direct" in {previous.get("keywordScope"), candidate.get("keywordScope")}
        else "related"
    )
    return merged


def collect_source(
    source: dict[str, Any],
    core: list[str],
    broad: list[str],
    start_date: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    section = policy_core.source_section(source)
    page_size = archive.PAGE_SIZE_BY_SECTION.get(section, 200)
    max_pages = archive.MAX_PAGES_BY_SECTION.get(section, 120)
    collected: dict[str, dict[str, Any]] = {}
    signatures: set[str] = set()
    pages = 0
    raw_rows = 0
    matched_rows = 0
    newest_seen = ""
    oldest_seen = ""
    previous_oldest = ""
    stop_reason = ""
    keyword_hits = {keyword: 0 for keyword in [*core, *broad]}

    for page_index in range(max_pages):
        offset = page_index * page_size
        url = archive.archive_url(str(source["url"]), offset=offset, page_size=page_size)
        root = legacy.retry(
            f"{source.get('name')} 확대수집 / offset {offset}",
            lambda url=url: archive.fetch_xml(url),
            attempts=3,
        )
        rows, matched = parse_page(root, source, core, broad)
        pages += 1
        if not rows:
            stop_reason = "exhausted"
            break

        signature = hashlib.sha1(
            "|".join(
                f"{row.get('publishedAt')}|{row.get('url')}|{row.get('title')}"
                for row in rows
            ).encode("utf-8")
        ).hexdigest()
        if signature in signatures:
            page_dates = sorted(str(row.get("publishedAt", "")) for row in rows)
            if page_dates and page_dates[-1] < start_date:
                stop_reason = "boundary-repeated-page"
                break
            raise RuntimeError(
                f"{source.get('name')} RSS offset={offset}가 이전 페이지를 반복했습니다."
            )
        signatures.add(signature)

        dates = sorted(
            str(row.get("publishedAt", ""))
            for row in rows
            if str(row.get("publishedAt", ""))
        )
        if not dates:
            raise RuntimeError(
                f"{source.get('name')} RSS offset={offset}에 유효한 게시일이 없습니다."
            )
        page_oldest, page_newest = dates[0], dates[-1]
        newest_seen = page_newest if not newest_seen else max(newest_seen, page_newest)
        oldest_seen = page_oldest if not oldest_seen else min(oldest_seen, page_oldest)
        if previous_oldest and page_newest > previous_oldest:
            print(
                f"::warning title={source.get('name')} archive order::"
                f"offset {offset}의 최신일 {page_newest}가 직전 페이지 최저일 "
                f"{previous_oldest}보다 뒤입니다.",
                flush=True,
            )
        previous_oldest = page_oldest
        raw_rows += len(rows)

        for item in matched:
            published = str(item.get("publishedAt", ""))[:10]
            if published < start_date:
                continue
            key = legacy.ministry_item_key(item)
            collected[key] = merge_item(collected[key], item) if key in collected else item

        matched_rows += sum(
            1 for item in matched if str(item.get("publishedAt", "")) >= start_date
        )

        print(
            f"{source.get('name')}: {pages}페이지 / 원문 {raw_rows}건 / "
            f"확대 기준 고유 {len(collected)}건 / 범위 {page_oldest}~{page_newest}",
            flush=True,
        )

        if page_oldest < start_date:
            stop_reason = "date-boundary"
            break
        if len(rows) < page_size:
            stop_reason = "exhausted"
            break
    else:
        raise RuntimeError(
            f"{source.get('name')} RSS가 {max_pages}페이지 한도를 초과했습니다."
        )

    items = sorted(
        collected.values(),
        key=lambda item: (
            str(item.get("publishedAt", "")),
            str(item.get("title", "")),
        ),
        reverse=True,
    )
    direct_count = 0
    related_count = 0
    for item in items:
        for keyword in item.get("matchedKeywords", []):
            if keyword in keyword_hits:
                keyword_hits[keyword] += 1
        if item.get("keywordScope") == "direct":
            direct_count += 1
        else:
            related_count += 1

    audit = {
        "source": str(source.get("name") or "기후부 공식자료"),
        "section": section,
        "requestedStartDate": start_date,
        "transport": "unfiltered-rss-local-split-keyword-filter",
        "keywordRule": {
            "titleAndContent": core,
            "titleOnly": broad,
        },
        "pageSize": page_size,
        "pagesFetched": pages,
        "rawRows": raw_rows,
        "inRangeMatchedRows": matched_rows,
        "itemCount": len(items),
        "directCount": direct_count,
        "relatedCount": related_count,
        "earliest": min(
            (str(item.get("publishedAt", "")) for item in items), default=None
        ),
        "latest": max(
            (str(item.get("publishedAt", "")) for item in items), default=None
        ),
        "oldestSeen": oldest_seen or None,
        "newestSeen": newest_seen or None,
        "crossedRequestedStart": bool(oldest_seen and oldest_seen < start_date),
        "stopReason": stop_reason,
        "complete": stop_reason in {
            "exhausted",
            "date-boundary",
            "boundary-repeated-page",
        },
        "keywordHits": keyword_hits,
    }
    return items, audit


def item_key(item: dict[str, Any]) -> str:
    section = str(item.get("section", "")).strip().lower()
    if section == "krx_notice":
        source_id = str(item.get("sourceId", "")).strip()
        if source_id:
            return f"krx_notice|{source_id}"
    return legacy.ministry_item_key(item)


def remove_climate_from_policy() -> dict[str, Any]:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    items = policy.get("items", [])
    if not isinstance(policy, dict) or not isinstance(items, list):
        raise RuntimeError("policies.json 형식이 올바르지 않습니다.")
    before = len(items)
    policy["items"] = [
        item
        for item in items
        if not (
            isinstance(item, dict)
            and str(item.get("sourceType", "")).lower() != "news"
            and str(item.get("section", "")).lower() in CLIMATE_SECTIONS
        )
    ]
    changed = atomic_write(POLICY_PATH, policy)
    return {"changed": changed, "removed": before - len(policy["items"])}


def coverage(items: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {section: 0 for section in sorted(VALID_SECTIONS)}
    earliest: dict[str, str | None] = {section: None for section in counts}
    latest: dict[str, str | None] = {section: None for section in counts}
    direct = {"press": 0, "notice": 0}
    related = {"press": 0, "notice": 0}
    for item in items:
        section = str(item.get("section", "")).lower()
        if section not in counts:
            continue
        published = str(item.get("publishedAt", ""))[:10]
        counts[section] += 1
        earliest[section] = (
            published if earliest[section] is None else min(str(earliest[section]), published)
        )
        latest[section] = (
            published if latest[section] is None else max(str(latest[section]), published)
        )
        if section in CLIMATE_SECTIONS:
            target = direct if item.get("keywordScope") == "direct" else related
            target[section] += 1
    return {
        "itemCount": sum(counts.values()),
        "counts": counts,
        "earliest": earliest,
        "latest": latest,
        "directCounts": direct,
        "relatedCounts": related,
        "labels": merger.SECTION_LABELS,
    }


def validate_items(
    items: list[dict[str, Any]],
    start_date: str,
    previous_counts: dict[str, int],
) -> dict[str, Any]:
    keys: set[str] = set()
    duplicates = 0
    invalid_dates = 0
    invalid_domains = 0
    missing_keywords = 0
    counts = {section: 0 for section in VALID_SECTIONS}
    related_counts = {"press": 0, "notice": 0}

    for item in items:
        if not isinstance(item, dict):
            continue
        section = str(item.get("section", "")).strip().lower()
        published = str(item.get("publishedAt", ""))[:10]
        host = (urllib.parse.urlsplit(str(item.get("url", ""))).hostname or "").lower()
        if section not in counts:
            raise RuntimeError(f"알 수 없는 공식자료 section: {section}")
        counts[section] += 1
        if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", published) or published < start_date:
            invalid_dates += 1
        if section in CLIMATE_SECTIONS:
            if host not in MCEE_HOSTS:
                invalid_domains += 1
            if not item.get("matchedKeywords"):
                missing_keywords += 1
            if item.get("keywordScope") == "related":
                related_counts[section] += 1
        elif host not in KRX_HOSTS:
            invalid_domains += 1

        key = item_key(item)
        if key in keys:
            duplicates += 1
        keys.add(key)

    if duplicates or invalid_dates or invalid_domains or missing_keywords:
        raise RuntimeError(
            "기후부 확대 이력 검증 실패: "
            f"중복={duplicates}, 날짜={invalid_dates}, 도메인={invalid_domains}, "
            f"키워드누락={missing_keywords}"
        )
    if any(counts[section] <= 0 for section in VALID_SECTIONS):
        raise RuntimeError(f"공식자료 구분 중 0건이 있습니다: {counts}")
    for section in CLIMATE_SECTIONS:
        if counts[section] < int(previous_counts.get(section, 0)):
            raise RuntimeError(
                f"확대 후 {section} 건수가 기존보다 감소했습니다: "
                f"{counts[section]} < {previous_counts.get(section, 0)}"
            )
        if related_counts[section] <= 0:
            raise RuntimeError(f"{section}에 확대 키워드 전용 자료가 없습니다.")

    return {
        "itemCount": len(items),
        "counts": counts,
        "relatedCounts": related_counts,
        "duplicateKeys": duplicates,
        "invalidDates": invalid_dates,
        "invalidDomains": invalid_domains,
        "missingKeywords": missing_keywords,
    }


def write_summary(
    history: dict[str, Any],
    validation: dict[str, Any],
) -> bool:
    cov = history.get("coverage", {})
    audits = history.get("sourceAudit", [])
    summary_audits = []
    for audit in audits:
        if not isinstance(audit, dict):
            continue
        section = str(audit.get("section", "")).lower()
        if not section and "한국거래소" in str(audit.get("source", "")):
            section = "krx_notice"
        summary_audits.append(
            {
                "source": audit.get("source"),
                "section": section or None,
                "pagesFetched": audit.get("pagesFetched"),
                "rawRowsScanned": audit.get("rawRows", audit.get("rawRowsScanned")),
                "finalMergedItems": int((cov.get("counts") or {}).get(section, 0)),
                "directCount": audit.get("directCount"),
                "relatedCount": audit.get("relatedCount"),
                "oldestSourceDateSeen": audit.get(
                    "oldestSeen", audit.get("oldestSourceDateSeen")
                ),
                "crossedRequestedStart": audit.get("crossedRequestedStart"),
                "complete": audit.get("complete"),
            }
        )

    document = {
        "schemaVersion": "2.0",
        "requestedStartDate": history.get("requestedStartDate", START_DATE),
        "verifiedAt": datetime.now(policy_core.KST).isoformat(timespec="seconds"),
        "keywordExpansionVersion": EXPANSION_VERSION,
        "keywordRule": history.get("climateKeywordExpansion", {}).get(
            "keywordRule", {}
        ),
        "scope": {
            "press": "기후부 보도자료 전체 이력에서 핵심 7개는 제목·본문, 확대 23개는 제목으로 판정",
            "notice": "기후부 공지·공고 전체 이력에서 핵심 7개는 제목·본문, 확대 23개는 제목으로 판정",
            "krx_notice": "한국거래소 배출권시장 공지사항의 2015-01-01 이후 일반 공지 전체",
        },
        "officialItemCount": int(
            cov.get("itemCount", len(history.get("items", [])))
        ),
        "counts": cov.get("counts", {}),
        "directCounts": cov.get("directCounts", {}),
        "relatedCounts": cov.get("relatedCounts", {}),
        "earliest": cov.get("earliest", {}),
        "latest": cov.get("latest", {}),
        "sourceAudit": summary_audits,
        "validation": validation,
    }
    return atomic_write(SUMMARY_PATH, document)


def self_test() -> None:
    sample = """<?xml version='1.0' encoding='UTF-8'?>
    <rss><channel>
      <item><title><![CDATA[배출권거래제 운영계획]]></title>
      <description><![CDATA[유상할당 세부내용]]></description>
      <link>https://www.mcee.go.kr/example/1</link>
      <pubDate>Thu Jan 02 00:00:00 KST 2025</pubDate></item>
      <item><title><![CDATA[탄소중립 산업 전환 지원]]></title>
      <description><![CDATA[일반 안내]]></description>
      <link>https://www.mcee.go.kr/example/2</link>
      <pubDate>Wed Jan 01 00:00:00 KST 2025</pubDate></item>
      <item><title><![CDATA[일반 환경자료]]></title>
      <description><![CDATA[본문에 탄소중립만 포함]]></description>
      <link>https://www.mcee.go.kr/example/3</link>
      <pubDate>Tue Dec 31 00:00:00 KST 2024</pubDate></item>
    </channel></rss>"""
    root = ET.fromstring(sample.encode("utf-8"))
    source = {"name": "기후부 보도자료", "type": "press"}
    rows, matched = parse_page(
        root,
        source,
        ["배출권", "유상할당"],
        ["탄소중립"],
    )
    assert len(rows) == 3
    assert len(matched) == 2
    assert matched[0]["keywordScope"] == "direct"
    assert matched[0]["matchedFields"] == ["title", "content"]
    assert matched[1]["keywordScope"] == "related"
    assert matched[1]["matchedFields"] == ["title"]
    assert not rows[2]["matchedKeywords"]
    core, broad = load_keywords()
    assert len(core) == 7 and len(broad) == 23
    print("CLIMATE_EXPANDED_SELF_TEST=PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default=START_DATE)
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", args.start_date):
        raise SystemExit("--start-date는 YYYY-MM-DD 형식이어야 합니다.")
    if args.lookback_days < 1 or args.lookback_days > 366:
        raise SystemExit("--lookback-days는 1~366 범위여야 합니다.")
    if not HISTORY_PATH.is_file():
        raise RuntimeError("기존 기후부·한국거래소 공식자료 전체 이력이 없습니다.")
    if not POLICY_PATH.is_file():
        raise RuntimeError("public/data/policies.json이 없습니다.")

    core, broad = load_keywords()
    sources = load_climate_sources()
    today = datetime.now(policy_core.KST).date()
    query_start = (
        args.start_date
        if args.full
        else max(
            args.start_date,
            (today - timedelta(days=args.lookback_days)).isoformat(),
        )
    )
    query_end = today.isoformat()

    previous_history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    previous_items = previous_history.get("items", [])
    if not isinstance(previous_items, list):
        raise RuntimeError("기존 공식자료 이력 items가 배열이 아닙니다.")
    previous_cov = previous_history.get("coverage", {})
    previous_counts = {
        key: int((previous_cov.get("counts") or {}).get(key, 0))
        for key in VALID_SECTIONS
    }

    collected: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    for source in sources:
        items, audit = collect_source(source, core, broad, query_start)
        if not audit.get("complete"):
            raise RuntimeError(
                f"{source.get('name')} 확대 수집이 완전하지 않습니다: {audit}"
            )
        collected.extend(items)
        audits.append(audit)

    merged: dict[str, dict[str, Any]] = {}
    for raw in previous_items:
        if not isinstance(raw, dict):
            continue
        section = str(raw.get("section", "")).strip().lower()
        if section not in VALID_SECTIONS:
            continue
        if args.full and section in CLIMATE_SECTIONS:
            continue
        merged[item_key(raw)] = raw
    for item in collected:
        key = item_key(item)
        merged[key] = merge_item(merged[key], item) if key in merged else item

    final_items = sorted(
        merged.values(),
        key=lambda item: (
            str(item.get("publishedAt", "")),
            str(item.get("title", "")),
            str(item.get("sourceId", "")),
        ),
        reverse=True,
    )
    validation = validate_items(final_items, args.start_date, previous_counts)
    final_cov = coverage(final_items)

    preserved_audits = [
        audit
        for audit in previous_history.get("sourceAudit", [])
        if isinstance(audit, dict)
        and str(audit.get("section", "")).lower() == "krx_notice"
    ]
    if not preserved_audits:
        preserved_audits = [
            audit
            for audit in previous_history.get("sourceAudit", [])
            if isinstance(audit, dict)
            and "한국거래소" in str(audit.get("source", ""))
        ]

    history = dict(previous_history)
    history["requestedStartDate"] = args.start_date
    history["generatedAt"] = datetime.now(policy_core.KST).isoformat(timespec="seconds")
    history["collectionMode"] = "full" if args.full else "incremental"
    history["queryStartDate"] = query_start
    history["queryEndDate"] = query_end
    history["scope"] = {
        "ministry": "기후부 보도자료·공지사항 중 핵심 7개는 제목·본문, 확대 23개는 제목에 포함된 공식자료",
        "krx": "한국거래소 배출권시장 공지사항 일반 게시물 전체",
        "keywords": {"titleAndContent": core, "titleOnly": broad},
    }
    history["climateKeywordExpansion"] = {
        "version": EXPANSION_VERSION,
        "sharedWith": "산업부 보도자료·공지사항",
        "keywordFile": "config/motie_policy_keywords.json",
        "keywordRule": {"titleAndContent": core, "titleOnly": broad},
        "collectionMode": "full" if args.full else "incremental",
        "queryStartDate": query_start,
        "queryEndDate": query_end,
    }
    history["sourceAudit"] = [*audits, *preserved_audits]
    history["coverage"] = final_cov
    history["items"] = final_items
    atomic_write(HISTORY_PATH, history)

    removal = remove_climate_from_policy()
    merge_result = merger.merge_files(
        POLICY_PATH,
        HISTORY_PATH,
        start_date=args.start_date,
    )

    final_history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    final_history["coverage"] = coverage(final_history.get("items", []))
    final_history["climateKeywordExpansion"] = history["climateKeywordExpansion"]
    final_history["scope"] = history["scope"]
    final_history["sourceAudit"] = history["sourceAudit"]
    final_history["collectionMode"] = history["collectionMode"]
    final_history["queryStartDate"] = query_start
    final_history["queryEndDate"] = query_end
    atomic_write(HISTORY_PATH, final_history)

    final_validation = validate_items(
        final_history.get("items", []),
        args.start_date,
        previous_counts,
    )
    summary_changed = write_summary(final_history, final_validation)

    result = {
        "version": EXPANSION_VERSION,
        "collectionMode": history["collectionMode"],
        "queryStartDate": query_start,
        "queryEndDate": query_end,
        "coverage": final_history.get("coverage", {}),
        "validation": final_validation,
        "policyRemoval": removal,
        "merge": merge_result,
        "summaryChanged": summary_changed,
        "sourceAudit": audits,
    }
    print("CLIMATE_EXPANDED_RESULT=" + json.dumps(result, ensure_ascii=False))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"CLIMATE_EXPANDED_ERROR={exc}", flush=True)
        raise
