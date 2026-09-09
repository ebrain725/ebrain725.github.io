#!/usr/bin/env python3
"""Collect expanded industrial-ministry policy history and merge it into the dashboard."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import tempfile
import unicodedata
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from run_motie_policy_sync import resilient_request_text as request_text
from sync_motie_official_history import clean_text, parse_total_count, echoed_keyword

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "motie_policy_keywords.json"
POLICY_PATH = ROOT / "public" / "data" / "policies.json"
HISTORY_PATH = ROOT / "public" / "data" / "motie-policy-history.json"
KST = ZoneInfo("Asia/Seoul")
SCHEMA_VERSION = "2.0"
DEFAULT_START_DATE = "2015-01-01"
DEFAULT_LOOKBACK_DAYS = 60
ROW_PAGE_COUNT = 100
MAX_QUERY_PAGES = 500
VALID_SECTIONS = {"motie_press", "motie_notice"}

SOURCES: tuple[dict[str, str], ...] = (
    {"key":"press_release","section":"motie_press","label":"산업부 보도자료","category":"보도·참고자료","board":"ATCL3f49a5a8c"},
    {"key":"press_explanation","section":"motie_press","label":"산업부 보도자료","category":"보도설명자료","board":"ATCLe0854704d"},
    {"key":"notice","section":"motie_notice","label":"산업부 공지사항","category":"공지사항","board":"ATCL6e90bb9de"},
    {"key":"business_notice","section":"motie_notice","label":"산업부 공지사항","category":"사업공고","board":"ATCL2826a2625"},
    {"key":"legislation_notice","section":"motie_notice","label":"산업부 공지사항","category":"입법예고","board":"ATCLa1cb24c71"},
    {"key":"administrative_notice","section":"motie_notice","label":"산업부 공지사항","category":"행정예고","board":"ATCLa6723dc7b"},
    {"key":"public_notice","section":"motie_notice","label":"산업부 공지사항","category":"고시","board":"ATCL0c554f816"},
    {"key":"announcement","section":"motie_notice","label":"산업부 공지사항","category":"공고","board":"ATCLc01b2801b"},
)
SOURCE_BY_KEY = {source["key"]: source for source in SOURCES}
SOURCE_BY_BOARD = {source["board"]: source for source in SOURCES}


def source_url(source: dict[str, str]) -> str:
    return f"https://www.motir.go.kr/kor/article/{source['board']}"


def article_url(source: dict[str, str], source_id: str) -> str:
    return f"{source_url(source)}/{source_id}/view"


def dedupe(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(clean_text(value) for value in values if clean_text(value)))


def atomic_write(path: Path, document: dict[str, Any]) -> bool:
    content = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)
    return True


def load_keywords() -> tuple[list[str], list[str]]:
    document = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    core = dedupe(document.get("titleAndContent", []))
    broad = [value for value in dedupe(document.get("titleOnly", [])) if value not in core]
    if not core:
        raise RuntimeError("산업부 핵심 검색어가 없습니다.")
    return core, broad


def normalize_match(value: Any) -> str:
    text = unicodedata.normalize("NFKC", clean_text(value)).lower()
    return re.sub(r"[^0-9a-z가-힣]+", "", text)


def title_matches(title: str, keywords: list[str]) -> list[str]:
    normalized = normalize_match(title)
    return [keyword for keyword in keywords if normalize_match(keyword) in normalized]


def query_url(source: dict[str, str], keyword: str, condition: str, page: int, start: str, end: str) -> str:
    params = {
        "mno":"", "pageIndex":str(page), "rowPageC":str(ROW_PAGE_COUNT), "searchCategory":"0",
        "startDtD":start, "endDtD":end, "searchCondition":condition, "searchKeyword":keyword,
    }
    return f"{source_url(source)}?{urllib.parse.urlencode(params)}"


def parse_rows(document: str, source: dict[str, str]) -> list[dict[str, Any]]:
    board = source["board"]
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row_html in re.findall(r"<tr\b[^>]*>(.*?)</tr>", document, re.I | re.S):
        anchor = re.search(
            rf"<a\b[^>]*href=[\"']([^\"']*/kor/article/{re.escape(board)}/(\d+)/view[^\"']*)[\"'][^>]*>(.*?)</a>",
            row_html, re.I | re.S,
        )
        if not anchor:
            continue
        source_id = anchor.group(2)
        if source_id in seen:
            continue
        title = clean_text(anchor.group(3))
        dates = re.findall(r"20\d{2}-\d{2}-\d{2}", clean_text(row_html))
        if not title or not dates:
            continue
        raw_cells = re.findall(r"<td\b[^>]*>(.*?)</td>", row_html, re.I | re.S)
        cells = [clean_text(value) for value in raw_cells]
        title_index = next((index for index, value in enumerate(raw_cells) if source_id in value and "/view" in value), -1)
        preceding = cells[title_index - 1] if title_index > 0 else ""
        following = cells[title_index + 1] if 0 <= title_index + 1 < len(cells) else ""
        category = source["category"]
        if source["key"] in {"press_release", "notice"} and preceding and not re.fullmatch(r"[\d,.-]+", preceding):
            category = preceding
        department = ""
        for candidate in (following, *(cells[title_index + 2:] if title_index >= 0 else [])):
            if candidate and not re.fullmatch(r"20\d{2}-\d{2}-\d{2}|[\d,.-]+", candidate):
                department = candidate
                break
        summary = " · ".join(value for value in (department + " 담당" if department else "", category) if value)
        summary = f"{summary}입니다. 원문에서 세부 내용을 확인하세요."
        rows.append({
            "id": f"{source['section']}-{board}-{source_id}", "sourceId":source_id,
            "sourceBoard":board, "sourceBoardKey":source["key"], "boardCategory":source["category"],
            "title":title, "summary":summary, "url":article_url(source, source_id),
            "publishedAt":dates[-1], "source":source["label"], "sourceType":"official",
            "section":source["section"], "category":category, "department":department,
            "matchedKeywords":[], "matchedFields":[],
        })
        seen.add(source_id)
    return rows


def merge_candidate(merged: dict[str, dict[str, Any]], item: dict[str, Any], keyword: str, field: str) -> None:
    key = f"{item['sourceBoard']}|{item['sourceId']}"
    previous = merged.get(key)
    if previous is None:
        item["matchedKeywords"] = [keyword]
        item["matchedFields"] = [field]
        merged[key] = item
        return
    previous["matchedKeywords"] = dedupe([*previous.get("matchedKeywords", []), keyword])
    previous["matchedFields"] = dedupe([*previous.get("matchedFields", []), field])


def collect_query(source: dict[str, str], keyword: str, condition: str, field: str, start: str, end: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    first = request_text(query_url(source, keyword, condition, 1, start, end))
    if echoed_keyword(first) != keyword:
        raise RuntimeError(f"산업부 검색조건 미적용: {source['key']}/{field}/{keyword}")
    total = parse_total_count(first)
    first_rows = parse_rows(first, source)
    if total > 0 and not first_rows:
        raise RuntimeError(f"산업부 검색결과 파싱 실패: {source['key']}/{field}/{keyword}/{total}")
    page_size = max(1, len(first_rows))
    pages = max(1, math.ceil(total / page_size)) if total else 1
    if pages > MAX_QUERY_PAGES:
        raise RuntimeError(f"산업부 검색페이지 과다: {source['key']}/{keyword}/{pages}")
    rows: list[dict[str, Any]] = []
    previous_ids: tuple[str, ...] | None = None
    for page in range(1, pages + 1):
        document = first if page == 1 else request_text(query_url(source, keyword, condition, page, start, end))
        page_rows = first_rows if page == 1 else parse_rows(document, source)
        ids = tuple(row["sourceId"] for row in page_rows)
        if page > 1 and ids and ids == previous_ids:
            raise RuntimeError(f"산업부 페이지 반복: {source['key']}/{keyword}/{page}")
        previous_ids = ids
        rows.extend(row for row in page_rows if start <= row["publishedAt"] <= end)
        if total == 0 or not page_rows:
            break
    return rows, {"keyword":keyword, "field":field, "reportedTotal":total, "pagesFetched":pages, "parsedRows":len(rows)}


def collect_source(source: dict[str, str], core: list[str], broad: list[str], start: str, end: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    queries: list[dict[str, Any]] = []
    search_plan = [(keyword, "1", "title") for keyword in [*core, *broad]]
    search_plan += [(keyword, "2", "content") for keyword in core]
    for keyword, condition, field in search_plan:
        rows, audit = collect_query(source, keyword, condition, field, start, end)
        queries.append(audit)
        for item in rows:
            merge_candidate(merged, item, keyword, field)
        print(f"{source['category']}: {field}/{keyword} {audit['reportedTotal']}건, 누적 {len(merged)}건", flush=True)
    items = sorted(merged.values(), key=lambda item:(item["publishedAt"], item["title"]), reverse=True)
    return items, {
        "sourceBoardKey":source["key"], "source":source["label"], "section":source["section"],
        "boardCategory":source["category"], "boardUrl":source_url(source),
        "requestedStartDate":start, "requestedEndDate":end, "searchMode":"official-expanded-keyword-query",
        "pagesFetched":sum(int(query["pagesFetched"]) for query in queries),
        "parsedRows":sum(int(query["parsedRows"]) for query in queries), "itemCount":len(items),
        "earliest":min((item["publishedAt"] for item in items), default=None),
        "latest":max((item["publishedAt"] for item in items), default=None), "complete":True, "queries":queries,
    }


def item_key(item: dict[str, Any]) -> str:
    return f"{item.get('section')}|{item.get('sourceBoard')}|{item.get('sourceId')}"


def normalize_item(raw: Any, start: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    board = clean_text(raw.get("sourceBoard"))
    source = SOURCE_BY_BOARD.get(board)
    if not source:
        old_section = clean_text(raw.get("section")).lower()
        old_url = clean_text(raw.get("url"))
        source = next((candidate for candidate in SOURCES if candidate["section"] == old_section and candidate["board"] in old_url), None)
    if not source:
        return None
    source_id = clean_text(raw.get("sourceId"))
    title = clean_text(raw.get("title"))
    published = clean_text(raw.get("publishedAt"))[:10]
    if not source_id or not title or not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", published) or published < start:
        return None
    item = dict(raw)
    item.update({
        "id":f"{source['section']}-{source['board']}-{source_id}", "sourceId":source_id,
        "sourceBoard":source["board"], "sourceBoardKey":source["key"], "boardCategory":source["category"],
        "title":title, "summary":clean_text(raw.get("summary")) or f"{source['category']}입니다. 원문에서 세부 내용을 확인하세요.",
        "url":article_url(source, source_id), "publishedAt":published, "source":source["label"],
        "sourceType":"official", "section":source["section"],
        "category":clean_text(raw.get("category")) or source["category"],
        "department":clean_text(raw.get("department")),
        "matchedKeywords":dedupe(raw.get("matchedKeywords", [])),
        "matchedFields":dedupe(raw.get("matchedFields", [])),
    })
    return item


def load_history(start: str) -> dict[str, Any]:
    if not HISTORY_PATH.exists():
        return {"schemaVersion":SCHEMA_VERSION, "requestedStartDate":start, "items":[]}
    return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))


def merge_history(previous: list[Any], collected: list[dict[str, Any]], start: str, full: bool) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    if not full:
        for raw in previous:
            item = normalize_item(raw, start)
            if item:
                merged[item_key(item)] = item
    for raw in collected:
        item = normalize_item(raw, start)
        if not item:
            continue
        key = item_key(item)
        if key in merged:
            old = merged[key]
            item["matchedKeywords"] = dedupe([*old.get("matchedKeywords", []), *item.get("matchedKeywords", [])])
            item["matchedFields"] = dedupe([*old.get("matchedFields", []), *item.get("matchedFields", [])])
        merged[key] = item
    return sorted(merged.values(), key=lambda item:(item["publishedAt"], item["title"], item["sourceBoard"]), reverse=True)


def coverage(items: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for section in sorted(VALID_SECTIONS):
        selected = [item for item in items if item["section"] == section]
        dates = [item["publishedAt"] for item in selected]
        categories: dict[str, int] = {}
        for item in selected:
            category = item["boardCategory"]
            categories[category] = categories.get(category, 0) + 1
        result[section] = {
            "label":"산업부 보도자료" if section == "motie_press" else "산업부 공지사항",
            "records":len(selected), "earliest":min(dates) if dates else None,
            "latest":max(dates) if dates else None, "byBoardCategory":dict(sorted(categories.items())),
        }
    return result


def validate(history: dict[str, Any], start: str) -> dict[str, Any]:
    if history.get("schemaVersion") != SCHEMA_VERSION or history.get("requestedStartDate") != start:
        raise RuntimeError("산업부 이력 스키마 또는 기준일 오류")
    items = history.get("items")
    if not isinstance(items, list):
        raise RuntimeError("산업부 이력 items 오류")
    counts = {section:0 for section in VALID_SECTIONS}
    board_counts = {source["key"]:0 for source in SOURCES}
    seen: set[str] = set()
    for index, raw in enumerate(items):
        item = normalize_item(raw, start)
        if not item:
            raise RuntimeError(f"산업부 이력 items[{index}] 오류")
        key = item_key(item)
        if key in seen:
            raise RuntimeError(f"산업부 이력 중복: {key}")
        seen.add(key)
        counts[item["section"]] += 1
        board_counts[item["sourceBoardKey"]] += 1
    if counts["motie_press"] <= 0 or counts["motie_notice"] <= 0:
        raise RuntimeError(f"산업부 구분별 자료가 비었습니다: {counts}")
    audits = history.get("sourceAudit")
    if not isinstance(audits, list) or {audit.get("sourceBoardKey") for audit in audits} != set(SOURCE_BY_KEY):
        raise RuntimeError("산업부 게시판별 수집 감사정보 오류")
    if any(audit.get("complete") is not True for audit in audits):
        raise RuntimeError("완료되지 않은 산업부 게시판 수집")
    return {"itemCount":len(items), "counts":counts, "boardCounts":board_counts, "duplicateKeys":0}


def is_motie(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    section = clean_text(item.get("section")).lower()
    source_name = clean_text(item.get("source"))
    url = clean_text(item.get("url"))
    return section in VALID_SECTIONS or source_name in {"산업부 보도자료", "산업부 공지사항"} or any(
        f"/kor/article/{source['board']}/" in url for source in SOURCES
    )


def merge_policy(history: dict[str, Any]) -> dict[str, Any]:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    if not isinstance(policy, dict) or not isinstance(policy.get("items"), list):
        raise RuntimeError("policies.json 형식 오류")
    items = history["items"]
    policy["items"] = sorted(
        [*[item for item in policy["items"] if not is_motie(item)], *items],
        key=lambda item:(str(item.get("publishedAt", "")), str(item.get("title", ""))), reverse=True,
    )
    policy["motieOfficialHistory"] = {
        "file":"data/motie-policy-history.json", "schemaVersion":SCHEMA_VERSION,
        "requestedStartDate":history["requestedStartDate"], "generatedAt":history["generatedAt"],
        "coverage":history["coverage"], "itemCount":len(items),
        "sourceBoards":[{"key":source["key"], "category":source["category"], "url":source_url(source)} for source in SOURCES],
    }
    changed = atomic_write(POLICY_PATH, policy)
    return {"changed":changed, "motieCount":len(items), "dashboardItems":len(policy["items"]), "policyBytes":POLICY_PATH.stat().st_size}


def self_test() -> None:
    source = SOURCE_BY_KEY["business_notice"]
    sample = '''<html><body><div>전체 1건</div><input name="searchKeyword" value="온실가스"><table><tr>
    <td>2025-525</td><td><a href="/kor/article/ATCL2826a2625/70435/view">2025년 온실가스 국제감축사업 변경 공고</a></td>
    <td>투자정책과</td><td>2025-07-21</td><td>1,752</td></tr></table></body></html>'''
    rows = parse_rows(sample, source)
    assert parse_total_count(sample) == 1 and echoed_keyword(sample) == "온실가스"
    assert len(rows) == 1 and rows[0]["department"] == "투자정책과"
    assert title_matches(rows[0]["title"], ["온실가스", "배출권"]) == ["온실가스"]
    print("MOTIE_EXPANDED_SELF_TEST=PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", args.start_date):
        raise RuntimeError("--start-date는 YYYY-MM-DD 형식이어야 합니다.")
    today = date.today()
    query_end = today.isoformat()
    query_start = args.start_date if args.full else max(
        datetime.strptime(args.start_date, "%Y-%m-%d").date(),
        today - timedelta(days=max(1, min(args.lookback_days, 365))),
    ).isoformat()
    core, broad = load_keywords()
    collected: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    for source in SOURCES:
        items, audit = collect_source(source, core, broad, query_start, query_end)
        collected.extend(items)
        audits.append(audit)
    previous = load_history(args.start_date)
    items = merge_history(previous.get("items", []), collected, args.start_date, args.full)
    history = {
        "schemaVersion":SCHEMA_VERSION, "requestedStartDate":args.start_date, "items":items,
        "generatedAt":datetime.now(KST).isoformat(timespec="seconds"),
        "collectionMode":"full" if args.full else "incremental", "queryStartDate":query_start,
        "queryEndDate":query_end, "policyKeywords":{"titleAndContent":core, "titleOnly":broad},
        "coverage":coverage(items), "sourceAudit":audits,
    }
    validation = validate(history, args.start_date)
    history_changed = atomic_write(HISTORY_PATH, history)
    merged = merge_policy(history)
    result = {"historyChanged":history_changed, "historyBytes":HISTORY_PATH.stat().st_size,
              "collectionMode":history["collectionMode"], "queryStartDate":query_start,
              "queryEndDate":query_end, "coverage":history["coverage"],
              "validation":validation, "merge":merged, "sourceAudit":audits}
    print("MOTIE_EXPANDED_RESULT=" + json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
