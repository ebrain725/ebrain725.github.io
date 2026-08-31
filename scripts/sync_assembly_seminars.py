#!/usr/bin/env python3
"""국회도서관 국회의원 정책자료에서 K-ETS 관련 세미나 일정을 수집한다.

국회도서관 AMPOS의 공개 세미나 일정 목록만 사용하며 별도 API 키는
필요하지 않다. 오늘(KST) 기준 30일 전부터 365일 뒤까지를 검색한다.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "public" / "data" / "assembly_seminars.json"
LIST_ENDPOINT = "https://ampos.nanet.go.kr/seminarListInner.do"
OFFICIAL_PAGE = "https://ampos.nanet.go.kr/seminarList.do"
SOURCE_NAME = "국회도서관 국회의원 정책자료 세미나일정"
KST = timezone(timedelta(hours=9))

LOOKBACK_DAYS = 30
LOOKAHEAD_DAYS = 365
ARCHIVE_START = date(2015, 1, 1)
PAGE_SIZE = 10
MAX_PAGES_PER_QUERY = 50
REQUEST_TIMEOUT_SECONDS = 30
MAX_WORKERS = 4
MAX_STORED_ITEMS = 240

# AMPOS는 제목 검색을 제공한다. 서로 겹치는 검색어는 sourceId와 행사정보로
# 다시 합치므로 결과가 중복 저장되지 않는다.
SEARCH_QUERIES = (
    "배출권",
    "K-ETS",
    "탄소시장",
    "탄소가격",
    "KAU",
    "KCU",
    "KOC",
    "유상할당",
    "할당계획",
    "국제감축",
    "탄소국경",
    "CBAM",
    "상쇄",
    "온실가스",
    "탄소중립",
    "기후위기",
)

# 최초 실행 때만 과거 일정을 쌓는다. 이후에는 JSON의 완료 표식을 보고
# 건너뛰고, 이미 저장한 과거 기록은 계속 보존한다.
ARCHIVE_QUERIES = (
    "배출권",
    "K-ETS",
    "탄소시장",
    "탄소가격",
    "KAU",
    "KCU",
    "KOC",
    "유상할당",
    "무상할당",
    "할당계획",
    "배출허용총량",
)

STRONG_ETS = re.compile(
    r"배출권(?:거래제|시장|거래|경매|할당|가격)?|"
    r"탄소\s*(?:배출권|시장|가격|거래)|"
    r"온실가스\s*배출권|"
    r"(?<![A-Za-z0-9])K\s*[-_]?\s*ETS(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])K(?:AU|CU|OC)\s*\d{0,2}(?![A-Za-z0-9])|"
    r"유상\s*할당|무상\s*할당|배출허용총량",
    re.IGNORECASE,
)
RELATED_CLIMATE = re.compile(
    r"탄소중립|기후위기|기후대응|온실가스|국제감축|"
    r"탄소국경(?:조정)?|(?<![A-Za-z0-9])CBAM(?![A-Za-z0-9])|NDC",
    re.IGNORECASE,
)
MARKET_BRIDGE = re.compile(
    r"배출권|할당|경매|상쇄|외부사업|감축실적|크레딧|"
    r"탄소\s*(?:시장|가격|거래)|시장\s*안정|"
    r"(?<![A-Za-z0-9])K\s*[-_]?\s*ETS(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])K(?:AU|CU|OC)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
FALSE_POSITIVE = re.compile(
    r"자동차.{0,12}배출가스|대기오염물질|대기환경|미세먼지|"
    r"탄소섬유|탄소나노튜브|활성탄|일산화탄소",
    re.IGNORECASE,
)

KEYWORD_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("배출권거래제", re.compile(r"배출권\s*거래제", re.IGNORECASE)),
    ("탄소배출권", re.compile(r"탄소\s*배출권", re.IGNORECASE)),
    ("배출권시장", re.compile(r"배출권\s*시장", re.IGNORECASE)),
    ("K-ETS", re.compile(r"(?<![A-Za-z0-9])K\s*[-_]?\s*ETS(?![A-Za-z0-9])", re.IGNORECASE)),
    ("유상할당", re.compile(r"유상\s*할당", re.IGNORECASE)),
    ("무상할당", re.compile(r"무상\s*할당", re.IGNORECASE)),
    ("배출권경매", re.compile(r"배출권.{0,8}경매|경매.{0,8}배출권", re.IGNORECASE)),
    ("할당계획", re.compile(r"할당\s*계획", re.IGNORECASE)),
    ("배출허용총량", re.compile(r"배출허용총량", re.IGNORECASE)),
    ("상쇄", re.compile(r"상쇄|외부사업", re.IGNORECASE)),
    ("국제감축", re.compile(r"국제\s*감축", re.IGNORECASE)),
    ("탄소시장", re.compile(r"탄소\s*시장", re.IGNORECASE)),
    ("탄소가격", re.compile(r"탄소\s*가격", re.IGNORECASE)),
    ("탄소국경조정", re.compile(r"탄소국경(?:조정)?|CBAM", re.IGNORECASE)),
    ("KAU", re.compile(r"(?<![A-Za-z0-9])KAU\s*\d{0,2}(?![A-Za-z0-9])", re.IGNORECASE)),
    ("KCU", re.compile(r"(?<![A-Za-z0-9])KCU\s*\d{0,2}(?![A-Za-z0-9])", re.IGNORECASE)),
    ("KOC", re.compile(r"(?<![A-Za-z0-9])KOC\s*\d{0,2}(?![A-Za-z0-9])", re.IGNORECASE)),
)


def now_kst() -> datetime:
    return datetime.now(KST)


def now_iso() -> str:
    return now_kst().isoformat(timespec="seconds")


def clean_text(value: object, limit: int = 3000) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(str(value or "")))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def normalized_key(value: object) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", clean_text(value).lower())


class SeminarListParser(HTMLParser):
    """AMPOS 목록 테이블의 각 행을 외부 패키지 없이 읽는다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, str]] = []
        self._row: dict[str, Any] | None = None
        self._cell_index = -1
        self._cell_depth = 0
        self._cell_buffers: list[list[str]] = []
        self._title_depth = 0
        self._title_buffer: list[str] = []
        self._li_depth = 0
        self._li_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {name.lower(): (value or "") for name, value in attrs}
        if tag == "tr":
            self._row = {
                "title": "",
                "venue": "",
                "host": "",
                "coverPath": "",
                "detailPath": "",
            }
            self._cell_index = -1
            self._cell_depth = 0
            self._cell_buffers = []
            self._title_depth = 0
            self._li_depth = 0
            return
        if self._row is None:
            return
        if tag == "td":
            self._cell_index += 1
            self._cell_depth += 1
            self._cell_buffers.append([])
        elif self._cell_depth and tag == "p" and self._cell_index == 1 and not self._row["title"]:
            self._title_depth = 1
            self._title_buffer = []
        elif self._title_depth:
            self._title_depth += 1
        if self._cell_depth and tag == "li" and self._cell_index == 1:
            self._li_depth = 1
            self._li_buffer = []
        elif self._li_depth:
            self._li_depth += 1
        if tag == "img" and self._cell_index == 1:
            cover = attributes.get("data-cover-src") or attributes.get("src") or ""
            if "/repo-link/apos/COVER/" in cover:
                self._row["coverPath"] = cover
        if tag == "a":
            href = attributes.get("href", "")
            if "materialSeminarDetail.do" in href and "control_no=" in href:
                self._row["detailPath"] = href

    def handle_data(self, data: str) -> None:
        if self._row is None:
            return
        if self._cell_depth and 0 <= self._cell_index < len(self._cell_buffers):
            self._cell_buffers[self._cell_index].append(data)
        if self._title_depth:
            self._title_buffer.append(data)
        if self._li_depth:
            self._li_buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._row is None:
            return
        if self._title_depth:
            if tag == "p" and self._title_depth == 1:
                self._row["title"] = clean_text(" ".join(self._title_buffer))
                self._title_depth = 0
            elif self._title_depth > 1:
                self._title_depth -= 1
        if self._li_depth:
            if tag == "li" and self._li_depth == 1:
                text = clean_text(" ".join(self._li_buffer))
                if text.startswith("장소"):
                    self._row["venue"] = clean_text(text[2:])
                elif text.startswith("주최"):
                    self._row["host"] = clean_text(text[2:])
                self._li_depth = 0
            elif self._li_depth > 1:
                self._li_depth -= 1
        if tag == "td" and self._cell_depth:
            self._cell_depth -= 1
        elif tag == "tr":
            cells = [clean_text(" ".join(parts)) for parts in self._cell_buffers]
            if cells and self._row.get("title"):
                self._row["dateText"] = cells[0]
                self.rows.append({key: clean_text(value) for key, value in self._row.items()})
            self._row = None


