#!/usr/bin/env python3
"""KRX KAU 시세와 유상할당 경매 이력을 대시보드 데이터에 누적한다."""

from __future__ import annotations

import csv
import http.cookiejar
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "public" / "data" / "prices.csv"
AUCTION_OUTPUT_PATH = ROOT / "public" / "data" / "auctions.json"
COLLECTOR_VERSION = "v3.0-krx-site-auction-2026-08-20"
KRX_URL = os.getenv(
    "KRX_EMISSIONS_URL",
    "https://data-dbg.krx.co.kr/svc/apis/gen/ets_bydd_trd",
)
KRX_SITE_ORIGIN = "https://ets.krx.co.kr"
KRX_SITE_PRICE_PAGE = f"{KRX_SITE_ORIGIN}/contents/ETS/03/03010000/ETS03010000.jsp"
KRX_SITE_OTP_URL = f"{KRX_SITE_ORIGIN}/contents/COM/GenerateOTP.jspx"
KRX_SITE_DATA_URL = f"{KRX_SITE_ORIGIN}/contents/ETS/99/ETS99000001.jspx"
KRX_SITE_CURRENT_BLD = "ETS/03/03010000/ets03010000_04"
KRX_SITE_AUCTION_BLD = "ETS/03/03010000/ets03010000_06"
KRX_SITE_AUCTION_GRID_NO = "45c48cce2e2d7fbdea1afc51c7c6ad26"
KRX_SITE_MARKET_DATE_BLD = "/COM/market_date_t"
FIELDS = [
    "date", "symbol", "close", "change", "change_rate", "open", "high",
    "low", "volume", "trade_value",
]
KST = ZoneInfo("Asia/Seoul")
USER_AGENT = "Mozilla/5.0 (compatible; ETS-SIGNAL/1.1; +https://ebrain725.github.io/)"


def number(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not text or text in {"-", "--"}:
        return None
    negative = any(mark in text for mark in ("하락", "▼"))
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    result = float(match.group())
    return -abs(result) if negative else result


def clean_number(value: float | None) -> int | float:
    if value is None:
        return 0
    return int(value) if float(value).is_integer() else round(float(value), 4)


def fetch_rows(day: date, auth_key: str) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"basDd": day.strftime("%Y%m%d")})
    request = urllib.request.Request(
        f"{KRX_URL}?{query}",
        headers={"AUTH_KEY": auth_key, "Accept": "application/json", "User-Agent": "ETS-SIGNAL/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in {400, 404}:
            return []
        raise RuntimeError(f"KRX API HTTP {exc.code}") from exc
    rows = payload.get("OutBlock_1", [])
    if rows is None:
        return []
    if not isinstance(rows, list):
        message = str(payload.get("RESULT_MSG") or payload.get("message") or "KRX 응답 형식 오류")
        raise RuntimeError(message)
    return [row for row in rows if isinstance(row, dict)]


def site_opener() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [
        ("User-Agent", USER_AGENT),
        ("Accept", "application/json,text/plain,*/*"),
        ("Accept-Language", "ko-KR,ko;q=0.9,en;q=0.7"),
        ("Referer", KRX_SITE_PRICE_PAGE),
    ]
    return opener


def site_open_text(
    opener: urllib.request.OpenerDirector,
    url: str,
    data: bytes | None = None,
    attempts: int = 3,
) -> str:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, data=data)
            with opener.open(request, timeout=35) as response:
                return response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"KRX 정보플랫폼 연결 실패: {last_error}")


def site_otp(opener: urllib.request.OpenerDirector, name: str, bld: str) -> str:
    query = urllib.parse.urlencode({"name": name, "bld": bld})
    code = site_open_text(opener, f"{KRX_SITE_OTP_URL}?{query}").strip()
    if len(code) < 20 or "<html" in code.lower():
        raise RuntimeError("KRX 정보플랫폼 OTP 발급에 실패했습니다.")
    return code


