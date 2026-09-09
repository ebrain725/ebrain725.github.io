#!/usr/bin/env python3
"""Reliable 2015 backfill using unfiltered MCEE RSS pages.

The ministry server becomes slow or unreachable when a title/content search is
combined with deep pagination. Its unfiltered RSS archive accepts large page
sizes reliably, so this wrapper reads the chronological archive and applies the
ETS keyword filter locally. KRX collection, validation, merging and publishing
remain delegated to the audited v1 implementation.
"""

from __future__ import annotations

import hashlib
import html
import re
import subprocess
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any

import backfill_official_policy_history as legacy
import sync_policies as policy_core

PAGE_SIZE_BY_SECTION = {
    "press": 1000,
    "notice": 200,
}
MAX_PAGES_BY_SECTION = {
    "press": 50,
    "notice": 120,
}


def archive_url(source_url: str, *, offset: int, page_size: int) -> str:
    parsed = urllib.parse.urlsplit(source_url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    for key in list(query):
        if key.lower() in {
            "searchkey",
            "searchvalue",
            "maxpageitems",
            "pageroffset",
        }:
            query.pop(key, None)
    query.update(
        {
            "maxPageItems": str(page_size),
            "pagerOffset": str(offset),
        }
    )
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment)
    )


def fetch_xml(url: str) -> ET.Element:
    command = [
        "curl",
        "-4",
        "--http1.1",
        "--location",
        "--compressed",
        "--retry",
        "2",
        "--retry-delay",
        "2",
        "--retry-all-errors",
        "--connect-timeout",
        "20",
        "--max-time",
        "240",
        "--user-agent",
        "Mozilla/5.0 (compatible; ETS-LIVE-DASHBOARD/4.0)",
        "--header",
        "Accept: application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
        "--silent",
        "--show-error",
        "--fail",
        url,
    ]
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"curl exit={result.returncode}: {detail[:500]}")
    if len(result.stdout) < 100:
        raise RuntimeError(f"RSS 응답이 비정상적으로 짧습니다: {len(result.stdout)} bytes")
    try:
        return ET.fromstring(result.stdout)
    except ET.ParseError as exc:
        prefix = result.stdout[:300].decode("utf-8", errors="replace")
        raise RuntimeError(f"RSS XML 파싱 실패: {exc}; prefix={prefix!r}") from exc


def parse_page(
    root: ET.Element,
    source: dict[str, Any],
    keywords: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_rows: list[dict[str, Any]] = []
    matched_rows: list[dict[str, Any]] = []
    section = policy_core.source_section(source)

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
        published = legacy.strict_date(
            policy_core.child_text(xml_item, "pubDate")
            or policy_core.child_text(xml_item, "date")
        )
        if not title or not published:
            continue

        material = f"{title} {description}".lower()
        matched_keywords = [
            keyword for keyword in keywords if keyword.lower() in material
        ]
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
            "matchedKeywords": matched_keywords,
        }
        all_rows.append(item)
        if matched_keywords:
            matched_rows.append(item)

    return all_rows, matched_rows


def collect_ministry_source(
    source: dict[str, Any],
    keywords: list[str],
    start_date: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    section = policy_core.source_section(source)
    page_size = PAGE_SIZE_BY_SECTION.get(section, 200)
    max_pages = MAX_PAGES_BY_SECTION.get(section, 120)
    collected: dict[str, dict[str, Any]] = {}
    signatures: set[str] = set()
    pages = 0
    raw_rows = 0
    in_range_rows = 0
    keyword_hits = {keyword: 0 for keyword in keywords}
    newest_seen = ""
    oldest_seen = ""
    previous_oldest = ""
    stop_reason = ""

    for page_index in range(max_pages):
        offset = page_index * page_size
        url = archive_url(str(source["url"]), offset=offset, page_size=page_size)
        root = legacy.retry(
            f"{source.get('name')} / offset {offset}",
            lambda url=url: fetch_xml(url),
            attempts=3,
        )
        rows, matched = parse_page(root, source, keywords)
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
            date = str(item.get("publishedAt", ""))
            if date < start_date:
                continue
            key = legacy.ministry_item_key(item)
            collected[key] = (
                legacy.merge_keyword_item(collected[key], item)
                if key in collected
                else item
            )
            in_range_rows += 1
            for keyword in item.get("matchedKeywords", []):
                if keyword in keyword_hits:
                    keyword_hits[keyword] += 1

        print(
            f"{source.get('name')}: {pages}페이지 / 원문 {raw_rows}건 / "
            f"배출권 관련 고유 {len(collected)}건 / "
            f"범위 {page_oldest}~{page_newest}",
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
    audit = {
        "source": str(source.get("name") or "기후부 공식자료"),
        "section": section,
        "requestedStartDate": start_date,
        "transport": "unfiltered-rss-local-keyword-filter",
        "pageSize": page_size,
        "pagesFetched": pages,
        "rawRows": raw_rows,
        "inRangeMatchedRows": in_range_rows,
        "itemCount": len(items),
        "earliest": min((str(item.get("publishedAt", "")) for item in items), default=None),
        "latest": max((str(item.get("publishedAt", "")) for item in items), default=None),
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


def self_test() -> None:
    sample = b"""<?xml version='1.0' encoding='UTF-8'?>
    <rss><channel>
      <item><title><![CDATA[배출권거래제 시험 자료]]></title>
      <description><![CDATA[유상할당 경매 안내]]></description>
      <link>https://example.test/read?boardId=1</link>
      <pubDate>Thu Jan 02 00:00:00 KST 2025</pubDate></item>
      <item><title><![CDATA[일반 환경자료]]></title>
      <description><![CDATA[대기질 안내]]></description>
      <link>https://example.test/read?boardId=2</link>
      <pubDate>Wed Jan 01 00:00:00 KST 2025</pubDate></item>
    </channel></rss>"""
    root = ET.fromstring(sample)
    all_rows, matched = parse_page(
        root,
        {"name": "기후부 보도자료", "type": "press"},
        ["배출권", "유상할당"],
    )
    assert len(all_rows) == 2
    assert len(matched) == 1
    assert matched[0]["publishedAt"] == "2025-01-02"
    assert matched[0]["matchedKeywords"] == ["배출권", "유상할당"]
    url = archive_url(
        "https://www.mcee.go.kr/home/web/board/rss.do?menuId=10598&boardMasterId=939&searchValue=x",
        offset=1000,
        page_size=1000,
    )
    assert "searchValue" not in url
    assert "pagerOffset=1000" in url
    assert "maxPageItems=1000" in url
    print("MCEE_UNFILTERED_RSS_SELF_TEST=PASS")


def main() -> int:
    if "--self-test" in sys.argv:
        self_test()
        return 0
    legacy.collect_ministry_source = collect_ministry_source
    return legacy.main()


if __name__ == "__main__":
    raise SystemExit(main())