def parse_list_html(document: str) -> tuple[list[dict[str, str]], int]:
    parser = SeminarListParser()
    parser.feed(document)
    total_match = re.search(
        r'id=["\']seminar_result["\'][^>]*>.*?class=["\']greenLine["\'][^>]*>\s*([\d,]+)',
        document,
        re.IGNORECASE | re.DOTALL,
    )
    total = int(total_match.group(1).replace(",", "")) if total_match else len(parser.rows)
    return parser.rows, total


def parse_event_datetime(value: object) -> tuple[str, str] | None:
    match = re.search(
        r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"
        r"(?:\s*\([^)]*\))?(?:\s*(\d{1,2})\s*:\s*(\d{2}))?",
        clean_text(value, 100),
    )
    if not match:
        return None
    try:
        day = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
    clock = f"{int(match.group(4)):02d}:{int(match.group(5)):02d}" if match.group(4) else ""
    return day.isoformat(), clock


def request_html(params: dict[str, object]) -> str:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{LIST_ENDPOINT}?{query}",
        headers={
            "User-Agent": "ETS-LIVE-DASHBOARD/1.0 (+https://ebrain725.github.io/)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
        },
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")
        except Exception as exc:  # 네트워크 일시 오류는 짧게 재시도한다.
            last_error = exc
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(str(last_error or "AMPOS 응답을 받지 못했습니다."))