def site_json(
    opener: urllib.request.OpenerDirector,
    fields: dict[str, str],
) -> dict[str, Any]:
    raw = site_open_text(
        opener,
        KRX_SITE_DATA_URL,
        urllib.parse.urlencode(fields).encode("utf-8"),
    )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("KRX 정보플랫폼이 JSON이 아닌 응답을 반환했습니다.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("KRX 정보플랫폼 응답 형식이 올바르지 않습니다.")
    return payload


def site_market_day(opener: urllib.request.OpenerDirector) -> date:
    code = site_otp(opener, "calendar", KRX_SITE_MARKET_DATE_BLD)
    payload = site_json(opener, {"code": code})
    for value in payload.values():
        if not isinstance(value, list):
            continue
        for row in value:
            if isinstance(row, dict) and re.fullmatch(r"\d{8}", str(row.get("max_work_dt") or "")):
                return datetime.strptime(str(row["max_work_dt"]), "%Y%m%d").date()
    raise RuntimeError("KRX 정보플랫폼에서 최종 거래일을 확인하지 못했습니다.")


def fetch_site_rows(opener: urllib.request.OpenerDirector, market_day: date) -> list[dict[str, Any]]:
    code = site_otp(opener, "tablesubmit", KRX_SITE_CURRENT_BLD)
    day_text = market_day.strftime("%Y%m%d")
    payload = site_json(opener, {
        "code": code,
        "bldcode": KRX_SITE_CURRENT_BLD,
        "isu_cd": "",
        "fromdate": day_text,
        "todate": day_text,
        "pagePath": "/contents/ETS/03/03010000/ETS03010000.jsp",
    })
    rows = payload.get("result", [])
    if not isinstance(rows, list):
        raise RuntimeError("KRX 정보플랫폼 현재가 응답 형식이 올바르지 않습니다.")
    return [row for row in rows if isinstance(row, dict)]


def fetch_site_auction_rows(
    opener: urllib.request.OpenerDirector,
    from_day: date,
    to_day: date,
) -> list[dict[str, Any]]:
    """KRX 공개 시세조회 화면의 유상할당 경매 이력을 조회한다."""
    code = site_otp(opener, "grid", KRX_SITE_AUCTION_BLD)
    payload = site_json(opener, {
        "code": code,
        "gNo": KRX_SITE_AUCTION_GRID_NO,
        "isu_cd": "",
        "fromdate": from_day.strftime("%Y%m%d"),
        "todate": to_day.strftime("%Y%m%d"),
        "pagePath": "/contents/ETS/03/03010000/ETS03010000.jsp",
    })
    rows = payload.get("output", [])
    if not isinstance(rows, list):
        raise RuntimeError("KRX 정보플랫폼 경매 응답 형식이 올바르지 않습니다.")
    return [row for row in rows if isinstance(row, dict)]


def kau_rows(rows: list[dict[str, Any]], symbol: str = "") -> list[dict[str, Any]]:
    result = []
    for row in rows:
        name = str(row.get("ISU_NM") or "").strip()
        close = number(row.get("TDD_CLSPRC"))
        if not name.upper().startswith("KAU") or close is None or close <= 0:
            continue
        if symbol and name.upper() != symbol.upper():
            continue
        result.append(row)
    return result


def read_existing() -> list[dict[str, str]]:
    if not OUTPUT_PATH.exists():
        return []
    with OUTPUT_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle) if row.get("date") and row.get("symbol")]


def previous_close(existing: list[dict[str, str]], symbol: str, day_iso: str) -> float | None:
    prior = [row for row in existing if row.get("symbol") == symbol and row.get("date", "") < day_iso]
    if not prior:
        return None
    prior.sort(key=lambda row: row["date"])
    return number(prior[-1].get("close"))


def csv_row(day: date, row: dict[str, Any], existing: list[dict[str, str]]) -> dict[str, Any]:
    symbol = str(row.get("ISU_NM") or "").strip()
    day_iso = day.isoformat()
    close = number(row.get("TDD_CLSPRC")) or 0
    change = number(row.get("CMPPREVDD_PRC"))
    rate = number(row.get("FLUC_RT"))
    prior = previous_close(existing, symbol, day_iso)
    if change is None and prior is not None:
        change = close - prior
    if rate is None and prior:
        rate = (close / prior - 1) * 100
    return {
        "date": day_iso,
        "symbol": symbol,
        "close": clean_number(close),
        "change": clean_number(change),
        "change_rate": clean_number(rate),
        "open": clean_number(number(row.get("TDD_OPNPRC"))),
        "high": clean_number(number(row.get("TDD_HGPRC"))),
        "low": clean_number(number(row.get("TDD_LWPRC"))),
        "volume": clean_number(number(row.get("ACC_TRDVOL"))),
        "trade_value": clean_number(number(row.get("ACC_TRDVAL"))),
    }


