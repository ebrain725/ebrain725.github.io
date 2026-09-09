#!/usr/bin/env python3
"""Backfill ETS-related ministry materials and all KRX ETS notices since 2015.

Ministry boards are searched with the dashboard's policy keywords because those
boards contain every environmental subject. The KRX ETS notice board is already
market-specific, so every ordinary notice in the requested period is retained.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

import sync_policies as policy_core
from merge_official_policy_history import merge_files

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "config" / "settings.json"
POLICY_PATH = ROOT / "public" / "data" / "policies.json"
HISTORY_PATH = ROOT / "public" / "data" / "policy-official-history.json"
DEFAULT_START_DATE = "2015-01-01"
MINISTRY_PAGE_SIZE = 50
MINISTRY_MAX_PAGES_PER_KEYWORD = 500
KRX_MAX_PAGES = 500
KRX_DETAIL_WORKERS = 6
T = TypeVar("T")


def retry(label: str, operation: Callable[[], T], attempts: int = 3) -> T:
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:  # noqa: BLE001 - network boundary
            errors.append(f"{attempt}/{attempts}: {exc}")
            if attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), 4))
    raise RuntimeError(f"{label} 실패: {' | '.join(errors)}")


def strict_date(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = parsedate_to_datetime(raw.replace(" KST ", " +0900 "))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=policy_core.KST)
        return parsed.astimezone(policy_core.KST).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        pass
    match = re.search(r"(20\d{2})[./-]?(\d{1,2})[./-]?(\d{1,2})", raw)
    if not match:
        return ""
    try:
        return datetime(*map(int, match.groups())).date().isoformat()
    except ValueError:
        return ""


def ministry_search_url(source_url: str, keyword: str, offset: int) -> str:
    parsed = urllib.parse.urlsplit(source_url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    query.update(
        {
            "searchKey": "titleOrContent",
            "searchValue": keyword,
            "maxPageItems": str(MINISTRY_PAGE_SIZE),
            "pagerOffset": str(offset),
        }
    )
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment)
    )


def ministry_rows(root: Any, source: dict[str, Any], keyword: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for xml_item in root.findall(".//item"):
        title = policy_core.clean_html(policy_core.child_text(xml_item, "title"), 180)
        description = policy_core.clean_html(
            policy_core.child_text(xml_item, "description"), 2_000
        )
        link = re.sub(
            r";jsessionid=[^?&#]+",
            "",
            html.unescape(policy_core.child_text(xml_item, "link")),
            flags=re.IGNORECASE,
        ).strip()
        published = strict_date(
            policy_core.child_text(xml_item, "pubDate")
            or policy_core.child_text(xml_item, "date")
        )
        if not title or not published:
            continue
        material = f"{title} {description}"
        stable = link or f"{source.get('type')}|{published}|{title}"
        rows.append(
            {
                "id": hashlib.sha1(stable.encode("utf-8")).hexdigest()[:16],
                "publishedAt": published,
                "title": title,
                "category": policy_core.category_for(material),
                "summary": description or "원문에서 세부 내용을 확인하세요.",
                "source": str(source.get("name") or "기후부 공식자료"),
                "sourceType": "official",
                "section": policy_core.source_section(source),
                "url": link,
                "matchedKeywords": [keyword],
            }
        )
    return rows


def merge_keyword_item(previous: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    richer = (
        candidate
        if len(str(candidate.get("summary", ""))) >= len(str(previous.get("summary", "")))
        else previous
    )
    merged = dict(richer)
    keywords: list[str] = []
    for item in (previous, candidate):
        values = item.get("matchedKeywords", [])
        if isinstance(values, list):
            keywords.extend(str(value).strip() for value in values if str(value).strip())
    merged["matchedKeywords"] = list(dict.fromkeys(keywords))
    return merged


def ministry_item_key(item: dict[str, Any]) -> str:
    url = str(item.get("url", "")).strip()
    if url:
        return f"{item.get('section')}|{url}"
    title = re.sub(r"\s+", " ", str(item.get("title", ""))).strip().lower()
    return f"{item.get('section')}|{item.get('publishedAt')}|{title}"


def collect_ministry_source(
    source: dict[str, Any],
    keywords: list[str],
    start_date: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    collected: dict[str, dict[str, Any]] = {}
    keyword_audit: dict[str, Any] = {}

    for keyword in keywords:
        offset = 0
        pages = 0
        raw_rows = 0
        in_range_rows = 0
        page_signatures: set[str] = set()
        stop_reason = ""
        oldest_seen = ""
        newest_seen = ""
        crossed_boundary = False

        while pages < MINISTRY_MAX_PAGES_PER_KEYWORD:
            url = ministry_search_url(str(source["url"]), keyword, offset)
            root = retry(
                f"{source.get('name')} / {keyword} / offset {offset}",
                lambda url=url: policy_core.fetch_rss(url),
            )
            rows = ministry_rows(root, source, keyword)
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
            if signature in page_signatures:
                dates = sorted(str(row.get("publishedAt", "")) for row in rows)
                if dates and dates[-1] < start_date:
                    stop_reason = "boundary-repeated-page"
                    break
                raise RuntimeError(
                    f"{source.get('name')} '{keyword}' 검색에서 pagerOffset={offset}가 반복 페이지를 반환했습니다."
                )
            page_signatures.add(signature)

            page_dates = sorted(
                str(row.get("publishedAt", ""))
                for row in rows
                if str(row.get("publishedAt", ""))
            )
            raw_rows += len(rows)
            if page_dates:
                oldest_seen = page_dates[0] if not oldest_seen else min(oldest_seen, page_dates[0])
                newest_seen = page_dates[-1] if not newest_seen else max(newest_seen, page_dates[-1])

            page_in_range = 0
            for row in rows:
                if str(row.get("publishedAt", "")) < start_date:
                    crossed_boundary = True
                    continue
                page_in_range += 1
                key = ministry_item_key(row)
                collected[key] = (
                    merge_keyword_item(collected[key], row) if key in collected else row
                )
            in_range_rows += page_in_range

            # Fetch one page beyond the first mixed boundary page. This avoids a
            # pinned/irregular old row causing an early stop while still proving
            # that the chronological archive has crossed the requested date.
            if page_dates and page_dates[-1] < start_date:
                stop_reason = "date-boundary"
                break
            if crossed_boundary and page_in_range == 0:
                stop_reason = "date-boundary"
                break

            next_offset = offset + len(rows)
            if next_offset <= offset:
                raise RuntimeError(
                    f"{source.get('name')} '{keyword}' 검색 offset이 증가하지 않았습니다."
                )
            offset = next_offset
        else:
            raise RuntimeError(
                f"{source.get('name')} '{keyword}' 검색이 {MINISTRY_MAX_PAGES_PER_KEYWORD}페이지 한도를 초과했습니다."
            )

        keyword_audit[keyword] = {
            "pagesFetched": pages,
            "rawRows": raw_rows,
            "inRangeRows": in_range_rows,
            "newestSeen": newest_seen or None,
            "oldestSeen": oldest_seen or None,
            "crossedRequestedStart": bool(oldest_seen and oldest_seen < start_date),
            "stopReason": stop_reason,
            "complete": stop_reason in {
                "exhausted",
                "date-boundary",
                "boundary-repeated-page",
            },
        }
        print(
            f"{source.get('name')} / {keyword}: {pages}페이지, "
            f"범위내 {in_range_rows}건, 종료={stop_reason}",
            flush=True,
        )

    items = sorted(
        collected.values(),
        key=lambda item: (str(item.get("publishedAt", "")), str(item.get("title", ""))),
        reverse=True,
    )
    dates = [str(item.get("publishedAt", "")) for item in items]
    audit = {
        "source": str(source.get("name") or "기후부 공식자료"),
        "section": policy_core.source_section(source),
        "requestedStartDate": start_date,
        "itemCount": len(items),
        "earliest": min(dates) if dates else None,
        "latest": max(dates) if dates else None,
        "keywords": keyword_audit,
        "complete": all(value.get("complete") for value in keyword_audit.values()),
    }
    return items, audit


def krx_list_payload(page: int) -> str:
    return policy_core.fetch_krx_board_html(
        "list",
        {
            "bbsId": "OPN03010000T8",
            "bbsUrl": "ETS01030000",
            "curPage": page,
            "searchType": "",
            "bbsSeq": "",
            "boardStyle": "normal",
            "language": "ko",
            "srchTitle": "",
            "srchWord": "",
            "srchWord1": "",
        },
    )


def collect_krx_rows(start_date: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows_by_id: dict[str, dict[str, Any]] = {}
    page_signatures: set[str] = set()
    pages = 0
    raw_rows = 0
    stop_reason = ""
    oldest_seen = ""
    newest_seen = ""

    for page in range(1, KRX_MAX_PAGES + 1):
        payload = retry(
            f"한국거래소 공지 목록 {page}페이지",
            lambda page=page: krx_list_payload(page),
        )
        rows = policy_core.krx_notice_rows(payload)
        pages += 1
        if not rows:
            stop_reason = "exhausted"
            break

        signature = hashlib.sha1(
            "|".join(str(row.get("sourceId")) for row in rows).encode("utf-8")
        ).hexdigest()
        if signature in page_signatures:
            dates = sorted(str(row.get("publishedAt", "")) for row in rows)
            if dates and dates[-1] < start_date:
                stop_reason = "boundary-repeated-page"
                break
            raise RuntimeError(f"한국거래소 공지 {page}페이지가 이전 페이지와 반복됩니다.")
        page_signatures.add(signature)

        raw_rows += len(rows)
        page_dates = sorted(str(row.get("publishedAt", "")) for row in rows)
        if page_dates:
            oldest_seen = page_dates[0] if not oldest_seen else min(oldest_seen, page_dates[0])
            newest_seen = page_dates[-1] if not newest_seen else max(newest_seen, page_dates[-1])

        for row in rows:
            if str(row.get("publishedAt", "")) >= start_date:
                rows_by_id.setdefault(str(row["sourceId"]), row)

        if page_dates and page_dates[-1] < start_date:
            stop_reason = "date-boundary"
            break
        if page % 10 == 0:
            print(
                f"한국거래소 공지: {page}페이지, 범위내 {len(rows_by_id)}건",
                flush=True,
            )
    else:
        raise RuntimeError(f"한국거래소 공지 목록이 {KRX_MAX_PAGES}페이지 한도를 초과했습니다.")

    rows = sorted(
        rows_by_id.values(),
        key=lambda row: (str(row.get("publishedAt", "")), int(row.get("sourceId", 0))),
        reverse=True,
    )
    audit = {
        "source": "한국거래소 배출권 공지사항",
        "section": "krx_notice",
        "requestedStartDate": start_date,
        "pagesFetched": pages,
        "rawRows": raw_rows,
        "itemCount": len(rows),
        "earliest": min((str(row.get("publishedAt", "")) for row in rows), default=None),
        "latest": max((str(row.get("publishedAt", "")) for row in rows), default=None),
        "oldestSeen": oldest_seen or None,
        "newestSeen": newest_seen or None,
        "crossedRequestedStart": bool(oldest_seen and oldest_seen < start_date),
        "stopReason": stop_reason,
        "complete": stop_reason in {
            "exhausted",
            "date-boundary",
            "boundary-repeated-page",
        },
    }
    return rows, audit


def fetch_krx_detail(row: dict[str, Any]) -> tuple[str, str, str | None]:
    source_id = str(row["sourceId"])
    try:
        summary = retry(
            f"한국거래소 공지 {source_id} 상세",
            lambda: policy_core.krx_notice_summary(source_id),
            attempts=3,
        )
        return source_id, summary, None
    except Exception as exc:  # retain title/date even if one legacy detail is unavailable
        return source_id, "", str(exc)


def enrich_krx_items(
    rows: list[dict[str, Any]],
    keywords: list[str],
    board_url: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    details: dict[str, str] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(KRX_DETAIL_WORKERS, max(len(rows), 1))) as executor:
        futures = {executor.submit(fetch_krx_detail, row): row for row in rows}
        completed = 0
        for future in as_completed(futures):
            source_id, summary, error = future.result()
            details[source_id] = summary
            if error:
                errors.append(f"{source_id}: {error}")
            completed += 1
            if completed % 25 == 0 or completed == len(rows):
                print(f"한국거래소 공지 상세: {completed}/{len(rows)}건", flush=True)

    items: list[dict[str, Any]] = []
    for row in rows:
        source_id = str(row["sourceId"])
        summary = details.get(source_id, "") or (
            "한국거래소 배출권시장 공지사항입니다. 원문에서 세부 내용을 확인하세요."
        )
        material = f"{row.get('title', '')} {summary}"
        items.append(
            {
                "id": f"krx-ets-{source_id}",
                "sourceId": source_id,
                "publishedAt": str(row["publishedAt"]),
                "title": str(row["title"]),
                "category": policy_core.category_for(material),
                "summary": summary,
                "source": "한국거래소 배출권 공지사항",
                "sourceType": "official",
                "section": "krx_notice",
                "url": f"{board_url}#view={source_id}",
                "matchedKeywords": [
                    keyword for keyword in keywords if keyword.lower() in material.lower()
                ],
                "detailStatus": "fallback" if source_id in {error.split(":", 1)[0] for error in errors} else "ok",
            }
        )
    return items, errors


def validate_items(items: list[dict[str, Any]], start_date: str) -> dict[str, Any]:
    if not items:
        raise RuntimeError("공식자료 백필 결과가 0건입니다.")
    keys: set[str] = set()
    section_counts = {"press": 0, "notice": 0, "krx_notice": 0}
    section_dates: dict[str, list[str]] = {key: [] for key in section_counts}
    for index, item in enumerate(items):
        section = str(item.get("section", ""))
        date = str(item.get("publishedAt", ""))[:10]
        title = str(item.get("title", "")).strip()
        source = str(item.get("source", "")).strip()
        if section not in section_counts:
            raise RuntimeError(f"items[{index}] 알 수 없는 section: {section}")
        if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", date) or date < start_date:
            raise RuntimeError(f"items[{index}] 날짜가 범위를 벗어납니다: {date}")
        if not title or not source:
            raise RuntimeError(f"items[{index}] 제목 또는 출처가 없습니다.")
        key = (
            f"krx|{item.get('sourceId')}"
            if section == "krx_notice"
            else f"{section}|{item.get('url') or date + '|' + title}"
        )
        if key in keys:
            raise RuntimeError(f"중복 공식자료: {key}")
        keys.add(key)
        section_counts[section] += 1
        section_dates[section].append(date)
    if any(count == 0 for count in section_counts.values()):
        raise RuntimeError(f"공식자료 구분 중 0건이 있습니다: {section_counts}")
    return {
        "itemCount": len(items),
        "counts": section_counts,
        "earliest": {key: min(values) for key, values in section_dates.items()},
        "latest": {key: max(values) for key, values in section_dates.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    args = parser.parse_args()
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", args.start_date):
        raise SystemExit("--start-date는 YYYY-MM-DD 형식이어야 합니다.")

    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    keywords = [
        str(value).strip()
        for value in settings.get("policyKeywords", [])
        if str(value).strip()
    ]
    sources = [source for source in settings.get("policySources", []) if isinstance(source, dict)]
    ministry_sources = [source for source in sources if source.get("collector") != "krxBoard"]
    krx_source = next((source for source in sources if source.get("collector") == "krxBoard"), None)
    if len(ministry_sources) < 2 or not krx_source:
        raise RuntimeError("config/settings.json의 기후부·한국거래소 공식 출처 설정이 부족합니다.")
    if not keywords:
        raise RuntimeError("config/settings.json의 policyKeywords가 비어 있습니다.")

    started_at = datetime.now(policy_core.KST).isoformat(timespec="seconds")
    all_items: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    warnings: list[str] = []

    for source in ministry_sources:
        items, audit = collect_ministry_source(source, keywords, args.start_date)
        if not audit.get("complete"):
            raise RuntimeError(f"{source.get('name')} 아카이브 순회가 완전하지 않습니다: {audit}")
        all_items.extend(items)
        audits.append(audit)

    krx_rows, krx_audit = collect_krx_rows(args.start_date)
    if not krx_audit.get("complete"):
        raise RuntimeError(f"한국거래소 공지 아카이브 순회가 완전하지 않습니다: {krx_audit}")
    board_url = str(krx_source.get("url") or "https://ets.krx.co.kr/board/ETS01030000/bbs").split("#", 1)[0]
    krx_items, detail_errors = enrich_krx_items(krx_rows, keywords, board_url)
    krx_audit["detailSuccessCount"] = len(krx_items) - len(detail_errors)
    krx_audit["detailFallbackCount"] = len(detail_errors)
    if detail_errors:
        warnings.append(
            f"한국거래소 과거 공지 상세 {len(detail_errors)}건은 제목·일자·공식링크를 보존하고 요약을 대체 문구로 저장했습니다."
        )
    all_items.extend(krx_items)
    audits.append(krx_audit)

    # Cross-keyword and existing-history duplicates are resolved by the merger,
    # but this first pass removes exact archive keys before writing the source file.
    exact: dict[str, dict[str, Any]] = {}
    for item in all_items:
        key = (
            f"krx_notice|{item.get('sourceId')}"
            if item.get("section") == "krx_notice"
            else ministry_item_key(item)
        )
        exact[key] = merge_keyword_item(exact[key], item) if key in exact else item
    collected = sorted(
        exact.values(),
        key=lambda item: (str(item.get("publishedAt", "")), str(item.get("title", ""))),
        reverse=True,
    )
    validation = validate_items(collected, args.start_date)

    history = {
        "schemaVersion": "1.0",
        "requestedStartDate": args.start_date,
        "startedAt": started_at,
        "generatedAt": datetime.now(policy_core.KST).isoformat(timespec="seconds"),
        "scope": {
            "ministry": "기후부 보도자료·공지사항 중 배출권 관련 설정 키워드가 제목 또는 본문에 포함된 공식자료",
            "krx": "한국거래소 배출권시장 공지사항 일반 게시물 전체",
            "keywords": keywords,
        },
        "sourceAudit": audits,
        "warnings": warnings,
        "coverage": validation,
        "items": collected,
    }
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(
        json.dumps(history, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    merge_result = merge_files(
        POLICY_PATH,
        HISTORY_PATH,
        start_date=args.start_date,
    )
    final_history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    final_history["sourceAudit"] = audits
    final_history["warnings"] = warnings
    final_history["scope"] = history["scope"]
    final_history["startedAt"] = started_at
    HISTORY_PATH.write_text(
        json.dumps(final_history, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    result = {
        "requestedStartDate": args.start_date,
        "collectedValidation": validation,
        "merge": merge_result,
        "warnings": warnings,
        "sourceAudit": audits,
    }
    print("OFFICIAL_POLICY_BACKFILL_RESULT=" + json.dumps(result, ensure_ascii=False))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # provide a single actionable failure line in Actions
        print(f"OFFICIAL_POLICY_BACKFILL_ERROR={exc}", file=sys.stderr)
        raise
