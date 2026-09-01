#!/usr/bin/env python3
"""국회도서관 국회의원 정책자료에서 K-ETS 관련 세미나 일정을 수집한다.

국회도서관 AMPOS의 공개 세미나 일정 목록만 사용하며 별도 API 키는
필요하지 않다. 2026년 1월 1일부터 오늘(KST) 기준 365일 뒤까지를 검색한다.
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

LOOKAHEAD_DAYS = 365
SEMINAR_START_DATE = date(2026, 1, 1)
PAGE_SIZE = 10
MAX_PAGES_PER_QUERY = 50
REQUEST_TIMEOUT_SECONDS = 30
MAX_WORKERS = 4
MAX_STORED_ITEMS = 240
UNKNOWN_HOST = "주최 미확인"

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


def verified_event_day(value: object, range_end: date) -> date | None:
    """공식 목록의 행사일이 저장 범위 안의 실제 달력 날짜인지 확인한다."""
    parsed = parse_event_datetime(value)
    if not parsed:
        return None
    try:
        event_day = date.fromisoformat(parsed[0])
    except ValueError:
        return None
    return event_day if SEMINAR_START_DATE <= event_day <= range_end else None


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


def event_status_for(
    title: object,
    event_date: str,
    today_iso: str,
    previous_status: object = "",
) -> str:
    """행사일과 공식 상태 표현을 기준으로 표시 상태를 일관되게 계산한다."""
    clean_title = clean_text(title)
    old_status = clean_text(previous_status, 20)
    if re.search(r"취소", clean_title) or old_status == "취소":
        return "취소"
    if re.search(r"연기", clean_title) or old_status == "연기":
        return "연기"
    if event_date > today_iso:
        return "예정"
    if event_date == today_iso:
        return "오늘"
    return "종료"


def seminar_content_hash(
    *,
    title: object,
    event_date: object,
    start_time: object,
    venue: object,
    host: object,
    status: object,
    detail_path: object,
) -> str:
    material = json.dumps(
        {
            "title": clean_text(title),
            "startDate": clean_text(event_date, 20),
            "startTime": clean_text(start_time, 20),
            "venue": clean_text(venue, 500),
            "host": clean_text(host, 800) or UNKNOWN_HOST,
            "status": clean_text(status, 20),
            "detailPath": clean_text(detail_path, 500),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def refresh_existing_item_status(
    item: dict[str, Any],
    today_iso: str,
    timestamp: str,
) -> dict[str, Any]:
    """부분 수집 때 재조회되지 않은 기존 일정의 일자 상태도 현재화한다."""
    refreshed = dict(item)
    event_date = clean_text(refreshed.get("startDate", ""), 20)[:10]
    try:
        date.fromisoformat(event_date)
    except ValueError:
        return refreshed

    old_host = clean_text(refreshed.get("host", ""), 800)
    host = old_host or UNKNOWN_HOST
    old_status = clean_text(refreshed.get("status", ""), 20)
    status = event_status_for(
        refreshed.get("title", ""),
        event_date,
        today_iso,
        previous_status=old_status,
    )
    if old_host == host and old_status == status:
        return refreshed

    refreshed["host"] = host
    refreshed["status"] = status
    refreshed["contentHash"] = seminar_content_hash(
        title=refreshed.get("title", ""),
        event_date=event_date,
        start_time=refreshed.get("startTime", ""),
        venue=refreshed.get("venue", ""),
        host=host,
        status=status,
        detail_path=refreshed.get("detailPath", ""),
    )
    refreshed["updatedAt"] = timestamp
    return refreshed


def load_existing() -> dict[str, Any]:
    try:
        payload = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def build_item(
    row: dict[str, str],
    previous: dict[str, Any] | None,
    timestamp: str,
    range_end: date,
    current_day: date | None = None,
) -> dict[str, Any] | None:
    title = clean_text(row.get("title", ""))
    parsed = parse_event_datetime(row.get("dateText", ""))
    relevance = relevance_for(title)
    verified_day = verified_event_day(row.get("dateText", ""), range_end)
    if not title or not parsed or not verified_day or not relevance:
        return None
    previous = previous if isinstance(previous, dict) else {}
    event_date, start_time = parsed
    today = (current_day or now_kst().date()).isoformat()
    status = event_status_for(
        title,
        event_date,
        today,
        previous_status=previous.get("status", ""),
    )

    source_id = source_id_for(row, event_date)
    first_seen_at = clean_text(previous.get("firstSeenAt", ""), 80) or timestamp
    first_seen_date = first_seen_at[:10] if re.match(r"^20\d{2}-\d{2}-\d{2}", first_seen_at) else timestamp[:10]
    published_at = clean_text(previous.get("publishedAt", ""), 20) or first_seen_date
    relevance_level, relevance_reason = relevance
    venue = clean_text(row.get("venue", ""), 500)
    host = (
        clean_text(row.get("host", ""), 800)
        or clean_text(previous.get("host", ""), 800)
        or UNKNOWN_HOST
    )
    summary_parts = [f"주최 {host}" if host else "", f"장소 {venue}" if venue else ""]
    summary = " · ".join(part for part in summary_parts if part) or "국회도서관에 등록된 국회의원 정책세미나 일정입니다."
    detail_path = clean_text(row.get("detailPath", ""), 500)
    official_url = urllib.parse.urljoin(OFFICIAL_PAGE, detail_path) if detail_path else OFFICIAL_PAGE
    content_hash = seminar_content_hash(
        title=title,
        event_date=event_date,
        start_time=start_time,
        venue=venue,
        host=host,
        status=status,
        detail_path=detail_path,
    )
    previous_hash = clean_text(previous.get("contentHash", ""), 100)
    return {
        "id": f"assembly-seminar-{hashlib.sha256(source_id.encode('utf-8')).hexdigest()[:20]}",
        "category": "세미나일정",
        "title": title,
        "publishedAt": published_at,
        "publishedAtSource": clean_text(previous.get("publishedAtSource", ""), 40) or "최초수집일",
        "startDate": event_date,
        "endDate": event_date,
        "sourceDateText": clean_text(row.get("dateText", ""), 100),
        "dateVerifiedBy": "AMPOS 행사일시 셀",
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
) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "lastSync": now_iso(),
        "source": SOURCE_NAME,
        "sourceUrl": OFFICIAL_PAGE,
        "range": {"from": start_date.isoformat(), "to": end_date.isoformat()},
        "archiveFrom": SEMINAR_START_DATE.isoformat(),
        "archiveBackfillCompleted": True,
        "warning": " | ".join(warnings) if warnings else None,
        "items": items,
    }
    temporary = OUTPUT_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT_PATH)


def main() -> int:
    today = now_kst().date()
    timestamp = now_iso()
    start_date = SEMINAR_START_DATE
    end_date = today + timedelta(days=LOOKAHEAD_DAYS)
    existing_document = load_existing()
    existing_items = []
    for item in existing_document.get("items", []):
        if not isinstance(item, dict):
            continue
        try:
            event_day = date.fromisoformat(clean_text(item.get("startDate", ""), 20))
        except ValueError:
            continue
        if SEMINAR_START_DATE <= event_day <= end_date:
            existing_items.append(
                refresh_existing_item_status(item, today.isoformat(), timestamp)
            )
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

    if successful_queries == 0:
        has_valid_existing = (
            OUTPUT_PATH.is_file()
            and isinstance(existing_document.get("items"), list)
            and bool(clean_text(existing_document.get("lastSync", ""), 80))
            and bool(clean_text(existing_document.get("source", ""), 300))
        )
        if not has_valid_existing:
            print(
                "::error::국회도서관 세미나 검색이 모두 실패했고 보존할 정상 "
                "assembly_seminars.json도 없습니다.",
                file=sys.stderr,
            )
            return 1
        print(
            "::warning::국회도서관 세미나 검색이 모두 실패했습니다. "
            "기존 assembly_seminars.json을 보존하고 정책·뉴스 배포를 계속합니다.",
            file=sys.stderr,
        )
        if warnings:
            print("일부 국회도서관 검색 경고: " + " | ".join(warnings[:5]), file=sys.stderr)
        return 0

    # 먼저 공식 cover ID, 이어서 제목·주최·행사일 조합으로 중복을 제거한다.
    unique_rows: dict[str, dict[str, str]] = {}
    seen_dedupe: set[str] = set()
    for row in fetched_rows:
        parsed = parse_event_datetime(row.get("dateText", ""))
        if not parsed or not verified_event_day(row.get("dateText", ""), end_date) or not relevance_for(row.get("title", "")):
            continue
        event_date, _ = parsed
        source_id = source_id_for(row, event_date)
        key = source_id or dedupe_key(row, event_date)
        secondary = dedupe_key(row, event_date)
        if key in unique_rows or secondary in seen_dedupe:
            continue
        unique_rows[key] = row
        seen_dedupe.add(secondary)

    fresh_items: list[dict[str, Any]] = []
    for row in unique_rows.values():
        parsed = parse_event_datetime(row.get("dateText", ""))
        if not parsed:
            continue
        event_date, _ = parsed
        source_id = source_id_for(row, event_date)
        previous = existing_by_source.get(source_id) or existing_by_dedupe.get(dedupe_key(row, event_date))
        item = build_item(row, previous, timestamp, end_date, today)
        if item:
            fresh_items.append(item)

    # 2026년 이후 일정은 누적 보존하되, 검증 범위를 벗어난 기존 항목은 위에서 제외한다.
    # 날짜가 앞당겨지거나 늦춰진 경우 모두 새 공식 sourceId 항목을 기존 항목보다
    # 먼저 처리해야 정정 방향과 무관하게 공식 최신값이 선택된다.
    items = [*fresh_items, *existing_items]

    final_by_id: dict[str, dict[str, Any]] = {}
    final_dedupe: set[str] = set()
    for item in items:
        try:
            event_day = date.fromisoformat(clean_text(item.get("startDate", ""), 20))
        except ValueError:
            continue
        if not (SEMINAR_START_DATE <= event_day <= end_date):
            continue
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

    final_items = sorted(
        final_by_id.values(),
        key=lambda value: (
            clean_text(value.get("startDate", ""), 20),
            clean_text(value.get("title", "")),
        ),
        reverse=True,
    )[:MAX_STORED_ITEMS]
    save_document(final_items, warnings[:20], start_date, end_date)
    print(f"국회 배출권 관련 세미나 일정 {len(final_items)}건 저장")
    if warnings:
        print("일부 국회도서관 검색 경고: " + " | ".join(warnings[:5]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