def site_kau_rows(rows: list[dict[str, Any]], symbol: str = "") -> list[dict[str, Any]]:
    result = []
    for row in rows:
        name = str(row.get("isu_cd") or "").strip()
        close = number(row.get("tdd_clsprc"))
        if not name.upper().startswith("KAU") or close is None or close <= 0:
            continue
        if symbol and name.upper() != symbol.upper():
            continue
        result.append(row)
    return result


def site_csv_row(day: date, row: dict[str, Any], existing: list[dict[str, str]]) -> dict[str, Any]:
    symbol = str(row.get("isu_cd") or "").strip()
    day_iso = day.isoformat()
    close = number(row.get("tdd_clsprc")) or 0
    change = number(row.get("cmpprevdd_prc"))
    rate = number(row.get("fluc_rt"))
    prior = previous_close(existing, symbol, day_iso)
    if prior is not None:
        change = close - prior
        rate = (close / prior - 1) * 100 if prior else 0
    elif rate is not None and change is not None:
        change = -abs(change) if rate < 0 else abs(change)
    return {
        "date": day_iso,
        "symbol": symbol,
        "close": clean_number(close),
        "change": clean_number(change),
        "change_rate": clean_number(rate),
        "open": clean_number(number(row.get("tdd_opnprc"))),
        "high": clean_number(number(row.get("tdd_hgprc"))),
        "low": clean_number(number(row.get("tdd_lwprc"))),
        "volume": clean_number(number(row.get("acc_trdvol"))),
        # 현재가 테이블은 거래대금을 fluc_tp_cd라는 이름으로 반환합니다.
        "trade_value": clean_number(number(row.get("acc_trdval") or row.get("fluc_tp_cd"))),
    }


def write_updates(existing: list[dict[str, str]], updates: list[dict[str, Any]]) -> None:
    merged = {(row["date"], row["symbol"]): row for row in existing}
    for row in updates:
        merged[(str(row["date"]), str(row["symbol"]))] = row
    result = sorted(merged.values(), key=lambda row: (row["date"], row["symbol"]))

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in FIELDS} for row in result)


def auction_item(row: dict[str, Any]) -> dict[str, Any] | None:
    auction_date = str(row.get("trd_dd") or "").strip()
    symbol = str(row.get("isu_eng_abbrv") or "").strip().upper()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", auction_date) or not symbol.startswith("KAU"):
        return None
    return {
        "date": auction_date,
        "symbol": symbol,
        "offeredQuantity": clean_number(number(row.get("onewayauct_appl_qty"))),
        "bidQuantity": clean_number(number(row.get("auct_trd_qty"))),
        "bidRatio": clean_number(number(row.get("auct_trd_rto"))),
        "bidderCount": clean_number(number(row.get("auct_trd_partc_cnt"))),
        "winnerCount": clean_number(number(row.get("acc_trdcnt"))),
        "highestBid": clean_number(number(row.get("hgst_ord_prc"))),
        "lowestBid": clean_number(number(row.get("lwst_ord_prc"))),
        "awardedQuantity": clean_number(number(row.get("acc_trdvol"))),
        "clearingPrice": clean_number(number(row.get("clsprc"))),
    }