def fetch_query(keyword: str, start_date: date, end_date: date) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for page in range(1, MAX_PAGES_PER_QUERY + 1):
        params = {
            "searchGubun": "search",
            "curPage": page,
            "curMonth": start_date.strftime("%Y%m"),
            "fileNo": "",
            "searchType": "title",
            "queryText": keyword,
            "fromDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "sort": "asc",
        }
        document = request_html(params)
        rows, total = parse_list_html(document)
        output.extend(rows)
        if not rows or len(output) >= total or len(rows) < PAGE_SIZE:
            return output
    raise RuntimeError(f"{keyword}: 최대 {MAX_PAGES_PER_QUERY}페이지를 초과했습니다.")


def relevance_for(title: str) -> tuple[str, str] | None:
    text = clean_text(title)
    strong = bool(STRONG_ETS.search(text))
    if FALSE_POSITIVE.search(text) and not strong:
        return None
    if strong:
        return "직접", "제목에 국내 배출권거래제 핵심어가 포함됨"
    if RELATED_CLIMATE.search(text) and MARKET_BRIDGE.search(text):
        return "연관", "기후정책 용어와 배출권 시장 연결어가 함께 포함됨"
    return None


def event_type_for(title: str) -> str:
    if re.search(r"공청회", title):
        return "공청회"
    if re.search(r"토론회|토론\s*회", title):
        return "토론회"
    if re.search(r"간담회", title):
        return "간담회"
    return "세미나"


def matched_keywords(title: str) -> list[str]:
    return [name for name, pattern in KEYWORD_PATTERNS if pattern.search(title)]


def source_id_for(row: dict[str, str], event_date: str) -> str:
    detail = clean_text(row.get("detailPath", ""), 500)
    if detail:
        control_no = urllib.parse.parse_qs(urllib.parse.urlsplit(detail).query).get("control_no", [""])[0]
        if control_no:
            return clean_text(control_no, 100)
    cover = clean_text(row.get("coverPath", ""), 500)
    cover_name = Path(urllib.parse.urlsplit(cover).path).stem if cover else ""
    if cover_name:
        return cover_name
    material = "|".join(
        (
            normalized_key(row.get("title", "")),
            event_date,
            normalized_key(row.get("host", "")),
            normalized_key(row.get("venue", "")),
        )
    )
    return "AMPOS-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def dedupe_key(row: dict[str, str], event_date: str) -> str:
    return "|".join(
        (
            normalized_key(row.get("title", "")),
            event_date,
            normalized_key(row.get("host", "")),
        )
    )


