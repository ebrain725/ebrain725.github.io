#!/usr/bin/env python3
"""KRX 배출권시장 Open API의 최신 KAU 일별시세를 CSV에 누적한다."""

from __future__ import annotations

import csv
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "public" / "data" / "prices.csv"
KRX_URL = os.getenv(
    "KRX_EMISSIONS_URL",
    "https://data-dbg.krx.co.kr/svc/apis/gen/ets_bydd_trd",
)
FIELDS = [
    "date", "symbol", "close", "change", "change_rate", "open", "high",
    "low", "volume", "trade_value",
]
KST = ZoneInfo("Asia/Seoul")


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


def main() -> int:
    auth_key = os.getenv("KRX_AUTH_KEY", "").strip()
    if not auth_key:
        raise RuntimeError("GitHub Secret KRX_AUTH_KEY가 없습니다.")
    symbol = os.getenv("KAU_SYMBOL", "").strip()
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
    merged = {(row["date"], row["symbol"]): row for row in existing}
    for row in updates:
        merged[(row["date"], row["symbol"])] = row
    result = sorted(merged.values(), key=lambda row: (row["date"], row["symbol"]))

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in FIELDS} for row in result)
    labels = ", ".join(row["symbol"] for row in updates)
    print(f"KRX {selected_day.isoformat()} 시세 {len(updates)}건 반영: {labels}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
