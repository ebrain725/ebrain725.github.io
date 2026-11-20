#!/usr/bin/env python3
"""Publish and verify the provenance for KRX quotes and the representative PDF close."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

import sync_krx as krx
import sync_krx_daily_trends as daily

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
PRICES = "data/prices.csv"
RECEIPT = "data/krx-market-refresh.json"
DAILY_LATEST = "data/krx-daily/latest.json"
DAILY_INDEX = "data/krx-daily/index.json"
VERSION = "krx-market-refresh-2"
QUOTE_SOURCES = {
    "official_site": krx.KRX_SITE_PRICE_PAGE,
    "authorized_open_api": krx.KRX_URL,
}


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object in {path.name}")
    return value


def write_json(path: Path, value: dict) -> None:
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def write_quote_attempt(status: str, requested: date, *, source: str | None = None,
                        observed: date | None = None, detail: str | None = None) -> None:
    destination = os.getenv("KRX_QUOTE_ATTEMPT_PATH")
    if destination:
        write_json(Path(destination), {
            "status": status,
            "requested_trade_date": requested.isoformat(),
            "observed_trade_date": observed.isoformat() if observed else None,
            "source_type": source,
            "detail": detail,
            "attempted_at": datetime.now(krx.KST).isoformat(timespec="seconds"),
            "workflow_run_id": os.getenv("GITHUB_RUN_ID"),
        })


def read_full_quotes(previous: dict, attempt: dict, public: Path) -> tuple[dict, dict]:
    path = public / PRICES
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    quotes = {(row["date"], row["symbol"]): row for row in rows if row["symbol"].startswith("KAU")}
    if not quotes:
        raise ValueError("KRX quote history is empty")
    latest_day = max(day for day, _ in quotes)
    date.fromisoformat(latest_day)
    digest = daily.file_sha256(path)
    former = previous.get("full_quotes") or {}
    source_type, source_run = "stored_quotes", None
    if attempt.get("status") == "collected" and attempt.get("observed_trade_date") == latest_day:
        source_type = attempt["source_type"]
        source_run = attempt.get("workflow_run_id")
        if source_type not in QUOTE_SOURCES:
            raise ValueError("Unexpected KRX quote source")
    elif former.get("sha256") == digest:
        source_type = former.get("source_type", source_type)
        source_run = former.get("workflow_run_id")
    return {
        "trade_date": latest_day,
        "source_type": source_type,
        "source_url": QUOTE_SOURCES.get(source_type),
        "workflow_run_id": source_run,
        "sha256": digest,
        "symbol_count": sum(day == latest_day for day, _ in quotes),
    }, quotes


def read_representative(public: Path, today: date) -> dict | None:
    latest = read_json(public / DAILY_LATEST)
    report = latest.get("item")
    if not report or not report.get("pdfUrl"):
        return None
    day = report["tradeDate"]
    if date.fromisoformat(day) > today:
        raise ValueError("KRX report has a future trade date")
    if date.fromisoformat(report["publishedDate"]) < date.fromisoformat(day):
        raise ValueError("KRX report was published before its trade date")
    daily.validate_report(report)
    pdf_url = daily.pdf_url_for_trade_date(day)
    pdf_path = public / pdf_url
    with pdf_path.open("rb") as stream:
        pdf_header = stream.read(5)
    if (pdf_header != b"%PDF-" or pdf_path.stat().st_size != report["fileSize"]
            or daily.file_sha256(pdf_path) != report["sha256"]):
        raise ValueError("KRX report file does not match its verified archive metadata")
    source = urllib.parse.urlsplit(report["sourcePageUrl"])
    queries = urllib.parse.parse_qs(source.query)
    if (source.scheme != "https" or source.netloc != "ets.krx.co.kr"
            or source.path != "/contents/ETS/97/97010000/ETS97010000S2.jsp"
            or queries.get("bbs_seq") != [str(report["bbsSeq"])]):
        raise ValueError("KRX report is missing its official board provenance")
    market = report["market"]
    row = market["representativeInstrument"]
    if not re.fullmatch(r"KAU\d{2}", row["symbol"]):
        return None
    if type(row["close"]) is not int or type(row["change"]) is not int or row["close"] <= max(0, row["change"]):
        raise ValueError("Invalid amount in KRX representative close")
    return {
        "trade_date": day,
        "published_date": report["publishedDate"],
        "source_type": "official_daily_pdf",
        "source_url": report["sourcePageUrl"],
        "revision_id": report["sourceRevisionId"],
        "pdf_url": pdf_url,
        "pdf_sha256": report["sha256"],
        "index_sha256": daily.file_sha256(public / DAILY_LATEST),
        "symbol": row["symbol"],
        "close": row["close"],
        "change": row["change"],
        "volume": row["volume"],
        "market_volume": market["totalVolume"],
    }


def receipt_payload(public: Path, today: date, previous: dict, attempt: dict) -> dict:
    full, quotes = read_full_quotes(previous, attempt, public)
    report = read_representative(public, today)
    use_pdf = False
    if report:
        key = (report["trade_date"], report["symbol"])
        quote = quotes.get(key)
        if quote:
            matches = all(krx.number(quote[field]) == report[field] for field in ("close", "change", "volume"))
            report["full_quote_comparison"] = "match" if matches else "mismatch"
        else:
            report["full_quote_comparison"] = "unavailable"
            use_pdf = not any(symbol == key[1] and day >= key[0] for day, symbol in quotes)
    selected = report if use_pdf and report["trade_date"] >= full["trade_date"] else full
    trade_date = selected["trade_date"]
    return {
        "version": VERSION,
        "trade_date": trade_date,
        "status": "same_day_collected" if trade_date == today.isoformat() else "source_not_current",
        "coverage": "representative_close_volume" if selected is not full else "full_quotes",
        "source_type": selected["source_type"],
        "full_quotes": full,
        "representative_report": report,
        "last_quote_attempt": attempt or previous.get("last_quote_attempt"),
    }


def build(public: Path, today: date, attempt: dict) -> dict:
    path = public / RECEIPT
    previous = read_json(path)
    payload = receipt_payload(public, today, previous, attempt)
    known = {key: previous.get(key) for key in payload}
    if payload != known:
        payload["collected_at"] = datetime.now(krx.KST).isoformat(timespec="seconds")
        write_json(path, payload)
    print(f"KRX market receipt: {payload['trade_date']} / {payload['coverage']}")
    report = payload["representative_report"] or {}
    if report.get("full_quote_comparison") == "mismatch":
        print("::warning::동일 거래일 KRX 전체 시세와 일일동향 PDF의 대표종목 수치에 차이가 있습니다.")
    return payload


def verify_public(url: str, *, include_daily_index: bool) -> None:
    base = urllib.parse.urlsplit(url)
    if base.scheme != "https" or not base.hostname or base.username or base.password or base.query or base.fragment:
        raise ValueError("Invalid public Pages URL")
    saved = read_json(PUBLIC / RECEIPT)
    if saved.get("version") != VERSION:
        raise ValueError("KRX market receipt has not been built")
    expected = {RECEIPT: daily.file_sha256(PUBLIC / RECEIPT), PRICES: saved["full_quotes"]["sha256"]}
    report = saved.get("representative_report")
    if report:
        pdf_path = daily.pdf_url_for_trade_date(report["trade_date"])
        if report["pdf_url"] != pdf_path:
            raise ValueError("Unexpected PDF path in KRX market receipt")
        expected[DAILY_LATEST] = report["index_sha256"]
        expected[pdf_path] = report["pdf_sha256"]
    if include_daily_index:
        expected[DAILY_INDEX] = daily.file_sha256(PUBLIC / DAILY_INDEX)
    for name, digest in expected.items():
        if daily.file_sha256(PUBLIC / name) != digest:
            raise ValueError(f"Local file disagrees with its market receipt: {name}")
    pending = expected.copy()
    for attempt in range(8):
        for name, digest in list(pending.items()):
            query = urllib.parse.urlencode({"verify": f"{os.getenv('GITHUB_RUN_ID', '')}-{attempt}"})
            request = urllib.request.Request(f"{url.rstrip('/')}/{name}?{query}", headers={"Cache-Control": "no-cache"})
            try:
                with urllib.request.urlopen(request, timeout=12) as response:
                    actual = hashlib.sha256(response.read()).hexdigest()
            except OSError:
                continue
            if actual == digest:
                del pending[name]
                print(f"KRX Pages verified: {name} sha256={digest}")
        if not pending:
            break
        if attempt < 7:
            time.sleep(8)
    if pending:
        raise RuntimeError(f"KRX Pages digest verification failed: {', '.join(pending)}")
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write(f"\nKRX 공개본 SHA-256 검증 완료 · {saved['trade_date']} · {saved['coverage']}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_subparsers(dest="mode", required=True)
    builder = mode.add_parser("build")
    builder.add_argument("--attempt", type=Path)
    verifier = mode.add_parser("verify")
    verifier.add_argument("url")
    verifier.add_argument("--include-daily-index", action="store_true")
    args = parser.parse_args()
    if args.mode == "build":
        build(PUBLIC, datetime.now(krx.KST).date(), read_json(args.attempt) if args.attempt else {})
    else:
        verify_public(args.url, include_daily_index=args.include_daily_index)


if __name__ == "__main__":
    main()