def load_existing() -> dict[str, Any]:
    try:
        payload = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def build_item(row: dict[str, str], previous: dict[str, Any] | None, timestamp: str) -> dict[str, Any] | None:
    title = clean_text(row.get("title", ""))
    parsed = parse_event_datetime(row.get("dateText", ""))
    relevance = relevance_for(title)
    if not title or not parsed or not relevance:
        return None
    event_date, start_time = parsed
    today = now_kst().date().isoformat()
    if re.search(r"취소", title):
        status = "취소"
    elif re.search(r"연기", title):
        status = "연기"
    elif event_date > today:
        status = "예정"
    elif event_date == today:
        status = "오늘"
    else:
        status = "종료"

    source_id = source_id_for(row, event_date)
    previous = previous or {}
    first_seen_at = clean_text(previous.get("firstSeenAt", ""), 80) or timestamp
    first_seen_date = first_seen_at[:10] if re.match(r"^20\d{2}-\d{2}-\d{2}", first_seen_at) else timestamp[:10]
    published_at = clean_text(previous.get("publishedAt", ""), 20) or first_seen_date
    relevance_level, relevance_reason = relevance
    venue = clean_text(row.get("venue", ""), 500)
    host = clean_text(row.get("host", ""), 800)
    summary_parts = [f"주최 {host}" if host else "", f"장소 {venue}" if venue else ""]
    summary = " · ".join(part for part in summary_parts if part) or "국회도서관에 등록된 국회의원 정책세미나 일정입니다."
    detail_path = clean_text(row.get("detailPath", ""), 500)
    official_url = urllib.parse.urljoin(OFFICIAL_PAGE, detail_path) if detail_path else OFFICIAL_PAGE
    content_material = json.dumps(
        {
            "title": title,
            "startDate": event_date,
            "startTime": start_time,
            "venue": venue,
            "host": host,
            "status": status,
            "detailPath": detail_path,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    content_hash = hashlib.sha256(content_material.encode("utf-8")).hexdigest()
    previous_hash = clean_text(previous.get("contentHash", ""), 100)
    return {
        "id": f"assembly-seminar-{hashlib.sha256(source_id.encode('utf-8')).hexdigest()[:20]}",
        "category": "세미나일정",
        "title": title,
        "publishedAt": published_at,
        "publishedAtSource": clean_text(previous.get("publishedAtSource", ""), 40) or "최초수집일",
        "startDate": event_date,
        "endDate": event_date,
        "startTime": start_time,
        "eventType": event_type_for(title),
        "venue": venue,
        "host": host,
        "status": status,
        "summary": summary,
        "url": official_url,
        "source": SOURCE_NAME,
        "sourceId": source_id,
        "coverPath": clean_text(row.get("coverPath", ""), 500),
        "detailPath": detail_path,
        "keywords": matched_keywords(title),
        "relevance": relevance_level,
        "relevanceReason": relevance_reason,
        "firstSeenAt": first_seen_at,
        "lastSeenAt": timestamp,
        "updatedAt": timestamp if not previous or previous_hash != content_hash else clean_text(previous.get("updatedAt", ""), 80) or timestamp,
        "contentHash": content_hash,
    }


def save_document(
    items: list[dict[str, Any]],
    warnings: list[str],
    start_date: date,
    end_date: date,
    archive_backfill_completed: bool,
) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "lastSync": now_iso(),
        "source": SOURCE_NAME,
        "sourceUrl": OFFICIAL_PAGE,
        "range": {"from": start_date.isoformat(), "to": end_date.isoformat()},
        "archiveFrom": ARCHIVE_START.isoformat(),
        "archiveBackfillCompleted": archive_backfill_completed,
        "warning": " | ".join(warnings) if warnings else None,
        "items": items,
    }
    temporary = OUTPUT_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT_PATH)