def read_existing_auctions() -> list[dict[str, Any]]:
    if not AUCTION_OUTPUT_PATH.exists():
        return []
    try:
        payload = json.loads(AUCTION_OUTPUT_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    rows = payload.get("items", []) if isinstance(payload, dict) else []
    return [row for row in rows if isinstance(row, dict) and row.get("date") and row.get("symbol")]


def write_auction_updates(existing: list[dict[str, Any]], updates: list[dict[str, Any]]) -> None:
    merged = {(str(row["date"]), str(row["symbol"])): row for row in existing}
    for row in updates:
        merged[(str(row["date"]), str(row["symbol"]))] = row
    items = sorted(merged.values(), key=lambda row: (str(row["date"]), str(row["symbol"])), reverse=True)
    payload = {
        "lastSync": datetime.now(KST).isoformat(timespec="seconds"),
        "schedule": {
            "label": "매월 둘째 주 수요일 17시 이후",
            "runAt": "17:17",
            "timezone": "Asia/Seoul",
        },
        "items": items,
    }
    AUCTION_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUCTION_OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def sync_site_today(symbol: str) -> int:
    today = datetime.now(KST).date()
    opener = site_opener()
    site_open_text(opener, KRX_SITE_PRICE_PAGE)
    market_day = site_market_day(opener)
    if market_day != today:
        print(f"KRX 휴장 또는 미갱신: 최종 거래일 {market_day.isoformat()}, 오늘 {today.isoformat()}")
        return 0

    rows = site_kau_rows(fetch_site_rows(opener, market_day), symbol)
    if not rows:
        raise RuntimeError("KRX 정보플랫폼에서 오늘 KAU 시세를 찾지 못했습니다.")
    existing = read_existing()
    updates = [site_csv_row(market_day, row, existing) for row in rows]
    write_updates(existing, updates)
    labels = ", ".join(f"{row['symbol']} {row['close']:,}원" for row in updates)
    print(f"KRX 정보플랫폼 {market_day.isoformat()} 당일 시세 {len(updates)}건 반영: {labels}")
    return 0


def sync_site_auctions(symbol: str) -> int:
    """최초 실행부터 전기간 경매 이력을 조회하고 날짜·종목 기준으로 갱신한다."""
    today = datetime.now(KST).date()
    start_text = os.getenv("KRX_AUCTION_START_DATE", "2015-01-01").strip()
    try:
        start_day = datetime.strptime(start_text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise RuntimeError("KRX_AUCTION_START_DATE는 YYYY-MM-DD 형식이어야 합니다.") from exc
    if start_day > today:
        raise RuntimeError("경매 조회 시작일이 오늘보다 늦습니다.")

    opener = site_opener()
    site_open_text(opener, KRX_SITE_PRICE_PAGE)
    rows = fetch_site_auction_rows(opener, start_day, today)
    updates = []
    for row in rows:
        item = auction_item(row)
        if item and (not symbol or item["symbol"] == symbol.upper()):
            updates.append(item)
    if not updates:
        raise RuntimeError("KRX 정보플랫폼에서 KAU 경매 이력을 찾지 못했습니다.")

    existing = read_existing_auctions()
    write_auction_updates(existing, updates)
    latest = max(updates, key=lambda item: str(item["date"]))
    print(
        f"KRX 경매 전기간 {len(updates)}건 반영: "
        f"최근 {latest['date']} {latest['symbol']} 낙찰가 {latest['clearingPrice']:,}원, "
        f"응찰률 {latest['bidRatio']}%"
    )
    return 0


def sync_open_api(symbol: str) -> int:
    auth_key = os.getenv("KRX_AUTH_KEY", "").strip()
    if not auth_key:
        raise RuntimeError("GitHub Secret KRX_AUTH_KEY가 없습니다.")
    target_text = os.getenv("KRX_TARGET_DATE", "").strip()
    target = datetime.strptime(target_text, "%Y-%m-%d").date() if target_text else datetime.now(KST).date() - timedelta(days=1)

    selected_day: date | None = None
    selected_rows: list[dict[str, Any]] = []
    for offset in range(15):
        candidate = target - timedelta(days=offset)
        found = kau_rows(fetch_rows(candidate, auth_key), symbol)
        if found:
            selected_day, selected_rows = candidate, found
            break
    if selected_day is None:
        raise RuntimeError("최근 15일 내 KAU 거래 데이터를 찾지 못했습니다.")

    existing = read_existing()
    updates = [csv_row(selected_day, row, existing) for row in selected_rows]
    write_updates(existing, updates)
    labels = ", ".join(row["symbol"] for row in updates)
    print(f"KRX Open API {selected_day.isoformat()} 시세 {len(updates)}건 반영: {labels}")
    return 0


def main() -> int:
    print(f"KRX_COLLECTOR_VERSION={COLLECTOR_VERSION}")
    symbol = os.getenv("KAU_SYMBOL", "").strip()
    mode = os.getenv("KRX_SYNC_MODE", "openapi").strip().lower()
    if mode in {"auction", "site-auction", "auctions"}:
        return sync_site_auctions(symbol)
    if mode in {"site-today", "site", "today"}:
        return sync_site_today(symbol)
    if mode != "openapi":
        raise RuntimeError(f"지원하지 않는 KRX_SYNC_MODE입니다: {mode}")
    return sync_open_api(symbol)


if __name__ == "__main__":
    raise SystemExit(main())
