#!/usr/bin/env python3
"""Collect and preserve the expanded policy-event taxonomy since 2015.

The collector keeps the existing official-history pipelines intact and writes a
separate expansion payload. Climate-ministry RSS archives are scanned directly;
industrial-ministry boards are queried with a field-aware plan; KRX ETS notices
are reclassified from the already verified full archive. Broad accident,
production and trade terms are accepted only when their configured context is
also present, which prevents generic fire, tariff or volume notices from
flooding the policy radar.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "policy_event_taxonomy.json"
SETTINGS_PATH = ROOT / "config" / "settings.json"
EXPANSION_PATH = ROOT / "public" / "data" / "policy-event-expansion.json"
SUMMARY_PATH = ROOT / "public" / "data" / "policy-event-expansion-summary.json"
OFFICIAL_HISTORY_PATH = ROOT / "public" / "data" / "policy-official-history.json"
KST = ZoneInfo("Asia/Seoul")
USER_AGENT = "Mozilla/5.0 (compatible; ETS-LIVE-DASHBOARD/6.0; +https://ebrain725.github.io/)"
REQUEST_TIMEOUT_SECONDS = 45
MAX_CLIMATE_PAGES = 100
MAX_MOTIE_QUERY_PAGES = 500


def clean_text(value: Any, limit: int | None = None) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(str(value or "")))
    text = re.sub(r"<script\b.*?</script>|<style\b.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[\u200b-\u200d\u2060\ufeff]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def compact_text(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣%]+", "", clean_text(value).lower())


def dedupe(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = clean_text(value)
        if text and text not in result:
            result.append(text)
    return result


def phrase_present(text: str, phrase: str) -> bool:
    visible = clean_text(text)
    needle = clean_text(phrase)
    if not visible or not needle:
        return False
    normalized_needle = re.sub(r"[\s‐‑‒–—−_-]+", "", needle).upper()
    normalized_visible = unicodedata.normalize("NFKC", visible).upper()
    if re.fullmatch(r"[A-Z]{2,6}\d{0,3}", normalized_needle):
        pattern = "\\s*[-‐‑‒–—−_]?\\s*".join(map(re.escape, normalized_needle.split("-")))
        return bool(re.search(rf"(?<![A-Z0-9]){pattern}(?![A-Z0-9])", normalized_visible))
    if needle.upper() == "EU ETS":
        return bool(re.search(r"(?<![A-Z0-9])EU\s*[-‐‑‒–—−_]?\s*ETS(?![A-Z0-9])", normalized_visible))
    if needle.upper() == "K-ETS":
        return bool(re.search(r"(?<![A-Z0-9])K\s*[-‐‑‒–—−_]?\s*ETS(?![A-Z0-9])", normalized_visible))
    return compact_text(needle) in compact_text(visible)


def load_config() -> dict[str, Any]:
    document = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    categories = document.get("categories")
    if not isinstance(categories, list) or len(categories) != 38:
        raise RuntimeError(f"정책 이벤트 분류는 38개여야 합니다: {len(categories or [])}")
    ids = [str(item.get("id", "")) for item in categories if isinstance(item, dict)]
    labels = [str(item.get("label", "")) for item in categories if isinstance(item, dict)]
    if len(set(ids)) != 38 or len(set(labels)) != 38 or any(not value for value in ids + labels):
        raise RuntimeError("정책 이벤트 분류 ID 또는 이름이 비어 있거나 중복됩니다.")
    query_plan = document.get("queryPlan") or {}
    if not query_plan.get("titleAndContent") or not query_plan.get("titleOnly"):
        raise RuntimeError("산업부 검색용 제목·본문/제목 전용 검색어가 없습니다.")
    return document


def rule_matches(rule: dict[str, Any], text: str) -> tuple[bool, list[str]]:
    matched: list[str] = []
    any_values = [clean_text(value) for value in rule.get("any", []) if clean_text(value)]
    if any_values:
        hits = [value for value in any_values if phrase_present(text, value)]
        if hits:
            return True, hits
    all_groups = rule.get("all") or []
    if all_groups:
        group_hits: list[str] = []
        for group in all_groups:
            hits = [clean_text(value) for value in group if clean_text(value) and phrase_present(text, value)]
            if not hits:
                return False, []
            group_hits.append(hits[0])
        return True, group_hits
    return False, []


def classify_event(
    title: str,
    content: str,
    config: dict[str, Any],
    *,
    trusted_query_matches: dict[str, set[str]] | None = None,
    krx_fallback: bool = False,
) -> dict[str, Any]:
    title = clean_text(title, 600)
    content = clean_text(content, 8_000)
    trusted_query_matches = trusted_query_matches or {}
    categories: list[dict[str, str]] = []
    keywords: list[str] = []
    fields: list[str] = []

    for category in config["categories"]:
        match_field = str(category.get("matchField") or "titleOnly")
        material = title if match_field == "titleOnly" else f"{title} {content}"
        category_hits: list[str] = []
        category_fields: list[str] = []

        trusted_terms = [clean_text(value) for value in category.get("trustedQueryTerms", [])]
        for term in trusted_terms:
            query_fields = trusted_query_matches.get(term, set())
            if not query_fields:
                continue
            category_hits.append(term)
            category_fields.extend(f"query-{field}" for field in sorted(query_fields))

        if not category_hits:
            for rule in category.get("rules", []):
                matched, hits = rule_matches(rule, material)
                if not matched:
                    continue
                category_hits.extend(hits)
                for hit in hits:
                    if phrase_present(title, hit):
                        category_fields.append("title")
                    elif phrase_present(content, hit):
                        category_fields.append("content")
                break

        if not category_hits:
            continue
        categories.append(
            {
                "id": str(category["id"]),
                "label": str(category["label"]),
                "scope": str(category.get("scope") or "related"),
            }
        )
        keywords.extend(category_hits)
        fields.extend(category_fields)

    if krx_fallback and not categories:
        categories.append(
            {"id": "ets_core", "label": "배출권·배출권거래제·ETS", "scope": "direct"}
        )
        keywords.append("한국거래소 배출권시장 전용 공지")
        fields.append("source-board")

    scope = "direct" if any(item["scope"] == "direct" for item in categories) else "related"
    return {
        "eventCategoryIds": dedupe(item["id"] for item in categories),
        "eventCategories": dedupe(item["label"] for item in categories),
        "matchedKeywords": dedupe(keywords),
        "matchedFields": dedupe(fields),
        "keywordScope": scope if categories else "",
    }


def category_for(event_ids: list[str], fallback: str = "제도") -> str:
    ids = set(event_ids)
    if ids & {"auction_supply_schedule", "auction_result", "paid_allocation_ratio"}:
        return "유상경매"
    if "market_stability" in ids:
        return "시장안정"
    if ids & {"offset_conversion_limit", "international_reduction"}:
        return "상쇄·외부사업"
    if ids & {
        "market_volume_value", "large_sale_release", "market_participant_maker", "eu_ets_eua"
    }:
        return "시장·가격"
    return fallback or "제도"


def strict_date(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    try:
        parsed = parsedate_to_datetime(text.replace(" KST ", " +0900 "))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=KST)
        return parsed.astimezone(KST).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        pass
    match = re.search(r"(20\d{2})[./-]?(\d{1,2})[./-]?(\d{1,2})", text)
    if not match:
        return ""
    try:
        return date(*(int(value) for value in match.groups())).isoformat()
    except ValueError:
        return ""


def child_text(item: ET.Element, name: str) -> str:
    node = item.find(name)
    if node is not None and node.text:
        return node.text.strip()
    for child in item:
        if child.tag.rsplit("}", 1)[-1].lower() == name.lower() and child.text:
            return child.text.strip()
    return ""


def parse_rss(payload: bytes) -> ET.Element:
    text = payload.decode("utf-8", errors="replace")
    text = re.sub(
        r"(<link>)(.*?)(</link>)",
        lambda match: match.group(1)
        + re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9A-Fa-f]+;)", "&amp;", match.group(2))
        + match.group(3),
        text,
        flags=re.S,
    )
    return ET.fromstring(text)


def request_bytes(url: str, attempts: int = 4) -> bytes:
    errors: list[str] = []
    candidates = [url]
    if "https://www.mcee.go.kr/" in url:
        candidates.append(url.replace("https://www.mcee.go.kr/", "https://mcee.go.kr/", 1))
    elif "https://mcee.go.kr/" in url:
        candidates.append(url.replace("https://mcee.go.kr/", "https://www.mcee.go.kr/", 1))
    for attempt in range(1, attempts + 1):
        for candidate in candidates:
            request = urllib.request.Request(
                candidate,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/rss+xml, application/xml, text/xml, */*",
                    "Connection": "close",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                    payload = response.read(20_000_000)
                if len(payload) < 200:
                    raise RuntimeError(f"응답이 너무 작습니다: {len(payload)}바이트")
                return payload
            except Exception as exc:  # noqa: BLE001 - all network errors are retried
                errors.append(f"{urllib.parse.urlsplit(candidate).netloc}: {exc}")
        if attempt < attempts:
            time.sleep(attempt * 2)
    raise RuntimeError(" / ".join(errors[-8:]))


def rss_page_url(base_url: str, *, offset: int, page_size: int) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    for key in ("searchKey", "searchValue", "searchKeyword", "searchCondition"):
        query.pop(key, None)
    query.update({"maxPageItems": str(page_size), "pagerOffset": str(offset)})
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment)
    )


def climate_source_rows(root: ET.Element) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for xml_item in root.findall(".//item"):
        title = clean_text(child_text(xml_item, "title"), 500)
        summary = clean_text(child_text(xml_item, "description"), 8_000)
        url = re.sub(
            r";jsessionid=[^?&#]+",
            "",
            html.unescape(child_text(xml_item, "link")),
            flags=re.I,
        ).strip()
        published = strict_date(child_text(xml_item, "pubDate") or child_text(xml_item, "date"))
        if title and published:
            rows.append({"title": title, "summary": summary, "url": url, "publishedAt": published})
    return rows


def source_id_from_url(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
        query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    except ValueError:
        return ""
    for name in ("boardId", "seq", "nttId", "bbsSeq", "boardSeq", "idx", "id"):
        value = str(query.get(name, "")).strip()
        if value:
            return value
    path_numbers = re.findall(r"/(\d{4,})(?:/|$)", parsed.path)
    return path_numbers[-1] if path_numbers else ""


def load_climate_sources() -> list[dict[str, Any]]:
    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    sources = [
        source
        for source in settings.get("policySources", [])
        if isinstance(source, dict) and str(source.get("type", "")).lower() in {"press", "notice"}
    ]
    if {str(item.get("type", "")).lower() for item in sources} != {"press", "notice"}:
        raise RuntimeError("기후부 보도자료·공지사항 RSS 설정이 부족합니다.")
    return sources


def crawl_climate(
    source: dict[str, Any],
    config: dict[str, Any],
    *,
    minimum_date: str,
    require_cutoff_crossing: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    section = str(source.get("type", "")).lower()
    page_size = 1000 if section == "press" else 200
    offset = 0
    pages = 0
    raw_rows = 0
    matched: dict[str, dict[str, Any]] = {}
    seen_page_signatures: set[str] = set()
    oldest_seen = "9999-12-31"
    crossed = False

    while pages < MAX_CLIMATE_PAGES:
        url = rss_page_url(str(source["url"]), offset=offset, page_size=page_size)
        rows = climate_source_rows(parse_rss(request_bytes(url)))
        pages += 1
        if not rows:
            break
        signature = hashlib.sha1(
            "|".join(f"{row['publishedAt']}:{row['title']}" for row in rows[:20]).encode("utf-8")
        ).hexdigest()
        if signature in seen_page_signatures:
            raise RuntimeError(f"기후부 {section} RSS 페이지 반복 감지: offset={offset}")
        seen_page_signatures.add(signature)
        raw_rows += len(rows)
        page_dates = [row["publishedAt"] for row in rows]
        oldest_seen = min(oldest_seen, *page_dates)
        if max(page_dates) < minimum_date:
            crossed = True
            break

        for row in rows:
            if row["publishedAt"] < minimum_date:
                crossed = True
                continue
            classification = classify_event(row["title"], row["summary"], config)
            if not classification["eventCategories"]:
                continue
            source_id = source_id_from_url(row["url"])
            stable = row["url"] or f"{section}|{row['publishedAt']}|{row['title']}"
            item = {
                "id": f"policy-event-{hashlib.sha1(stable.encode('utf-8')).hexdigest()[:20]}",
                "sourceId": source_id,
                "publishedAt": row["publishedAt"],
                "title": row["title"],
                "summary": clean_text(row["summary"], 1_200) or "원문에서 세부 내용을 확인하세요.",
                "source": str(source.get("name") or "기후부 공식자료"),
                "sourceType": "official",
                "section": section,
                "url": row["url"],
                "category": category_for(classification["eventCategoryIds"]),
                "taxonomyVersion": config["taxonomyVersion"],
                **classification,
            }
            matched[stable] = item

        if min(page_dates) < minimum_date:
            crossed = True
        offset += len(rows)
        if len(rows) < page_size:
            break

    if pages >= MAX_CLIMATE_PAGES:
        raise RuntimeError(f"기후부 {section} RSS가 최대 페이지를 초과했습니다.")
    if require_cutoff_crossing and not crossed and oldest_seen >= minimum_date:
        raise RuntimeError(
            f"기후부 {section}가 요청 경계 이전까지 조회되지 않았습니다: oldest={oldest_seen}"
        )
    audit = {
        "source": str(source.get("name") or section),
        "section": section,
        "mode": "rss-archive-scan",
        "minimumDate": minimum_date,
        "pagesFetched": pages,
        "rawRowsScanned": raw_rows,
        "matchedItems": len(matched),
        "oldestSourceDateSeen": None if oldest_seen == "9999-12-31" else oldest_seen,
        "crossedMinimumDate": crossed,
        "complete": True,
    }
    return sorted(matched.values(), key=lambda item: (item["publishedAt"], item["title"]), reverse=True), audit


def motie_output_section(board_key: str, metadata: dict[str, Any]) -> str:
    explicit = str(metadata.get("outputSection") or metadata.get("section") or "").lower()
    if explicit in {"motie_press", "motie_notice"}:
        return explicit
    label = str(metadata.get("label") or metadata.get("name") or "")
    return "motie_press" if "보도" in label or "press" in board_key.lower() else "motie_notice"


def collect_motie(
    config: dict[str, Any],
    *,
    minimum_date: str,
    end_date: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sys.path.insert(0, str(ROOT / "scripts"))
    import sync_motie_official_history as motie  # type: ignore

    plan = config["queryPlan"]
    query_specs: list[tuple[str, str, str]] = []
    for term in dedupe(plan.get("titleAndContent", [])):
        query_specs.extend(((term, "1", "title"), (term, "2", "content")))
    for term in dedupe(plan.get("titleOnly", [])):
        query_specs.append((term, "1", "title"))
    query_specs = list(dict.fromkeys(query_specs))

    all_items: dict[str, dict[str, Any]] = {}
    audits: list[dict[str, Any]] = []
    for board_key, metadata in motie.BOARDS.items():
        output_section = motie_output_section(str(board_key), metadata)
        board_candidates: dict[str, dict[str, Any]] = {}
        query_audit: list[dict[str, Any]] = []
        for query_index, (term, condition, field) in enumerate(query_specs, start=1):
            first_url = motie.build_query_url(
                board_key,
                keyword=term,
                condition=condition,
                page_index=1,
                start_date=minimum_date,
                end_date=end_date,
            )
            first_document = motie.request_text(first_url)
            echoed = motie.echoed_keyword(first_document)
            if echoed and clean_text(echoed) != clean_text(term):
                raise RuntimeError(
                    f"산업부 검색조건 미적용: {board_key}/{field}/{term!r} -> {echoed!r}"
                )
            total = int(motie.parse_total_count(first_document) or 0)
            first_rows = motie.parse_rows(first_document, board_key)
            if total > 0 and not first_rows:
                raise RuntimeError(f"산업부 검색결과 파싱 실패: {board_key}/{field}/{term}, total={total}")
            page_size = max(1, len(first_rows))
            total_pages = max(1, math.ceil(total / page_size)) if total else 1
            if total_pages > MAX_MOTIE_QUERY_PAGES:
                raise RuntimeError(
                    f"산업부 검색 페이지 과다: {board_key}/{field}/{term}={total_pages}"
                )
            parsed_count = 0
            previous_ids: tuple[str, ...] | None = None
            for page_index in range(1, total_pages + 1):
                document = first_document if page_index == 1 else motie.request_text(
                    motie.build_query_url(
                        board_key,
                        keyword=term,
                        condition=condition,
                        page_index=page_index,
                        start_date=minimum_date,
                        end_date=end_date,
                    )
                )
                rows = first_rows if page_index == 1 else motie.parse_rows(document, board_key)
                page_ids = tuple(str(item.get("sourceId", "")) for item in rows)
                if page_index > 1 and page_ids and page_ids == previous_ids:
                    raise RuntimeError(
                        f"산업부 페이지 반복 감지: {board_key}/{field}/{term}/page={page_index}"
                    )
                previous_ids = page_ids
                parsed_count += len(rows)
                for raw in rows:
                    source_id = str(raw.get("sourceId", "")).strip()
                    published = str(raw.get("publishedAt", ""))[:10]
                    if not source_id or not (minimum_date <= published <= end_date):
                        continue
                    stable = f"{output_section}|{source_id}"
                    item = board_candidates.get(stable, dict(raw))
                    item["section"] = output_section
                    item["source"] = "산업부 보도자료" if output_section == "motie_press" else "산업부 공지사항"
                    trusted = item.setdefault("_trustedQueryMatches", {})
                    trusted.setdefault(term, [])
                    if field not in trusted[term]:
                        trusted[term].append(field)
                    board_candidates[stable] = item
                if total == 0 or not rows:
                    break
            query_audit.append(
                {
                    "term": term,
                    "field": field,
                    "reportedTotal": total,
                    "pagesFetched": total_pages if total else 1,
                    "parsedRows": parsed_count,
                }
            )
            if query_index % 20 == 0 or query_index == len(query_specs):
                print(
                    f"산업부 {metadata.get('label', board_key)} 검색 {query_index}/{len(query_specs)} · "
                    f"후보 {len(board_candidates)}건",
                    flush=True,
                )

        accepted = 0
        for stable, raw in board_candidates.items():
            trusted = {
                term: set(fields)
                for term, fields in (raw.pop("_trustedQueryMatches", {}) or {}).items()
            }
            classification = classify_event(
                str(raw.get("title", "")),
                str(raw.get("summary", "")),
                config,
                trusted_query_matches=trusted,
            )
            if not classification["eventCategories"]:
                continue
            item = dict(raw)
            item.update(
                {
                    "id": str(item.get("id") or f"policy-event-{hashlib.sha1(stable.encode()).hexdigest()[:20]}"),
                    "sourceType": "official",
                    "section": output_section,
                    "source": "산업부 보도자료" if output_section == "motie_press" else "산업부 공지사항",
                    "summary": clean_text(item.get("summary"), 1_200)
                    or "산업부 공식자료입니다. 원문에서 세부 내용을 확인하세요.",
                    "category": category_for(
                        classification["eventCategoryIds"], str(item.get("category") or "제도")
                    ),
                    "taxonomyVersion": config["taxonomyVersion"],
                    **classification,
                }
            )
            all_items[stable] = item
            accepted += 1
        audits.append(
            {
                "source": str(metadata.get("label") or board_key),
                "boardKey": str(board_key),
                "section": output_section,
                "mode": "official-field-aware-search",
                "minimumDate": minimum_date,
                "queryCount": len(query_specs),
                "candidateItems": len(board_candidates),
                "matchedItems": accepted,
                "complete": True,
                "queries": query_audit,
            }
        )
    return sorted(all_items.values(), key=lambda item: (item["publishedAt"], item["title"]), reverse=True), audits


def load_krx_items(config: dict[str, Any], minimum_date: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    document = json.loads(OFFICIAL_HISTORY_PATH.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for raw in document.get("items", []):
        if not isinstance(raw, dict) or str(raw.get("section", "")) != "krx_notice":
            continue
        published = str(raw.get("publishedAt", ""))[:10]
        if published < minimum_date:
            continue
        classification = classify_event(
            str(raw.get("title", "")),
            str(raw.get("summary", "")),
            config,
            krx_fallback=True,
        )
        item = dict(raw)
        item.update(
            {
                "sourceType": "official",
                "section": "krx_notice",
                "source": str(item.get("source") or "한국거래소 배출권 공지사항"),
                "category": category_for(
                    classification["eventCategoryIds"], str(item.get("category") or "제도")
                ),
                "taxonomyVersion": config["taxonomyVersion"],
                **classification,
            }
        )
        rows.append(item)
    audit = {
        "source": "한국거래소 배출권 공지사항",
        "section": "krx_notice",
        "mode": "verified-full-archive-reclassification",
        "minimumDate": minimum_date,
        "matchedItems": len(rows),
        "complete": True,
    }
    return rows, audit


def canonical_url(value: Any) -> str:
    text = clean_text(value)
    if not re.match(r"^https?://", text, re.I):
        return ""
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    path = re.sub(r"/{2,}", "/", parsed.path or "/").rstrip("/") or "/"
    query = urllib.parse.urlencode(sorted(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)))
    return f"{host}{path}{'?' + query if query else ''}"


def item_key(item: dict[str, Any]) -> str:
    section = str(item.get("section", "")).strip().lower()
    source_id = str(item.get("sourceId", "")).strip()
    if source_id:
        return f"{section}|id|{source_id}"
    url = canonical_url(item.get("url"))
    if url:
        return f"{section}|url|{url}"
    title = compact_text(item.get("title"))
    return f"{section}|date-title|{str(item.get('publishedAt', ''))[:10]}|{title}"


def merge_item(previous: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    previous_summary = clean_text(previous.get("summary"))
    candidate_summary = clean_text(candidate.get("summary"))
    richer = candidate if len(candidate_summary) >= len(previous_summary) else previous
    merged = dict(richer)
    for field in ("eventCategoryIds", "eventCategories", "matchedKeywords", "matchedFields"):
        merged[field] = dedupe([*(previous.get(field) or []), *(candidate.get(field) or [])])
    merged["keywordScope"] = (
        "direct"
        if "direct" in {str(previous.get("keywordScope")), str(candidate.get("keywordScope"))}
        else "related"
    )
    merged["taxonomyVersion"] = candidate.get("taxonomyVersion") or previous.get("taxonomyVersion")
    merged["category"] = category_for(
        list(merged.get("eventCategoryIds") or []), str(merged.get("category") or "제도")
    )
    return merged


def atomic_write(path: Path, document: dict[str, Any]) -> bool:
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


def valid_previous_items(config: dict[str, Any], minimum_date: str) -> list[dict[str, Any]]:
    if not EXPANSION_PATH.is_file():
        return []
    try:
        document = json.loads(EXPANSION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = []
    for item in document.get("items", []):
        if not isinstance(item, dict):
            continue
        published = str(item.get("publishedAt", ""))[:10]
        if published < minimum_date or not item.get("title") or not item.get("section"):
            continue
        item = dict(item)
        item["taxonomyVersion"] = config["taxonomyVersion"]
        rows.append(item)
    return rows


def build_summary(document: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    items = document["items"]
    section_counts = Counter(str(item.get("section", "")) for item in items)
    scope_counts = Counter(str(item.get("keywordScope", "")) for item in items)
    category_counts: Counter[str] = Counter()
    for item in items:
        category_counts.update(str(value) for value in item.get("eventCategories", []) if value)
    dates = [str(item.get("publishedAt", ""))[:10] for item in items if item.get("publishedAt")]
    return {
        "schemaVersion": "2.0",
        "taxonomyVersion": config["taxonomyVersion"],
        "generatedAt": document["generatedAt"],
        "collectionMode": document["collectionMode"],
        "startDate": document["startDate"],
        "itemCount": len(items),
        "earliest": min(dates) if dates else None,
        "latest": max(dates) if dates else None,
        "sectionCounts": dict(sorted(section_counts.items())),
        "scopeCounts": dict(sorted(scope_counts.items())),
        "categoryCounts": {
            category["label"]: category_counts.get(category["label"], 0)
            for category in config["categories"]
        },
        "sourceAudit": document["sourceAudit"],
        "validation": {
            "categoryCount": len(config["categories"]),
            "duplicateKeys": len(items) - len({item_key(item) for item in items}),
            "missingCategory": sum(not item.get("eventCategories") for item in items),
            "invalidDates": sum(
                not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", str(item.get("publishedAt", ""))[:10])
                for item in items
            ),
        },
    }


def self_test() -> None:
    config = load_config()
    direct = classify_event(
        "제4차 배출권거래제 할당계획 확정",
        "유상할당 비율과 이월 한도를 조정한다.",
        config,
    )
    assert direct["keywordScope"] == "direct"
    assert "배출권·배출권거래제·ETS" in direct["eventCategories"]
    assert "할당계획·할당량" in direct["eventCategories"]

    related = classify_event("폭염으로 최대 전력수요 경신", "", config)
    assert related["keywordScope"] == "related"
    assert "폭염·한파·전력수요" in related["eventCategories"]

    accident = classify_event("석유화학 공장 화재로 가동중단", "", config)
    assert "화재·폭발·침수·가동중단" in accident["eventCategories"]
    false_positive = classify_event("주택 화재 예방 캠페인", "", config)
    assert "화재·폭발·침수·가동중단" not in false_positive["eventCategories"]

    trade = classify_event("중국산 철강 반덤핑 관세 조사", "", config)
    assert "중국 증설·반덤핑·관세" in trade["eventCategories"]
    generic_tariff = classify_event("여행객 휴대품 관세 안내", "", config)
    assert "중국 증설·반덤핑·관세" not in generic_tariff["eventCategories"]

    trusted = classify_event(
        "제도 운영 안내",
        "",
        config,
        trusted_query_matches={"배출권": {"content"}},
    )
    assert trusted["keywordScope"] == "direct"
    print("정책 이벤트 38개 분류 self-test 통과")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="2015-01-01부터 전체 이력을 다시 조회")
    parser.add_argument("--lookback-days", type=int, default=None)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0

    config = load_config()
    configured_start = str(config.get("startDate") or "2015-01-01")
    today = datetime.now(KST).date()
    lookback = max(1, int(args.lookback_days or config.get("incrementalLookbackDays") or 90))
    minimum_date = configured_start if args.full else max(
        date.fromisoformat(configured_start), today - timedelta(days=lookback)
    ).isoformat()
    mode = "full" if args.full else "incremental"

    collected: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    for source in load_climate_sources():
        rows, audit = crawl_climate(
            source,
            config,
            minimum_date=minimum_date,
            require_cutoff_crossing=args.full,
        )
        collected.extend(rows)
        audits.append(audit)
        print(f"{audit['source']}: 확대 이벤트 {len(rows)}건", flush=True)

    motie_rows, motie_audits = collect_motie(
        config,
        minimum_date=minimum_date,
        end_date=today.isoformat(),
    )
    collected.extend(motie_rows)
    audits.extend(motie_audits)
    print(f"산업부 확대 이벤트 합계 {len(motie_rows)}건", flush=True)

    krx_rows, krx_audit = load_krx_items(config, configured_start)
    collected.extend(krx_rows)
    audits.append(krx_audit)
    print(f"한국거래소 공지 재분류 {len(krx_rows)}건", flush=True)

    previous = valid_previous_items(config, configured_start)
    merged: dict[str, dict[str, Any]] = {}
    for item in [*previous, *collected]:
        key = item_key(item)
        merged[key] = merge_item(merged[key], item) if key in merged else dict(item)

    items = sorted(
        merged.values(),
        key=lambda item: (str(item.get("publishedAt", "")), str(item.get("title", ""))),
        reverse=True,
    )
    now = datetime.now(KST).isoformat(timespec="seconds")
    document = {
        "schemaVersion": "2.0",
        "taxonomyVersion": config["taxonomyVersion"],
        "generatedAt": now,
        "collectionMode": mode,
        "startDate": configured_start,
        "queriedMinimumDate": minimum_date,
        "retentionPolicy": "append-only",
        "itemCount": len(items),
        "sourceAudit": audits,
        "items": items,
    }
    summary = build_summary(document, config)
    validation = summary["validation"]
    if validation["duplicateKeys"] or validation["missingCategory"] or validation["invalidDates"]:
        raise RuntimeError(f"확대 이벤트 검증 실패: {validation}")
    if args.full and summary["earliest"] and summary["earliest"] < configured_start:
        raise RuntimeError(f"2015년 이전 자료가 포함됐습니다: {summary['earliest']}")

    changed = atomic_write(EXPANSION_PATH, document)
    atomic_write(SUMMARY_PATH, summary)
    print(
        "POLICY_EVENT_EXPANSION_RESULT="
        + json.dumps(
            {
                "changed": changed,
                "mode": mode,
                "itemCount": len(items),
                "earliest": summary["earliest"],
                "latest": summary["latest"],
                "sectionCounts": summary["sectionCounts"],
                "scopeCounts": summary["scopeCounts"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