def main() -> int:
    today = now_kst().date()
    start_date = today - timedelta(days=LOOKBACK_DAYS)
    end_date = today + timedelta(days=LOOKAHEAD_DAYS)
    existing_document = load_existing()
    existing_items = [item for item in existing_document.get("items", []) if isinstance(item, dict)]
    existing_by_source = {
        clean_text(item.get("sourceId", ""), 200): item
        for item in existing_items
        if clean_text(item.get("sourceId", ""), 200)
    }
    existing_by_dedupe = {
        "|".join(
            (
                normalized_key(item.get("title", "")),
                clean_text(item.get("startDate", ""), 20),
                normalized_key(item.get("host", "")),
            )
        ): item
        for item in existing_items
        if clean_text(item.get("title", "")) and clean_text(item.get("startDate", ""), 20)
    }

    fetched_rows: list[dict[str, str]] = []
    warnings: list[str] = []
    successful_queries = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        pending = {
            executor.submit(fetch_query, keyword, start_date, end_date): keyword
            for keyword in SEARCH_QUERIES
        }
        for future in as_completed(pending):
            keyword = pending[future]
            try:
                fetched_rows.extend(future.result())
                successful_queries += 1
            except Exception as exc:
                warnings.append(f"{keyword} 검색 실패: {clean_text(exc, 300)}")

    archive_was_complete = bool(existing_document.get("archiveBackfillCompleted"))
    archive_successful_queries = 0
    archive_end = start_date - timedelta(days=1)
    if not archive_was_complete and archive_end >= ARCHIVE_START:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            pending = {
                executor.submit(fetch_query, keyword, ARCHIVE_START, archive_end): keyword
                for keyword in ARCHIVE_QUERIES
            }
            for future in as_completed(pending):
                keyword = pending[future]
                try:
                    fetched_rows.extend(future.result())
                    archive_successful_queries += 1
                except Exception as exc:
                    warnings.append(f"과거 {keyword} 검색 실패: {clean_text(exc, 300)}")
    archive_backfill_completed = archive_was_complete or archive_successful_queries == len(ARCHIVE_QUERIES)

    if successful_queries == 0 and archive_successful_queries == 0:
        print("국회도서관 세미나 검색이 모두 실패했습니다. 기존 assembly_seminars.json은 보존합니다.", file=sys.stderr)
        return 1

    # 먼저 공식 cover ID, 이어서 제목·주최·행사일 조합으로 중복을 제거한다.
    unique_rows: dict[str, dict[str, str]] = {}
    seen_dedupe: set[str] = set()
    for row in fetched_rows:
        parsed = parse_event_datetime(row.get("dateText", ""))
        if not parsed or not relevance_for(row.get("title", "")):
            continue
        event_date, _ = parsed
        source_id = source_id_for(row, event_date)
        key = source_id or dedupe_key(row, event_date)
        secondary = dedupe_key(row, event_date)
        if key in unique_rows or secondary in seen_dedupe:
            continue
        unique_rows[key] = row
        seen_dedupe.add(secondary)

    timestamp = now_iso()
    items: list[dict[str, Any]] = []
    for row in unique_rows.values():
        parsed = parse_event_datetime(row.get("dateText", ""))
        if not parsed:
            continue
        event_date, _ = parsed
        source_id = source_id_for(row, event_date)
        previous = existing_by_source.get(source_id) or existing_by_dedupe.get(dedupe_key(row, event_date))
        item = build_item(row, previous, timestamp)
        if item:
            items.append(item)

    # 일정은 누적형이다. 조회기간 밖으로 밀려난 과거 항목도 삭제하지 않는다.
    # 새로 조회된 항목을 먼저 두어 같은 행사 중복에서는 최신 정보가 선택된다.
    items.extend(existing_items)

    final_by_id: dict[str, dict[str, Any]] = {}
    final_dedupe: set[str] = set()
    for item in sorted(
        items,
        key=lambda value: (
            clean_text(value.get("startDate", ""), 20),
            clean_text(value.get("title", "")),
        ),
        reverse=True,
    ):
        item_id = clean_text(item.get("id", ""), 200)
        secondary = "|".join(
            (
                normalized_key(item.get("title", "")),
                clean_text(item.get("startDate", ""), 20),
                normalized_key(item.get("host", "")),
            )
        )
        if not item_id or item_id in final_by_id or secondary in final_dedupe:
            continue
        final_by_id[item_id] = item
        final_dedupe.add(secondary)

    final_items = list(final_by_id.values())[:MAX_STORED_ITEMS]
    save_document(final_items, warnings[:20], start_date, end_date, archive_backfill_completed)
    print(f"국회 배출권 관련 세미나 일정 {len(final_items)}건 저장")
    if warnings:
        print("일부 국회도서관 검색 경고: " + " | ".join(warnings[:5]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
