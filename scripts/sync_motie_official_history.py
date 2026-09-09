#!/usr/bin/env python3
"""Collect and preserve industrial ministry official materials since 2015.

The industrial ministry website is searched by the same policy keywords used for
climate-ministry materials. Title and body searches are combined, deduplicated by
the official article identifier, and then merged into the dashboard payload.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "config" / "settings.json"
POLICY_PATH = ROOT / "public" / "data" / "policies.json"
HISTORY_PATH = ROOT / "public" / "data" / "motie-policy-history.json"
KST = ZoneInfo("Asia/Seoul")
USER_AGENT = "Mozilla/5.0 (compatible; ETS-LIVE-DASHBOARD/5.0; +https://ebrain725.github.io/)"
DEFAULT_START_DATE = "2015-01-01"
DEFAULT_LOOKBACK_DAYS = 45
REQUEST_TIMEOUT_SECONDS = 45
MAX_QUERY_PAGES = 500
MOTIE_SECTIONS = {"motie_press", "motie_notice"}

BOARDS: dict[str, dict[str, str]] = {
    "motie_press": {
        "label": "산업부 보도자료",
        "board": "ATCL3f49a5a8c",
        "url": "https://www.motir.go.kr/kor/article/ATCL3f49a5a8c",
    },
    "motie_notice": {
        "label": "산업부 공지사항",
        "board": "ATCL6e90bb9de",
        "url": "https://www.motir.go.kr/kor/article/ATCL6e90bb9de",
    },
}


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<script\b.*?</script>|<style\b.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


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


def request_text(url: str, *, attempts: int = 3) -> str:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.6",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                text = raw.decode(charset, errors="replace")
                if response.status != 200 or len(text) < 5_000:
                    raise RuntimeError(
                        f"산업부 응답 이상: status={response.status}, bytes={len(raw)}, url={url}"
                    )
                return text
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(attempt * 2)
    raise RuntimeError(f"산업부 페이지 수집 실패: {url}: {last_error}")


def canonical_article_url(board: str, source_id: str) -> str:
    return f"https://www.motir.go.kr/kor/article/{board}/{source_id}/view"


def parse_total_count(document: str) -> int:
    plain = clean_text(document)
    match = re.search(r"전체\s*([0-9,]+)\s*건", plain)
    return int(match.group(1).replace(",", "")) if match else 0


def echoed_keyword(document: str) -> str:
    match = re.search(
        r"<input\b[^>]*name=[\"']searchKeyword[\"'][^>]*value=[\"']([^\"']*)",
        document,
        flags=re.I,
    )
    return html.unescape(match.group(1)).strip() if match else ""


def parse_rows(document: str, section: str) -> list[dict[str, Any]]:
    board = BOARDS[section]["board"]
    label = BOARDS[section]["label"]
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row_html in re.findall(r"<tr\b[^>]*>(.*?)</tr>", document, flags=re.I | re.S):
        anchor = re.search(
            rf"<a\b[^>]*href=[\"']([^\"']*/kor/article/{re.escape(board)}/(\d+)/view[^\"']*)[\"'][^>]*>(.*?)</a>",
            row_html,
            flags=re.I | re.S,
        )
        if not anchor:
            continue
        source_id = anchor.group(2)
        if source_id in seen:
            continue
        title = clean_text(anchor.group(3))
        row_text = clean_text(row_html)
        dates = re.findall(r"20\d{2}-\d{2}-\d{2}", row_text)
        if not title or not dates:
            continue
        cells = [clean_text(value) for value in re.findall(r"<td\b[^>]*>(.*?)</td>", row_html, flags=re.I | re.S)]
        category = cells[1] if len(cells) >= 2 else ""
        department = cells[3] if len(cells) >= 4 else ""
        if department and re.fullmatch(r"20\d{2}-\d{2}-\d{2}", department):
            department = ""
        summary_parts = []
        if department:
            summary_parts.append(f"{department} 담당")
        if category:
            summary_parts.append(category)
        summary = " · ".join(summary_parts)
        if summary:
            summary += "입니다. 원문에서 세부 내용을 확인하세요."
        else:
            summary = f"{label}입니다. 원문에서 세부 내용을 확인하세요."
        rows.append(
            {
                "id": f"{section}-{source_id}",
                "sourceId": source_id,
                "title": title,
                "summary": summary,
                "url": canonical_article_url(board, source_id),
                "publishedAt": dates[-1],
                "source": label,
                "sourceType": "official",
                "section": section,
                "category": category,
                "department": department,
                "matchedKeywords": [],
                "matchedFields": [],
            }
        )
        seen.add(source_id)
    return rows


def build_query_url(
    section: str,
    *,
    keyword: str,
    condition: str,
    page_index: int,
    start_date: str,
    end_date: str,
) -> str:
    params = {
        "mno": "",
        "pageIndex": str(page_index),
        "rowPageC": "100",
        "searchCategory": "0",
        "startDtD": start_date,
        "endDtD": end_date,
        "searchCondition": condition,
        "searchKeyword": keyword,
    }
    return f"{BOARDS[section]['url']}?{urllib.parse.urlencode(params)}"


def merge_candidate(
    merged: dict[str, dict[str, Any]],
    candidate: dict[str, Any],
    *,
    keyword: str,
    field: str,
) -> None:
    source_id = str(candidate.get("sourceId", "")).strip()
    if not source_id:
        return
    candidate["matchedKeywords"] = [keyword]
    candidate["matchedFields"] = [field]
    previous = merged.get(source_id)
    if previous is None:
        merged[source_id] = candidate
        return
    previous["matchedKeywords"] = list(
        dict.fromkeys([*previous.get("matchedKeywords", []), keyword])
    )
    previous["matchedFields"] = list(
        dict.fromkeys([*previous.get("matchedFields", []), field])
    )
    if len(str(candidate.get("summary", ""))) > len(str(previous.get("summary", ""))):
        previous["summary"] = candidate["summary"]
    for key in ("category", "department"):
        if not previous.get(key) and candidate.get(key):
            previous[key] = candidate[key]


def collect_board(
    section: str,
    *,
    keywords: list[str],
    start_date: str,
    end_date: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    query_audit: list[dict[str, Any]] = []
    pages_fetched = 0
    parsed_rows = 0

    for keyword in keywords:
        for condition, field in (("1", "title"), ("2", "content")):
            first_url = build_query_url(
                section,
                keyword=keyword,
                condition=condition,
                page_index=1,
                start_date=start_date,
                end_date=end_date,
            )
            first_document = request_text(first_url)
            echoed = echoed_keyword(first_document)
            if echoed != keyword:
                raise RuntimeError(
                    f"산업부 검색조건이 적용되지 않았습니다: {section}/{field}/{keyword!r} -> {echoed!r}"
                )
            total = parse_total_count(first_document)
            first_rows = parse_rows(first_document, section)
            if total > 0 and not first_rows:
                raise RuntimeError(
                    f"산업부 검색 결과 파싱 실패: {section}/{field}/{keyword}, total={total}"
                )
            page_size = max(1, len(first_rows))
            total_pages = max(1, math.ceil(total / page_size)) if total else 1
            if total_pages > MAX_QUERY_PAGES:
                raise RuntimeError(
                    f"산업부 검색 페이지가 비정상적으로 많습니다: {section}/{field}/{keyword}={total_pages}"
                )

            query_rows = 0
            previous_page_ids: tuple[str, ...] | None = None
            for page_index in range(1, total_pages + 1):
                document = first_document if page_index == 1 else request_text(
                    build_query_url(
                        section,
                        keyword=keyword,
                        condition=condition,
                        page_index=page_index,
                        start_date=start_date,
                        end_date=end_date,
                    )
                )
                rows = first_rows if page_index == 1 else parse_rows(document, section)
                pages_fetched += 1
                parsed_rows += len(rows)
                query_rows += len(rows)
                page_ids = tuple(str(item.get("sourceId", "")) for item in rows)
                if page_index > 1 and page_ids and page_ids == previous_page_ids:
                    raise RuntimeError(
                        f"산업부 페이지 반복 감지: {section}/{field}/{keyword}/page={page_index}"
                    )
                previous_page_ids = page_ids
                for item in rows:
                    published = str(item.get("publishedAt", ""))[:10]
                    if start_date <= published <= end_date:
                        merge_candidate(merged, item, keyword=keyword, field=field)
                if total == 0 or not rows:
                    break
            query_audit.append(
                {
                    "keyword": keyword,
                    "field": field,
                    "reportedTotal": total,
                    "pagesFetched": min(total_pages, max(1, math.ceil(max(query_rows, 1) / page_size))),
                    "parsedRows": query_rows,
                }
            )
            print(
                f"{BOARDS[section]['label']}: {field} / {keyword} / "
                f"공식검색 {total}건 / 누적 고유 {len(merged)}건",
                flush=True,
            )

    items = sorted(
        merged.values(),
        key=lambda item: (
            str(item.get("publishedAt", "")),
            str(item.get("title", "")),
            str(item.get("sourceId", "")),
        ),
        reverse=True,
    )
    audit = {
        "source": BOARDS[section]["label"],
        "section": section,
        "boardUrl": BOARDS[section]["url"],
        "requestedStartDate": start_date,
        "requestedEndDate": end_date,
        "searchMode": "official-title-and-content-query",
        "pagesFetched": pages_fetched,
        "parsedRows": parsed_rows,
        "itemCount": len(items),
        "earliest": min((item["publishedAt"] for item in items), default=None),
        "latest": max((item["publishedAt"] for item in items), default=None),
        "complete": True,
        "queries": query_audit,
    }
    return items, audit


def item_key(item: dict[str, Any]) -> str:
    section = str(item.get("section", "")).strip()
    source_id = str(item.get("sourceId", "")).strip()
    if source_id:
        return f"{section}|{source_id}"
    url = str(item.get("url", "")).strip()
    if url:
        return f"{section}|{url}"
    normalized = re.sub(r"[^0-9a-z가-힣]+", "", clean_text(item.get("title")).lower())
    return f"{section}|{str(item.get('publishedAt', ''))[:10]}|{normalized}"


def normalize_item(item: Any, start_date: str) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    section = str(item.get("section", "")).strip().lower()
    if section not in MOTIE_SECTIONS:
        return None
    published = str(item.get("publishedAt", "")).strip()[:10]
    title = clean_text(item.get("title"))
    source_id = str(item.get("sourceId", "")).strip()
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", published):
        return None
    if published < start_date or not title or not source_id:
        return None
    board = BOARDS[section]["board"]
    normalized = dict(item)
    normalized.update(
        {
            "id": f"{section}-{source_id}",
            "sourceId": source_id,
            "title": title,
            "summary": clean_text(item.get("summary"))
            or f"{BOARDS[section]['label']}입니다. 원문에서 세부 내용을 확인하세요.",
            "url": canonical_article_url(board, source_id),
            "publishedAt": published,
            "source": BOARDS[section]["label"],
            "sourceType": "official",
            "section": section,
            "matchedKeywords": list(
                dict.fromkeys(clean_text(value) for value in item.get("matchedKeywords", []) if clean_text(value))
            ),
            "matchedFields": list(
                dict.fromkeys(clean_text(value) for value in item.get("matchedFields", []) if clean_text(value))
            ),
        }
    )
    return normalized


def merge_history_items(
    previous: list[Any],
    collected: list[dict[str, Any]],
    *,
    start_date: str,
    replace_sections: set[str] | None = None,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for raw in previous:
        item = normalize_item(raw, start_date)
        if item is None or (replace_sections and item["section"] in replace_sections):
            continue
        merged[item_key(item)] = item
    for raw in collected:
        item = normalize_item(raw, start_date)
        if item is None:
            continue
        key = item_key(item)
        if key in merged:
            old = merged[key]
            item["matchedKeywords"] = list(
                dict.fromkeys([*old.get("matchedKeywords", []), *item.get("matchedKeywords", [])])
            )
            item["matchedFields"] = list(
                dict.fromkeys([*old.get("matchedFields", []), *item.get("matchedFields", [])])
            )
        merged[key] = item
    return sorted(
        merged.values(),
        key=lambda item: (
            str(item.get("publishedAt", "")),
            str(item.get("title", "")),
            str(item.get("sourceId", "")),
        ),
        reverse=True,
    )


def coverage(items: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for section in sorted(MOTIE_SECTIONS):
        dates = [item["publishedAt"] for item in items if item.get("section") == section]
        result[section] = {
            "label": BOARDS[section]["label"],
            "records": len(dates),
            "earliest": min(dates) if dates else None,
            "latest": max(dates) if dates else None,
        }
    return result


def is_motie_item(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    section = str(item.get("section", "")).strip().lower()
    source = str(item.get("source", ""))
    url = str(item.get("url", ""))
    return (
        section in MOTIE_SECTIONS
        or source in {"산업부 보도자료", "산업부 공지사항"}
        or "motir.go.kr/kor/article/ATCL3f49a5a8c" in url
        or "motir.go.kr/kor/article/ATCL6e90bb9de" in url
    )


def remove_from_policy(policy_path: Path = POLICY_PATH) -> dict[str, Any]:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    items = policy.get("items", [])
    if not isinstance(policy, dict) or not isinstance(items, list):
        raise RuntimeError("policies.json 형식이 올바르지 않습니다.")
    before = len(items)
    policy["items"] = [item for item in items if not is_motie_item(item)]
    policy.pop("motieOfficialHistory", None)
    changed = atomic_write(policy_path, policy)
    return {"changed": changed, "removed": before - len(policy["items"])}


def merge_into_policy(
    history: dict[str, Any],
    policy_path: Path = POLICY_PATH,
) -> dict[str, Any]:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if not isinstance(policy, dict) or not isinstance(policy.get("items"), list):
        raise RuntimeError("policies.json 형식이 올바르지 않습니다.")
    history_items = history.get("items", [])
    if not isinstance(history_items, list):
        raise RuntimeError("산업부 이력 items 형식이 올바르지 않습니다.")
    others = [item for item in policy["items"] if not is_motie_item(item)]
    policy["items"] = sorted(
        [*others, *history_items],
        key=lambda item: (
            str(item.get("publishedAt", "")),
            str(item.get("title", "")),
        ),
        reverse=True,
    )
    policy["motieOfficialHistory"] = {
        "file": "data/motie-policy-history.json",
        "requestedStartDate": history.get("requestedStartDate"),
        "generatedAt": history.get("generatedAt"),
        "coverage": history.get("coverage"),
        "itemCount": len(history_items),
    }
    changed = atomic_write(policy_path, policy)
    return {
        "changed": changed,
        "motieCount": len(history_items),
        "dashboardItems": len(policy["items"]),
        "policyBytes": policy_path.stat().st_size,
    }


def load_keywords() -> list[str]:
    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    values = [clean_text(value) for value in settings.get("policyKeywords", [])]
    keywords = list(dict.fromkeys(value for value in values if value))
    if not keywords:
        raise RuntimeError("config/settings.json에 policyKeywords가 없습니다.")
    return keywords


def load_history(start_date: str) -> dict[str, Any]:
    if not HISTORY_PATH.exists():
        return {
            "schemaVersion": "1.0",
            "requestedStartDate": start_date,
            "items": [],
        }
    document = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("items"), list):
        raise RuntimeError("motie-policy-history.json 형식이 올바르지 않습니다.")
    return document


def validate_history(document: dict[str, Any], start_date: str) -> dict[str, Any]:
    items = document.get("items", [])
    if not isinstance(items, list):
        raise RuntimeError("산업부 이력 items가 배열이 아닙니다.")
    seen: set[str] = set()
    counts = {section: 0 for section in MOTIE_SECTIONS}
    for index, raw in enumerate(items):
        item = normalize_item(raw, start_date)
        if item is None:
            raise RuntimeError(f"산업부 이력 items[{index}] 형식 오류")
        key = item_key(item)
        if key in seen:
            raise RuntimeError(f"산업부 이력 중복: {key}")
        seen.add(key)
        counts[item["section"]] += 1
        if urllib.parse.urlsplit(item["url"]).hostname not in {"www.motir.go.kr", "motir.go.kr"}:
            raise RuntimeError(f"산업부 공식 도메인이 아닌 URL: {item['url']}")
    if any(value <= 0 for value in counts.values()):
        raise RuntimeError(f"산업부 구분별 수집 결과가 비었습니다: {counts}")
    return {"itemCount": len(items), "counts": counts, "duplicateKeys": 0}


def run_collection(*, start_date: str, full: bool, lookback_days: int) -> dict[str, Any]:
    keywords = load_keywords()
    history = load_history(start_date)
    previous_items = history.get("items", [])
    has_complete_history = bool(previous_items) and history.get("requestedStartDate") == start_date
    effective_full = full or not has_complete_history
    end_date = datetime.now(KST).date().isoformat()
    query_start = start_date if effective_full else max(
        date.fromisoformat(start_date), datetime.now(KST).date() - timedelta(days=lookback_days)
    ).isoformat()

    collected: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    for section in ("motie_press", "motie_notice"):
        items, audit = collect_board(
            section,
            keywords=keywords,
            start_date=query_start,
            end_date=end_date,
        )
        collected.extend(items)
        audits.append(audit)

    merged_items = merge_history_items(
        previous_items,
        collected,
        start_date=start_date,
        replace_sections=MOTIE_SECTIONS if effective_full else None,
    )
    generated_at = datetime.now(KST).isoformat(timespec="seconds")
    history.update(
        {
            "schemaVersion": "1.0",
            "requestedStartDate": start_date,
            "generatedAt": generated_at,
            "collectionMode": "full" if effective_full else "incremental",
            "queryStartDate": query_start,
            "queryEndDate": end_date,
            "policyKeywords": keywords,
            "coverage": coverage(merged_items),
            "sourceAudit": audits,
            "items": merged_items,
        }
    )
    validation = validate_history(history, start_date)
    history["validation"] = validation
    history_changed = atomic_write(HISTORY_PATH, history)
    merge_result = merge_into_policy(history)
    result = {
        "historyChanged": history_changed,
        "historyBytes": HISTORY_PATH.stat().st_size,
        "collectionMode": history["collectionMode"],
        "queryStartDate": query_start,
        "queryEndDate": end_date,
        "coverage": history["coverage"],
        "validation": validation,
        "merge": merge_result,
        "sourceAudit": audits,
    }
    print("MOTIE_POLICY_RESULT=" + json.dumps(result, ensure_ascii=False), flush=True)
    return result


def self_test() -> None:
    sample = """
    <input type="text" name="searchKeyword" value="배출권" />
    <div>전체 1건</div>
    <table><tbody><tr>
      <td>1</td><td>보도자료</td>
      <td><a href="/kor/article/ATCL3f49a5a8c/170001/view?pageIndex=1">배출권거래제 시험 자료</a></td>
      <td>산업환경과</td><td>2025-01-02</td><td>10</td>
    </tr></tbody></table>
    """
    assert parse_total_count(sample) == 1
    assert echoed_keyword(sample) == "배출권"
    rows = parse_rows(sample, "motie_press")
    assert len(rows) == 1
    assert rows[0]["sourceId"] == "170001"
    assert rows[0]["publishedAt"] == "2025-01-02"
    assert rows[0]["section"] == "motie_press"
    print("MOTIE_POLICY_SELF_TEST=PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--merge-only", action="store_true")
    parser.add_argument("--remove-from-policy", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", args.start_date):
        raise SystemExit("--start-date는 YYYY-MM-DD 형식이어야 합니다.")
    if args.lookback_days < 1:
        raise SystemExit("--lookback-days는 1 이상이어야 합니다.")
    if args.remove_from_policy:
        print(json.dumps(remove_from_policy(), ensure_ascii=False, indent=2))
        return 0
    if args.merge_only:
        history = load_history(args.start_date)
        validate_history(history, args.start_date)
        print(json.dumps(merge_into_policy(history), ensure_ascii=False, indent=2))
        return 0
    run_collection(
        start_date=args.start_date,
        full=args.full,
        lookback_days=args.lookback_days,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
