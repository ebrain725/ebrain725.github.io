#!/usr/bin/env python3
"""KRX 배출권 거래시장 일일동향 PDF를 검증해 기간별 수급으로 누적한다.

PDF를 검증·텍스트화해 거래일별 수치와 출처 정보를 JSON에 저장하고,
활성 보고서의 검증된 원본 PDF를 거래일 기준 공개 경로에 보관한다.
"""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "public" / "data" / "krx-daily"
MONTH_ROOT = DATA_ROOT / "by-month"
SOURCE_ROOT = DATA_ROOT / "sources"
PDF_ROOT = DATA_ROOT / "pdfs"
INDEX_PATH = DATA_ROOT / "index.json"
LATEST_PATH = DATA_ROOT / "latest.json"
QUALITY_PATH = DATA_ROOT / "quality.json"
MANUAL_OVERRIDE_PATH = ROOT / "scripts" / "krx_daily_manual_overrides.json"

KST = ZoneInfo("Asia/Seoul")
COLLECTOR_VERSION = "1.3.0"
SCHEMA_VERSION = "1.0"
KRX_ORIGIN = "https://ets.krx.co.kr"
KRX_LIST_PAGE = (
    f"{KRX_ORIGIN}/contents/ETS/97/97010000/"
    "ETS97010000S1.jsp?bbstype=5"
)
KRX_LIST_DATA_URL = f"{KRX_ORIGIN}/contents/ETS/99/ETS99000001.jspx"
KRX_FILE_LIST_URL = (
    f"{KRX_ORIGIN}/contents/ETS/97/97010000/"
    "ETS97010000.jspx?cmd=fileList"
)
KRX_OTP_URL = f"{KRX_ORIGIN}/contents/COM/GenerateOTP.jspx"
KRX_FILE_DOWNLOAD_URL = "https://file.krx.co.kr/download.jspx"
KRX_LIST_BLD = "ETS/97/97010000/ets97010000s1_01"
KRX_BBS_ID = "ETS970100005"
USER_AGENT = "Mozilla/5.0 (compatible; ETS-SIGNAL/1.3; +https://ebrain725.github.io/)"

CATEGORY_KEYS = {
    "할당대상업체": "liable_entities",
    "시장조성자": "market_makers",
    "거래중개회원": "brokerage_members",
    "금융기관": "financial_institutions",
    "기타": "others",
    "KOC전문회원": "koc_specialists",
}
METHOD_KEYS = {
    "경쟁": "competitive",
    "협의": "negotiated",
    "경매": "auction",
    "합계": "total",
}
REQUIRED_STABLE_CATEGORIES = {"할당대상업체", "시장조성자", "KOC전문회원"}
MANUAL_OVERRIDE_SCHEMA_VERSION = "1.0"
MANUAL_METHODS = ("competitive", "negotiated", "auction")


def now_kst() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def read_json(path: Path, fallback: Any, *, strict: bool = False) -> Any:
    if not path.is_file():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        if strict:
            raise RuntimeError(f"기존 JSON을 읽을 수 없습니다: {path}") from exc
        return fallback


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def pdf_url_for_trade_date(trade_date: str) -> str:
    if not re.fullmatch(r"20\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])", trade_date):
        raise RuntimeError(f"PDF 거래일 형식 오류: {trade_date}")
    return f"data/krx-daily/pdfs/{trade_date[:7]}/{trade_date}.pdf"


def pdf_path_for_trade_date(trade_date: str) -> Path:
    return ROOT / "public" / pdf_url_for_trade_date(trade_date)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_archived_pdf(report: dict[str, Any]) -> None:
    pdf_url = report.get("pdfUrl")
    if pdf_url is None:
        # 기존 JSON만 있는 백필 이전 스냅샷과 호환한다.
        return
    trade_date = str(report.get("tradeDate") or "")
    expected_url = pdf_url_for_trade_date(trade_date)
    if pdf_url != expected_url:
        raise RuntimeError(f"PDF 공개 경로 오류: {trade_date}, {pdf_url}")
    path = pdf_path_for_trade_date(trade_date)
    if not path.is_file():
        raise RuntimeError(f"PDF 공개 파일 누락: {trade_date}")
    file_size = path.stat().st_size
    if file_size != report.get("fileSize"):
        raise RuntimeError(
            f"PDF 공개 파일 크기 불일치: {trade_date}, "
            f"{file_size} != {report.get('fileSize')}"
        )
    with path.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise RuntimeError(f"PDF 공개 파일 헤더 오류: {trade_date}")
    actual_sha256 = file_sha256(path)
    if actual_sha256 != report.get("sha256"):
        raise RuntimeError(
            f"PDF 공개 파일 SHA-256 불일치: {trade_date}, "
            f"{actual_sha256} != {report.get('sha256')}"
        )


def archive_pdf(report: dict[str, Any], pdf_bytes: bytes) -> bool:
    trade_date = str(report.get("tradeDate") or "")
    expected_url = pdf_url_for_trade_date(trade_date)
    if report.get("pdfUrl") != expected_url:
        raise RuntimeError(f"PDF 저장 경로 메타데이터 오류: {trade_date}")
    if not pdf_bytes.startswith(b"%PDF-"):
        raise RuntimeError(f"PDF 저장 헤더 오류: {trade_date}")
    if len(pdf_bytes) != report.get("fileSize"):
        raise RuntimeError(f"PDF 저장 크기 불일치: {trade_date}")
    if hashlib.sha256(pdf_bytes).hexdigest() != report.get("sha256"):
        raise RuntimeError(f"PDF 저장 SHA-256 불일치: {trade_date}")

    path = pdf_path_for_trade_date(trade_date)
    if path.is_file() and path.stat().st_size == len(pdf_bytes):
        if file_sha256(path) == report["sha256"]:
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_bytes(pdf_bytes)
        if temporary.stat().st_size != len(pdf_bytes):
            raise RuntimeError(f"PDF 임시파일 크기 불일치: {trade_date}")
        if file_sha256(temporary) != report["sha256"]:
            raise RuntimeError(f"PDF 임시파일 SHA-256 불일치: {trade_date}")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    validate_archived_pdf(report)
    return True


def numeric(value: str, dash_as_zero: bool = True) -> int:
    text = str(value or "").strip().replace(",", "")
    if text in {"", "-", "--"}:
        if dash_as_zero:
            return 0
        raise ValueError("비어 있는 숫자입니다.")
    match = re.fullmatch(r"([+-]?)\s*(\d+)", text)
    if not match:
        raise ValueError(f"숫자 형식이 올바르지 않습니다: {value!r}")
    return int(f"{match.group(1)}{match.group(2)}")


def clean_label(value: str) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def expected_category_keys(trade_date: str) -> tuple[str, ...]:
    if trade_date < "2026-03-17":
        return (
            "liable_entities",
            "market_makers",
            "brokerage_members",
            "koc_specialists",
        )
    return (
        "liable_entities",
        "market_makers",
        "financial_institutions",
        "others",
        "koc_specialists",
    )


def validate_manual_override(
    source_key: str,
    sha256: str,
    override: dict[str, Any],
) -> None:
    if not re.fullmatch(r"krx:\d+:\d+", source_key):
        raise RuntimeError(f"수기 전사 sourceKey 형식 오류: {source_key}")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise RuntimeError(f"수기 전사 SHA-256 형식 오류: {source_key}")
    if not isinstance(override, dict) or set(override) != {
        "source",
        "market",
        "grossByMethod",
        "audit",
    }:
        raise RuntimeError(f"수기 전사 최상위 필드 오류: {source_key}")

    source = override.get("source")
    expected_source_fields = {
        "bbsSeq",
        "attachFileSeq",
        "boardRn",
        "filename",
        "publishedDate",
        "tradeDate",
        "fileSize",
        "pages",
    }
    if not isinstance(source, dict) or set(source) != expected_source_fields:
        raise RuntimeError(f"수기 전사 출처 필드 오류: {source_key}")
    source_parts = source_key.split(":")
    if (
        source.get("bbsSeq") != source_parts[1]
        or source.get("attachFileSeq") != source_parts[2]
    ):
        raise RuntimeError(f"수기 전사 출처 식별자 불일치: {source_key}")
    if type(source.get("boardRn")) is not int or source["boardRn"] < 1:
        raise RuntimeError(f"수기 전사 boardRn 오류: {source_key}")
    if type(source.get("fileSize")) is not int or not 10_000 <= source["fileSize"] <= 10_000_000:
        raise RuntimeError(f"수기 전사 PDF 크기 오류: {source_key}")
    if type(source.get("pages")) is not int or not 1 <= source["pages"] <= 5:
        raise RuntimeError(f"수기 전사 PDF 페이지 수 오류: {source_key}")
    trade_date = str(source.get("tradeDate") or "")
    published_date = str(source.get("publishedDate") or "")
    try:
        if datetime.strptime(trade_date, "%Y-%m-%d").date().isoformat() != trade_date:
            raise ValueError
        if datetime.strptime(published_date, "%Y-%m-%d").date().isoformat() != published_date:
            raise ValueError
    except ValueError as exc:
        raise RuntimeError(f"수기 전사 날짜 오류: {source_key}") from exc
    filename = str(source.get("filename") or "")
    filename_date = re.search(r"_(\d{6})\.pdf$", filename, re.I)
    if not filename_date:
        raise RuntimeError(f"수기 전사 PDF 파일명 날짜 누락: {source_key}")
    expected_trade_date = datetime.strptime(
        filename_date.group(1), "%y%m%d"
    ).date().isoformat()
    if expected_trade_date != trade_date:
        raise RuntimeError(f"수기 전사 PDF 파일명·거래일 불일치: {source_key}")

    market = override.get("market")
    if not isinstance(market, dict) or set(market) != {
        "totalVolume",
        "representativeInstrument",
    }:
        raise RuntimeError(f"수기 전사 시장 필드 오류: {source_key}")
    total_volume = market.get("totalVolume")
    if type(total_volume) is not int or total_volume < 0:
        raise RuntimeError(f"수기 전사 전체 거래량 오류: {source_key}")
    representative = market.get("representativeInstrument")
    if not isinstance(representative, dict) or set(representative) != {
        "symbol",
        "close",
        "change",
        "volume",
    }:
        raise RuntimeError(f"수기 전사 대표 종목 필드 오류: {source_key}")
    if not re.fullmatch(r"(?:KAU|KCU)\d+", str(representative.get("symbol") or "")):
        raise RuntimeError(f"수기 전사 대표 종목 코드 오류: {source_key}")
    if representative.get("close") is not None and type(representative["close"]) is not int:
        raise RuntimeError(f"수기 전사 대표 종목 종가 오류: {source_key}")
    if representative.get("change") is not None and type(representative["change"]) is not int:
        raise RuntimeError(f"수기 전사 대표 종목 등락 오류: {source_key}")
    representative_volume = representative.get("volume")
    if type(representative_volume) is not int or not 0 <= representative_volume <= total_volume:
        raise RuntimeError(f"수기 전사 대표 종목 거래량 오류: {source_key}")

    gross = override.get("grossByMethod")
    if not isinstance(gross, dict) or set(gross) != {"buy", "sell"}:
        raise RuntimeError(f"수기 전사 매수·매도 표 오류: {source_key}")
    category_keys = expected_category_keys(trade_date)
    for side in ("buy", "sell"):
        side_rows = gross.get(side)
        if not isinstance(side_rows, dict) or set(side_rows) != set(MANUAL_METHODS):
            raise RuntimeError(f"수기 전사 {side} 거래방식 오류: {source_key}")
        for method in MANUAL_METHODS:
            values = side_rows.get(method)
            if not isinstance(values, dict) or tuple(values) != category_keys:
                raise RuntimeError(
                    f"수기 전사 {side} {method} 참가자 분류 오류: {source_key}"
                )
            if any(type(value) is not int or value < 0 for value in values.values()):
                raise RuntimeError(
                    f"수기 전사 {side} {method} 수치 오류: {source_key}"
                )
    for method in MANUAL_METHODS:
        buy_total = sum(gross["buy"][method].values())
        sell_total = sum(gross["sell"][method].values())
        if buy_total != sell_total:
            raise RuntimeError(
                f"수기 전사 {method} 매수·매도 불균형: "
                f"{source_key}, {buy_total} != {sell_total}"
            )
    for side in ("buy", "sell"):
        side_total = sum(
            sum(gross[side][method].values()) for method in MANUAL_METHODS
        )
        if side_total != total_volume:
            raise RuntimeError(
                f"수기 전사 {side} 합계·전체 거래량 불일치: "
                f"{source_key}, {side_total} != {total_volume}"
            )

    audit = override.get("audit")
    if not isinstance(audit, dict) or set(audit) != {"method", "note"}:
        raise RuntimeError(f"수기 전사 감사 필드 오류: {source_key}")
    if audit.get("method") != "manual_transcription_from_rendered_image_only_pdf":
        raise RuntimeError(f"수기 전사 감사 방식 오류: {source_key}")
    if len(str(audit.get("note") or "").strip()) < 20:
        raise RuntimeError(f"수기 전사 감사 메모 누락: {source_key}")


def load_manual_overrides() -> dict[str, dict[str, dict[str, Any]]]:
    payload = read_json(MANUAL_OVERRIDE_PATH, {}, strict=True)
    if not isinstance(payload, dict) or set(payload) != {"schemaVersion", "overrides"}:
        raise RuntimeError("KRX 수기 전사 파일 최상위 형식이 올바르지 않습니다.")
    if payload.get("schemaVersion") != MANUAL_OVERRIDE_SCHEMA_VERSION:
        raise RuntimeError("KRX 수기 전사 파일 schemaVersion이 올바르지 않습니다.")
    overrides = payload.get("overrides")
    if not isinstance(overrides, dict):
        raise RuntimeError("KRX 수기 전사 overrides 형식이 올바르지 않습니다.")
    seen_hashes: set[str] = set()
    for source_key, revisions in overrides.items():
        if not isinstance(revisions, dict) or not revisions:
            raise RuntimeError(f"수기 전사 개정본 누락: {source_key}")
        for sha256, override in revisions.items():
            if sha256 in seen_hashes:
                raise RuntimeError(f"수기 전사 SHA-256 중복: {sha256}")
            validate_manual_override(source_key, sha256, override)
            seen_hashes.add(sha256)
    return overrides


def select_manual_override(
    overrides: dict[str, dict[str, dict[str, Any]]],
    source_key: str,
    sha256: str,
) -> dict[str, Any] | None:
    revisions = overrides.get(source_key)
    if revisions is not None:
        override = revisions.get(sha256)
        if override is None:
            registered_hashes = ", ".join(sorted(revisions))
            raise RuntimeError(
                f"수기 전사 대상 PDF 해시 불일치: {source_key}, "
                f"actual={sha256}, registered={registered_hashes}"
            )
        return override
    for registered_source_key, registered_revisions in overrides.items():
        if sha256 in registered_revisions:
            raise RuntimeError(
                f"수기 전사 PDF 출처 식별자 불일치: "
                f"actual={source_key}, registered={registered_source_key}"
            )
    return None


class KrxSession:
    def __init__(self) -> None:
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar)
        )
        self.opener.addheaders = [
            ("User-Agent", USER_AGENT),
            ("Accept", "application/json,text/plain,*/*"),
            ("Accept-Language", "ko-KR,ko;q=0.9,en;q=0.7"),
            ("Referer", KRX_LIST_PAGE),
        ]
        self.open_bytes(KRX_LIST_PAGE)

    def open_bytes(
        self,
        url: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        attempts: int = 4,
    ) -> bytes:
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                request = urllib.request.Request(url, data=data)
                for name, value in (headers or {}).items():
                    request.add_header(name, value)
                with self.opener.open(request, timeout=40) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in {408, 425, 429, 500, 502, 503, 504}:
                    raise RuntimeError(f"KRX HTTP {exc.code}: {url}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
            if attempt + 1 < attempts:
                time.sleep(min(8, 2**attempt))
        raise RuntimeError(f"KRX 연결 실패: {last_error}")

    def open_text(
        self,
        url: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        return self.open_bytes(url, data=data, headers=headers).decode("utf-8")

    def otp(self, fields: dict[str, str]) -> str:
        query = urllib.parse.urlencode(fields)
        code = self.open_text(f"{KRX_OTP_URL}?{query}").strip()
        if len(code) < 20 or "<html" in code.lower():
            raise RuntimeError("KRX OTP 발급에 실패했습니다.")
        return code

    def list_posts(self, page: int) -> tuple[list[dict[str, str]], int]:
        code = self.otp({"name": "form", "bld": KRX_LIST_BLD})
        fields = {
            "bbstype": "5",
            "bbs_id": KRX_BBS_ID,
            "sch_mbr_nm": "한국거래소",
            "sch_tp": "title",
            "sch_word": "일일동향",
            "pagePath": "/contents/ETS/97/97010000/ETS97010000S1.jsp",
            "curPage": str(max(1, page)),
            "code": code,
        }
        raw = self.open_text(
            KRX_LIST_DATA_URL,
            urllib.parse.urlencode(fields).encode("utf-8"),
            headers={"Origin": KRX_ORIGIN},
        )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("KRX 시장동향 목록이 JSON이 아닙니다.") from exc
        rows = payload.get("block1", []) if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            raise RuntimeError("KRX 시장동향 목록 형식이 올바르지 않습니다.")
        result = []
        total = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            if "일일동향" not in title:
                continue
            normalized = {
                "rn": str(row.get("rn") or "").strip(),
                "bbsSeq": str(row.get("bbs_seq") or "").strip(),
                "title": title,
                "publishedDate": str(row.get("tm") or "").strip()[:10],
            }
            if (
                normalized["bbsSeq"]
                and normalized["rn"].isdigit()
                and re.fullmatch(r"20\d{2}-\d{2}-\d{2}", normalized["publishedDate"])
            ):
                result.append(normalized)
            try:
                total = max(total, int(str(row.get("totCnt") or 0)))
            except ValueError:
                pass
        return result, total

    def attachments(self, bbs_seq: str) -> list[dict[str, str]]:
        fields = {"bbs_seq": bbs_seq, "bbs_id": KRX_BBS_ID}
        raw = self.open_text(
            KRX_FILE_LIST_URL,
            urllib.parse.urlencode(fields).encode("utf-8"),
            headers={"Origin": KRX_ORIGIN},
        )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"KRX 첨부목록이 JSON이 아닙니다: {bbs_seq}") from exc
        rows = payload.get("block1", []) if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            raise RuntimeError(f"KRX 첨부목록 형식 오류: {bbs_seq}")
        result = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            sequence = str(row.get("attach_file_seq") or "").strip()
            filename = str(row.get("orgn_file_nm") or "").strip()
            if sequence and filename.lower().endswith(".pdf"):
                result.append({"attachFileSeq": sequence, "filename": filename})
        return result

    def download_pdf(self, bbs_seq: str, attach_seq: str) -> bytes:
        fields = {
            "name": "fileDown",
            "filetype": "att",
            "url": "COM/board_attach_down",
            "bbsSeq": bbs_seq,
            "attachFileSeq": attach_seq,
            "bbsId": KRX_BBS_ID,
        }
        last_error: Exception | None = None
        # 다운로드 OTP는 일회성이므로 재시도할 때마다 새로 발급한다.
        for attempt in range(4):
            try:
                code = self.otp(fields)
                body = urllib.parse.urlencode({"code": code}).encode("utf-8")
                data = self.open_bytes(
                    KRX_FILE_DOWNLOAD_URL,
                    data=body,
                    headers={"Origin": KRX_ORIGIN, "Referer": KRX_LIST_PAGE},
                    attempts=1,
                )
                if not data.startswith(b"%PDF-"):
                    raise RuntimeError("다운로드 파일이 PDF가 아닙니다.")
                if not 10_000 <= len(data) <= 10_000_000:
                    raise RuntimeError(
                        f"PDF 크기가 비정상입니다: {len(data):,} bytes"
                    )
                return data
            except Exception as exc:
                last_error = exc
                if attempt + 1 < 4:
                    time.sleep(min(8, 2**attempt))
        raise RuntimeError(f"KRX PDF 다운로드 실패: {last_error}")


def pdf_page_count_from_path(pdf_path: Path) -> int:
    if not shutil.which("pdfinfo"):
        raise RuntimeError("Poppler(pdfinfo)가 설치되지 않았습니다.")
    info = subprocess.run(
        ["pdfinfo", str(pdf_path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout
    page_match = re.search(r"(?m)^Pages:\s+(\d+)\s*$", info)
    pages = int(page_match.group(1)) if page_match else 0
    if pages < 1 or pages > 5:
        raise RuntimeError(f"PDF 페이지 수가 비정상입니다: {pages}")
    return pages


def pdf_page_count(pdf_bytes: bytes) -> int:
    with tempfile.TemporaryDirectory(prefix="krx-daily-info-") as directory:
        pdf_path = Path(directory) / "report.pdf"
        pdf_path.write_bytes(pdf_bytes)
        return pdf_page_count_from_path(pdf_path)


def pdf_to_text(pdf_bytes: bytes) -> tuple[str, int]:
    if not shutil.which("pdftotext"):
        raise RuntimeError("Poppler(pdftotext)가 설치되지 않았습니다.")
    with tempfile.TemporaryDirectory(prefix="krx-daily-") as directory:
        pdf_path = Path(directory) / "report.pdf"
        pdf_path.write_bytes(pdf_bytes)
        pages = pdf_page_count_from_path(pdf_path)
        text = subprocess.run(
            ["pdftotext", "-layout", "-nopgbrk", str(pdf_path), "-"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    if "배출권 거래시장 일일동향" not in re.sub(r"\s+", " ", text):
        raise RuntimeError("KRX 일일동향 제목을 찾지 못했습니다.")
    if "전체 종목" not in text or "Disclaimer" not in text:
        raise RuntimeError("KRX 전체 종목 표 또는 고지문을 찾지 못했습니다.")
    return text.replace("\xa0", " "), pages


def build_manual_override_report(
    post: dict[str, str],
    attachment: dict[str, str],
    sha256: str,
    file_size: int,
    pages: int,
    override: dict[str, Any],
) -> dict[str, Any]:
    source_key = f"krx:{post['bbsSeq']}:{attachment['attachFileSeq']}"
    validate_manual_override(source_key, sha256, override)
    expected_source = override["source"]
    actual_source = {
        "bbsSeq": post["bbsSeq"],
        "attachFileSeq": attachment["attachFileSeq"],
        "boardRn": int(post["rn"]),
        "filename": attachment["filename"],
        "publishedDate": post["publishedDate"],
        "fileSize": file_size,
        "pages": pages,
    }
    for key, actual in actual_source.items():
        if expected_source.get(key) != actual:
            raise RuntimeError(
                f"수기 전사 출처 메타데이터 불일치: "
                f"{source_key} {key}={actual!r}, expected={expected_source.get(key)!r}"
            )
    if "일일동향" not in post["title"]:
        raise RuntimeError(f"수기 전사 게시물 제목 오류: {source_key}")

    trade_date = expected_source["tradeDate"]
    filename_date = re.search(r"_(\d{6})\.pdf$", attachment["filename"], re.I)
    if not filename_date:
        raise RuntimeError(f"PDF 파일명에서 거래일을 찾지 못했습니다: {attachment['filename']}")
    filename_trade_date = datetime.strptime(
        filename_date.group(1), "%y%m%d"
    ).date().isoformat()
    if filename_trade_date != trade_date:
        raise RuntimeError(
            f"수기 전사 PDF 파일명·거래일 불일치: "
            f"{filename_trade_date} != {trade_date}"
        )

    gross = override["grossByMethod"]
    category_keys = expected_category_keys(trade_date)
    labels_by_key = {value: key for key, value in CATEGORY_KEYS.items()}
    participant_flows = []
    for category_key in category_keys:
        net_by_method = {
            method: (
                gross["buy"][method][category_key]
                - gross["sell"][method][category_key]
            )
            for method in MANUAL_METHODS
        }
        net_by_method["total"] = sum(net_by_method.values())
        participant_flows.append(
            {
                "categoryKey": category_key,
                "label": labels_by_key[category_key],
                "netByMethod": net_by_method,
            }
        )
    net_total = sum(row["netByMethod"]["total"] for row in participant_flows)
    if net_total != 0:
        raise RuntimeError(f"수기 전사 순거래량 합계 불일치: {source_key}, {net_total}")

    taxonomy = (
        "legacy_brokerage_members"
        if trade_date < "2026-03-17"
        else "financial_institutions_and_others"
    )
    source_page = (
        f"{KRX_ORIGIN}/contents/ETS/97/97010000/"
        f"ETS97010000S2.jsp?bbstype=5&bbs_seq={post['bbsSeq']}"
    )
    override_id = f"{source_key}@{sha256}"
    return {
        "tradeDate": trade_date,
        "publishedDate": post["publishedDate"],
        "boardRn": int(post["rn"]),
        "bbsSeq": post["bbsSeq"],
        "attachFileSeq": attachment["attachFileSeq"],
        "title": post["title"],
        "sourceKey": source_key,
        "sourceRevisionId": f"{source_key}:{sha256[:12]}",
        "sourcePageUrl": source_page,
        "filename": attachment["filename"],
        "sha256": sha256,
        "fileSize": file_size,
        "pages": pages,
        "parserVersion": COLLECTOR_VERSION,
        "participantScope": "all_instruments",
        "taxonomyVersion": taxonomy,
        "market": override["market"],
        "participantFlows": participant_flows,
        "validation": {
            "netTotal": net_total,
            "balanced": True,
        },
        "provenance": {
            "method": "manual_transcription_override",
            "overrideId": override_id,
            "auditNote": override["audit"]["note"],
        },
    }


def split_cells(line: str) -> list[str]:
    return [cell.strip() for cell in re.split(r"\s{2,}", line.strip()) if cell.strip()]


def parse_side_table(section: str, side: str) -> tuple[list[str], dict[str, dict[str, int]]]:
    lines = section.splitlines()
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if "할당대상업체" in line and "시장조성자" in line
        ),
        -1,
    )
    if header_index < 0:
        raise RuntimeError(f"전체 종목 {side} 참가자 헤더를 찾지 못했습니다.")
    labels = [clean_label(value) for value in split_cells(lines[header_index])]
    if not REQUIRED_STABLE_CATEGORIES.issubset(set(labels)):
        raise RuntimeError(f"전체 종목 {side} 참가자 분류가 예상과 다릅니다: {labels}")
    unknown = [label for label in labels if label not in CATEGORY_KEYS]
    if unknown:
        raise RuntimeError(f"알 수 없는 참가자 분류가 있습니다: {unknown}")

    methods: dict[str, dict[str, int]] = {}
    for line in lines[header_index + 1 :]:
        cells = split_cells(line)
        if not cells:
            continue
        method_label = clean_label(cells[0])
        method_key = METHOD_KEYS.get(method_label)
        if not method_key:
            continue
        if method_key in methods:
            raise RuntimeError(
                f"전체 종목 {side} {method_label} 행이 중복되었습니다."
            )
        values = cells[1:]
        if len(values) != len(labels):
            raise RuntimeError(
                f"전체 종목 {side} {method_label} 열 수 오류: "
                f"labels={labels}, values={values}"
            )
        methods[method_key] = {
            CATEGORY_KEYS[label]: numeric(value)
            for label, value in zip(labels, values, strict=True)
        }
    expected_methods = {"competitive", "negotiated", "auction", "total"}
    if set(methods) != expected_methods:
        missing = sorted(expected_methods - set(methods))
        extra = sorted(set(methods) - expected_methods)
        raise RuntimeError(
            f"전체 종목 {side} 거래방식 행 오류: missing={missing}, extra={extra}"
        )
    return labels, methods


def representative_market(text: str) -> dict[str, Any]:
    pattern = re.compile(
        r"(?m)^\s*((?:KAU|KCU)\d+)\s+"
        r"(?:종가\s*:\s*([\d,]+)원\s*"
        r"\(전일\s*대비\s*([+\-]?\s*[\d,]+)원\)\s*)?"
        r"거래량\s*:\s*([\d,]+)톤"
    )
    match = pattern.search(text)
    if not match:
        raise RuntimeError("대표 종목과 거래량을 찾지 못했습니다.")
    close = numeric(match.group(2)) if match.group(2) else None
    change = numeric(match.group(3).replace(" ", "")) if match.group(3) else None
    return {
        "symbol": match.group(1),
        "close": close,
        "change": change,
        "volume": numeric(match.group(4)),
    }


def parse_report(
    text: str,
    post: dict[str, str],
    attachment: dict[str, str],
    sha256: str,
    file_size: int,
    pages: int,
) -> dict[str, Any]:
    date_match = re.search(r"(?m)^\s*(20\d{2}-\d{2}-\d{2})\s*$", text)
    if not date_match:
        raise RuntimeError("PDF 내부 거래일을 찾지 못했습니다.")
    trade_date = date_match.group(1)
    filename_date = re.search(r"_(\d{6})\.pdf$", attachment["filename"], re.I)
    if not filename_date:
        raise RuntimeError(f"PDF 파일명에서 거래일을 찾지 못했습니다: {attachment['filename']}")
    expected = datetime.strptime(filename_date.group(1), "%y%m%d").date().isoformat()
    if expected != trade_date:
        raise RuntimeError(
            f"PDF 거래일과 파일명이 다릅니다: {trade_date} != {expected}"
        )

    overall_match = re.search(
        r"(?m)^\s*전체\s*종목\s+거래량\s*:\s*([\d,]+)톤\s*$", text
    )
    if not overall_match:
        raise RuntimeError("전체 종목 거래량을 찾지 못했습니다.")
    overall_volume = numeric(overall_match.group(1))
    overall_block = text[overall_match.start() :]
    overall_block = overall_block.split("Disclaimer", 1)[0]
    side_suffix = r"(?:[ \t]+\(단위[ \t]*:[ \t]*톤\))?[ \t]*$"
    buy_match = re.search(r"(?m)^[ \t]*매수" + side_suffix, overall_block)
    sell_match = re.search(r"(?m)^[ \t]*매도" + side_suffix, overall_block)
    if not buy_match or not sell_match or buy_match.start() >= sell_match.start():
        raise RuntimeError("전체 종목 매수·매도 표를 분리하지 못했습니다.")
    buy_labels, buy_methods = parse_side_table(
        overall_block[buy_match.end() : sell_match.start()], "매수"
    )
    sell_labels, sell_methods = parse_side_table(
        overall_block[sell_match.end() :], "매도"
    )
    if buy_labels != sell_labels:
        raise RuntimeError(
            f"매수·매도 참가자 분류가 다릅니다: {buy_labels} != {sell_labels}"
        )

    expected_labels = (
        ["할당대상업체", "시장조성자", "거래중개회원", "KOC전문회원"]
        if trade_date < "2026-03-17"
        else ["할당대상업체", "시장조성자", "금융기관", "기타", "KOC전문회원"]
    )
    if buy_labels != expected_labels:
        raise RuntimeError(
            f"{trade_date} 참가자 분류가 기준과 다릅니다: "
            f"{buy_labels} != {expected_labels}"
        )

    category_keys = [CATEGORY_KEYS[label] for label in buy_labels]
    participant_flows = []
    private_totals: list[tuple[int, int]] = []
    for label, key in zip(buy_labels, category_keys, strict=True):
        net_by_method = {}
        for method_key in ("competitive", "negotiated", "auction", "total"):
            buy = buy_methods.get(method_key, {}).get(key, 0)
            sell = sell_methods.get(method_key, {}).get(key, 0)
            net_by_method[method_key] = buy - sell
            if method_key == "total":
                private_totals.append((buy, sell))
        participant_flows.append(
            {
                "categoryKey": key,
                "label": label,
                # 공개 JSON에는 원표 전체를 재현하지 않고 기간 분석에 필요한
                # 파생지표인 순거래량만 최소 저장한다.
                "netByMethod": net_by_method,
            }
        )

    buy_total = sum(buy for buy, _ in private_totals)
    sell_total = sum(sell for _, sell in private_totals)
    net_total = sum(row["netByMethod"]["total"] for row in participant_flows)
    if buy_total != overall_volume or sell_total != overall_volume or net_total != 0:
        raise RuntimeError(
            "전체 종목 수급 합계 검증 실패: "
            f"volume={overall_volume}, buy={buy_total}, sell={sell_total}, net={net_total}"
        )

    for method_key in ("competitive", "negotiated", "auction"):
        method_buy = sum(buy_methods[method_key].values())
        method_sell = sum(sell_methods[method_key].values())
        if method_buy != method_sell:
            raise RuntimeError(
                f"전체 종목 {method_key} 매수·매도 합계 불일치: "
                f"{method_buy} != {method_sell}"
            )
    for key in category_keys:
        buy_parts = sum(
            buy_methods[method_key][key]
            for method_key in ("competitive", "negotiated", "auction")
        )
        sell_parts = sum(
            sell_methods[method_key][key]
            for method_key in ("competitive", "negotiated", "auction")
        )
        if buy_parts != buy_methods["total"][key]:
            raise RuntimeError(f"{key} 매수 합계가 거래방식 합과 다릅니다.")
        if sell_parts != sell_methods["total"][key]:
            raise RuntimeError(f"{key} 매도 합계가 거래방식 합과 다릅니다.")

    taxonomy = (
        "legacy_brokerage_members"
        if "거래중개회원" in buy_labels
        else "financial_institutions_and_others"
    )
    bbs_seq = post["bbsSeq"]
    attach_seq = attachment["attachFileSeq"]
    source_key = f"krx:{bbs_seq}:{attach_seq}"
    source_page = (
        f"{KRX_ORIGIN}/contents/ETS/97/97010000/"
        f"ETS97010000S2.jsp?bbstype=5&bbs_seq={bbs_seq}"
    )
    representative = representative_market(text)
    if representative["volume"] > overall_volume:
        raise RuntimeError(
            "대표 종목 거래량이 전체 종목 거래량보다 큽니다: "
            f"{representative['volume']} > {overall_volume}"
        )
    return {
        "tradeDate": trade_date,
        "publishedDate": post["publishedDate"],
        "boardRn": int(post["rn"]),
        "bbsSeq": bbs_seq,
        "attachFileSeq": attach_seq,
        "title": post["title"],
        "sourceKey": source_key,
        "sourceRevisionId": f"{source_key}:{sha256[:12]}",
        "sourcePageUrl": source_page,
        "filename": attachment["filename"],
        "sha256": sha256,
        "fileSize": file_size,
        "pages": pages,
        "parserVersion": COLLECTOR_VERSION,
        "participantScope": "all_instruments",
        "taxonomyVersion": taxonomy,
        "market": {
            "totalVolume": overall_volume,
            "representativeInstrument": representative,
        },
        "participantFlows": participant_flows,
        "validation": {
            "netTotal": net_total,
            "balanced": True,
        },
    }


def validate_report(report: dict[str, Any]) -> None:
    trade_date = str(report.get("tradeDate") or "")
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", trade_date):
        raise RuntimeError(f"거래일 형식 오류: {trade_date}")
    market = report.get("market") or {}
    total_volume = int(market.get("totalVolume") or 0)
    if total_volume < 0:
        raise RuntimeError(f"음수 거래량: {trade_date}")
    representative = market.get("representativeInstrument") or {}
    representative_volume = representative.get("volume")
    if type(representative_volume) is not int or not 0 <= representative_volume <= total_volume:
        raise RuntimeError(f"대표 종목 거래량 오류: {trade_date}")
    if report.get("participantScope") != "all_instruments":
        raise RuntimeError(f"참가자 범위 오류: {trade_date}")
    pdf_url = report.get("pdfUrl")
    if pdf_url is not None and pdf_url != pdf_url_for_trade_date(trade_date):
        raise RuntimeError(f"PDF 공개 경로 메타데이터 오류: {trade_date}")
    flows = report.get("participantFlows") or []
    if not isinstance(flows, list) or len(flows) < 3:
        raise RuntimeError(f"참가자 수급 누락: {trade_date}")
    expected_keys = (
        {
            "liable_entities",
            "market_makers",
            "brokerage_members",
            "koc_specialists",
        }
        if trade_date < "2026-03-17"
        else {
            "liable_entities",
            "market_makers",
            "financial_institutions",
            "others",
            "koc_specialists",
        }
    )
    expected_taxonomy = (
        "legacy_brokerage_members"
        if trade_date < "2026-03-17"
        else "financial_institutions_and_others"
    )
    if report.get("taxonomyVersion") != expected_taxonomy:
        raise RuntimeError(f"참가자 분류 버전 오류: {trade_date}")
    actual_keys = [str(row.get("categoryKey") or "") for row in flows]
    if len(actual_keys) != len(set(actual_keys)) or set(actual_keys) != expected_keys:
        raise RuntimeError(f"참가자 분류 키 오류: {trade_date}, {actual_keys}")
    required_methods = {"competitive", "negotiated", "auction", "total"}
    for row in flows:
        if CATEGORY_KEYS.get(clean_label(str(row.get("label") or ""))) != row.get("categoryKey"):
            raise RuntimeError(f"참가자 표시명 오류: {trade_date}")
        methods = row.get("netByMethod")
        if not isinstance(methods, dict) or set(methods) != required_methods:
            raise RuntimeError(f"거래방식 순거래량 누락: {trade_date}")
        if any(type(value) is not int for value in methods.values()):
            raise RuntimeError(f"순거래량 숫자 형식 오류: {trade_date}")
        if methods["total"] != sum(
            methods[key] for key in ("competitive", "negotiated", "auction")
        ):
            raise RuntimeError(f"참가자 순거래량 방식 합계 오류: {trade_date}")
    net = sum(row["netByMethod"]["total"] for row in flows)
    if net != 0:
        raise RuntimeError(f"참가자 순거래량 합계 검증 실패: {trade_date}, {net}")
    provenance = report.get("provenance")
    if provenance is not None:
        if not isinstance(provenance, dict) or set(provenance) != {
            "method",
            "overrideId",
            "auditNote",
        }:
            raise RuntimeError(f"수기 전사 출처 이력 형식 오류: {trade_date}")
        if provenance.get("method") != "manual_transcription_override":
            raise RuntimeError(f"수기 전사 출처 이력 방식 오류: {trade_date}")
        expected_override_id = f"{report.get('sourceKey')}@{report.get('sha256')}"
        if provenance.get("overrideId") != expected_override_id:
            raise RuntimeError(f"수기 전사 출처 이력 식별자 오류: {trade_date}")
        if len(str(provenance.get("auditNote") or "").strip()) < 20:
            raise RuntimeError(f"수기 전사 출처 이력 감사 메모 누락: {trade_date}")


def load_months() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not MONTH_ROOT.is_dir():
        return result
    for path in sorted(MONTH_ROOT.glob("????-??.json")):
        payload = read_json(path, {}, strict=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise RuntimeError(f"월별 JSON 형식이 올바르지 않습니다: {path}")
        if payload.get("month") != path.stem:
            raise RuntimeError(f"월별 JSON의 month가 파일명과 다릅니다: {path}")
        result[path.stem] = payload
    return result


def load_sources() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not SOURCE_ROOT.is_dir():
        return result
    for path in sorted(SOURCE_ROOT.glob("????.json")):
        payload = read_json(path, {}, strict=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise RuntimeError(f"출처 JSON 형식이 올바르지 않습니다: {path}")
        for item in payload.get("items", []) if isinstance(payload, dict) else []:
            if isinstance(item, dict) and item.get("sourceKey"):
                source_key = str(item["sourceKey"])
                if source_key in result:
                    raise RuntimeError(f"출처 키가 중복되었습니다: {source_key}")
                result[source_key] = item
    return result


def report_items(months: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for payload in months.values():
        items.extend(item for item in payload.get("items", []) if isinstance(item, dict))
    return sorted(items, key=lambda item: str(item.get("tradeDate") or ""))


def active_report_for_date(
    months: dict[str, dict[str, Any]], trade_date: str
) -> dict[str, Any] | None:
    payload = months.get(trade_date[:7]) or {}
    return next(
        (
            item
            for item in payload.get("items", [])
            if isinstance(item, dict) and item.get("tradeDate") == trade_date
        ),
        None,
    )


def report_has_valid_archive(report: dict[str, Any]) -> bool:
    if report.get("pdfUrl") is None:
        return False
    try:
        validate_archived_pdf(report)
    except RuntimeError:
        return False
    return True


def remove_orphan_pdf_archives(months: dict[str, dict[str, Any]]) -> int:
    expected = {
        pdf_path_for_trade_date(str(report.get("tradeDate") or "")).resolve()
        for report in report_items(months)
        if report.get("pdfUrl") is not None
    }
    removed = 0
    if not PDF_ROOT.is_dir():
        return removed
    for path in PDF_ROOT.glob("20??-??/20??-??-??.pdf"):
        if path.resolve() not in expected:
            path.unlink()
            removed += 1
    for directory in sorted(PDF_ROOT.glob("20??-??"), reverse=True):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    return removed


def validate_dataset(
    months: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    index: dict[str, Any],
    latest_payload: dict[str, Any],
    quality: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(index, dict) or index.get("schemaVersion") != SCHEMA_VERSION:
        raise RuntimeError("index.json schemaVersion이 올바르지 않습니다.")
    seen_dates: set[str] = set()
    seen_source_keys: set[str] = set()
    items: list[dict[str, Any]] = []
    for month, payload in sorted(months.items()):
        if not re.fullmatch(r"20\d{2}-(?:0[1-9]|1[0-2])", month):
            raise RuntimeError(f"월별 데이터 키 오류: {month}")
        if payload.get("schemaVersion") != SCHEMA_VERSION or payload.get("month") != month:
            raise RuntimeError(f"월별 데이터 메타정보 오류: {month}")
        month_items = payload.get("items")
        if not isinstance(month_items, list) or not month_items:
            raise RuntimeError(f"빈 월별 데이터는 저장하지 않습니다: {month}")
        for report in month_items:
            if not isinstance(report, dict):
                raise RuntimeError(f"월별 거래일 항목 형식 오류: {month}")
            validate_report(report)
            trade_date = report["tradeDate"]
            try:
                parsed_date = datetime.strptime(trade_date, "%Y-%m-%d").date().isoformat()
            except ValueError as exc:
                raise RuntimeError(f"실제 달력에 없는 거래일입니다: {trade_date}") from exc
            if parsed_date != trade_date or trade_date[:7] != month:
                raise RuntimeError(f"거래일의 월별 파일 배치 오류: {trade_date}, {month}")
            if trade_date in seen_dates:
                raise RuntimeError(f"거래일 중복: {trade_date}")
            seen_dates.add(trade_date)
            source_key = str(report.get("sourceKey") or "")
            if not source_key or source_key in seen_source_keys:
                raise RuntimeError(f"활성 출처 키 누락 또는 중복: {source_key}")
            seen_source_keys.add(source_key)
            source = sources.get(source_key)
            if not isinstance(source, dict):
                raise RuntimeError(f"출처 메타데이터 누락: {source_key}")
            comparisons = {
                "tradeDate": trade_date,
                "publishedDate": report.get("publishedDate"),
                "sha256": report.get("sha256"),
                "filename": report.get("filename"),
                "revisionId": report.get("sourceRevisionId"),
                "parserVersion": report.get("parserVersion"),
            }
            if report.get("provenance") is not None:
                comparisons["provenance"] = report.get("provenance")
            if report.get("pdfUrl") != source.get("pdfUrl"):
                raise RuntimeError(
                    f"출처 {source_key}의 pdfUrl이 월별 데이터와 다릅니다."
                )
            for key, expected in comparisons.items():
                if source.get(key) != expected:
                    raise RuntimeError(
                        f"출처 {source_key}의 {key}가 월별 데이터와 다릅니다."
                    )
            validate_archived_pdf(report)
            items.append(report)
    items.sort(key=lambda item: item["tradeDate"])

    if int(index.get("itemCount") or 0) != len(items):
        raise RuntimeError("index.json itemCount가 월별 데이터와 다릅니다.")
    if index.get("availableMonths") != sorted(months):
        raise RuntimeError("index.json availableMonths가 월별 파일과 다릅니다.")
    first = items[0]["tradeDate"] if items else None
    latest = items[-1]["tradeDate"] if items else None
    if index.get("firstTradeDate") != first or index.get("lastTradeDate") != latest:
        raise RuntimeError("index.json 최초·최신 거래일이 월별 데이터와 다릅니다.")
    if (
        not isinstance(latest_payload, dict)
        or latest_payload.get("schemaVersion") != SCHEMA_VERSION
    ):
        raise RuntimeError("latest.json 형식이 올바르지 않습니다.")
    if (latest_payload.get("item") or None) != (items[-1] if items else None):
        raise RuntimeError("latest.json이 최신 월별 데이터와 다릅니다.")

    if not isinstance(quality, dict) or quality.get("schemaVersion") != SCHEMA_VERSION:
        raise RuntimeError("quality.json 형식이 올바르지 않습니다.")
    failures = quality.get("failures")
    if not isinstance(failures, list):
        raise RuntimeError("quality.json failures 형식이 올바르지 않습니다.")
    quality_status = quality.get("status")
    if quality_status not in {"pending", "partial", "valid"}:
        raise RuntimeError("quality.json status가 올바르지 않습니다.")
    if quality_status == "valid" and failures:
        raise RuntimeError("valid 품질 상태에 실패 기록이 남아 있습니다.")
    if quality_status == "partial" and not failures:
        raise RuntimeError("partial 품질 상태에 실패 기록이 없습니다.")

    backfill = index.get("backfill")
    if not isinstance(backfill, dict):
        raise RuntimeError("index.json backfill 형식이 올바르지 않습니다.")
    status = backfill.get("status")
    allowed_statuses = {
        "not_started",
        "in_progress",
        "complete",
        "complete_with_errors",
    }
    if status not in allowed_statuses:
        raise RuntimeError("index.json backfill status가 올바르지 않습니다.")
    board_count = int(backfill.get("boardCount") or 0)
    next_rn = int(backfill.get("nextRn") or 0)
    if status in {"complete", "complete_with_errors"} and not (
        board_count > 0 and next_rn > board_count
    ):
        raise RuntimeError("백필 완료 상태와 커서가 일치하지 않습니다.")
    if status == "complete_with_errors" and not failures:
        raise RuntimeError("실패 없는 백필은 complete_with_errors일 수 없습니다.")
    return items


def upsert_report(
    months: dict[str, dict[str, Any]], report: dict[str, Any]
) -> set[str]:
    changed: set[str] = set()
    source_key = report["sourceKey"]
    trade_date = report["tradeDate"]
    if any(
        item == report
        for payload in months.values()
        for item in payload.get("items", [])
        if isinstance(item, dict) and item.get("sourceKey") == source_key
    ):
        return changed
    incoming_rank = (
        str(report.get("publishedDate") or ""),
        int(report.get("bbsSeq") or 0),
        int(report.get("attachFileSeq") or 0),
    )
    blocked_by_newer_source = False
    # 정정본의 PDF 내부 거래일이 바뀌어도 기존 월에 같은 sourceKey가
    # 남지 않도록 모든 활성 월에서 먼저 제거한다.
    for existing_month, existing_payload in months.items():
        old_items = [
            item
            for item in existing_payload.get("items", [])
            if isinstance(item, dict)
        ]
        kept = []
        for item in old_items:
            if item.get("sourceKey") == source_key:
                continue
            if item.get("tradeDate") == trade_date:
                existing_source_parts = str(item.get("sourceKey") or "").split(":")
                existing_rank = (
                    str(item.get("publishedDate") or ""),
                    int(item.get("bbsSeq") or (existing_source_parts[1] if len(existing_source_parts) > 1 else 0)),
                    int(item.get("attachFileSeq") or (existing_source_parts[2] if len(existing_source_parts) > 2 else 0)),
                )
                if existing_rank > incoming_rank:
                    blocked_by_newer_source = True
                    kept.append(item)
                continue
            kept.append(item)
        if len(kept) != len(old_items):
            existing_payload["items"] = kept
            existing_payload["generatedAt"] = now_kst()
            changed.add(existing_month)

    if blocked_by_newer_source:
        return changed

    month = report["tradeDate"][:7]
    payload = months.setdefault(
        month,
        {"schemaVersion": SCHEMA_VERSION, "month": month, "items": []},
    )
    items = [item for item in payload.get("items", []) if isinstance(item, dict)]
    items.append(report)
    payload["items"] = sorted(items, key=lambda item: item["tradeDate"])
    payload["generatedAt"] = now_kst()
    changed.add(month)
    return changed


def write_months(months: dict[str, dict[str, Any]], changed: set[str]) -> None:
    for month in sorted(changed):
        path = MONTH_ROOT / f"{month}.json"
        if month in months:
            write_json(path, months[month])
        else:
            path.unlink(missing_ok=True)


def write_sources(sources: dict[str, dict[str, Any]], changed_years: set[str]) -> None:
    for year in sorted(changed_years):
        items = sorted(
            (
                item
                for item in sources.values()
                if str(item.get("publishedDate") or "")[:4] == year
            ),
            key=lambda item: (
                str(item.get("publishedDate") or ""),
                int(str(item.get("bbsSeq") or 0)),
                int(str(item.get("attachFileSeq") or 0)),
            ),
        )
        write_json(
            SOURCE_ROOT / f"{year}.json",
            {
                "schemaVersion": SCHEMA_VERSION,
                "year": year,
                "items": items,
            },
        )


def build_index(
    months: dict[str, dict[str, Any]],
    old_index: dict[str, Any],
    board_count: int,
    next_rn: int,
    failure_count: int,
) -> dict[str, Any]:
    items = report_items(months)
    complete = board_count > 0 and next_rn > board_count
    backfill_status = (
        "complete_with_errors"
        if complete and failure_count
        else "complete"
        if complete
        else "in_progress"
    )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "source": "한국거래소 배출권시장 정보플랫폼 시장동향",
        "sourceUrl": KRX_LIST_PAGE,
        "lastSync": now_kst(),
        "firstTradeDate": items[0]["tradeDate"] if items else None,
        "lastTradeDate": items[-1]["tradeDate"] if items else None,
        "itemCount": len(items),
        "availableMonths": sorted(months),
        "taxonomy": [
            {
                "id": "legacy_brokerage_members",
                "from": "2024-04-01",
                "to": "2026-03-16",
                "labels": [
                    "할당대상업체",
                    "시장조성자",
                    "거래중개회원",
                    "KOC전문회원",
                ],
            },
            {
                "id": "financial_institutions_and_others",
                "from": "2026-03-17",
                "to": None,
                "labels": [
                    "할당대상업체",
                    "시장조성자",
                    "금융기관",
                    "기타",
                    "KOC전문회원",
                ],
            },
        ],
        "backfill": {
            "status": backfill_status,
            "boardCount": board_count,
            # 완료 뒤에도 정수 커서를 유지해야 다음 신규 게시물만 이어서
            # 처리하며, None을 1로 오해해 백필을 재시작하지 않는다.
            "nextRn": next_rn,
            "failureCount": failure_count,
            "progressPercent": (
                100
                if complete
                else round(max(0, next_rn - 1) / max(board_count, 1) * 100, 1)
            ),
        },
        "previousLastSync": old_index.get("lastSync"),
    }


def validate_storage() -> int:
    manual_overrides = load_manual_overrides()
    index = read_json(INDEX_PATH, {}, strict=True)
    latest_payload = read_json(LATEST_PATH, {}, strict=True)
    quality = read_json(QUALITY_PATH, {}, strict=True)
    months = load_months()
    sources = load_sources()
    items = validate_dataset(months, sources, index, latest_payload, quality)
    override_revisions = sum(len(revisions) for revisions in manual_overrides.values())
    print(
        f"KRX 일일동향 검증 완료: {len(items)}거래일, {len(months)}개월, "
        f"수기 전사 {override_revisions}개정본"
    )
    return 0


def collect(mode: str, backfill_pages: int, revision_checks: int) -> int:
    manual_overrides = load_manual_overrides()
    old_index = read_json(INDEX_PATH, {}, strict=True)
    old_latest = read_json(LATEST_PATH, {}, strict=True)
    old_quality = read_json(QUALITY_PATH, {}, strict=True)
    months = load_months()
    sources = load_sources()
    existing_items = validate_dataset(
        months, sources, old_index, old_latest, old_quality
    )
    missing_archive_source_keys = {
        str(report.get("sourceKey") or "")
        for report in existing_items
        if not report_has_valid_archive(report)
    }
    existing_failures = {
        str(item.get("sourceKey") or ""): item
        for item in old_quality.get("failures", [])
        if isinstance(item, dict) and item.get("sourceKey")
    }

    session = KrxSession()
    first_page, board_count = session.list_posts(1)
    if board_count < 1 or not first_page:
        raise RuntimeError("KRX 일일동향 게시물을 찾지 못했습니다.")
    backfill = old_index.get("backfill") if isinstance(old_index, dict) else {}
    next_rn_raw = backfill.get("nextRn") if isinstance(backfill, dict) else None
    next_rn = int(next_rn_raw) if str(next_rn_raw or "").isdigit() else 1
    next_rn = max(1, next_rn)

    # 1페이지는 신규·정정 확인용으로 항상 읽되, 백필 커서에는 실제로
    # 과거자료 처리 대상으로 선택한 페이지만 반영한다.
    pages = {1} if mode == "incremental" else set()
    backfill_page_numbers: set[int] = set()
    if next_rn <= board_count:
        target_rn = next_rn
        for _ in range(max(1, backfill_pages)):
            page = max(1, math.ceil((board_count - target_rn + 1) / 10))
            pages.add(page)
            backfill_page_numbers.add(page)
            estimated_high_rn = board_count - (page - 1) * 10
            target_rn = max(target_rn + 1, estimated_high_rn + 1)
            if target_rn > board_count:
                break
    # JSON 백필이 이미 완료됐어도 PDF가 없는 활성 보고서의
    # rn을 목록 페이지로 역산해 지정한 페이지 수 범위에서 재탐색한다.
    if mode == "backfill" and missing_archive_source_keys:
        missing_rns = sorted(
            {
                int(str(source.get("rn") or 0))
                for source_key, source in sources.items()
                if source_key in missing_archive_source_keys
                and str(source.get("rn") or "").isdigit()
                and 1 <= int(str(source.get("rn") or 0)) <= board_count
            }
        )
        for missing_rn in missing_rns:
            page = max(1, math.ceil((board_count - missing_rn + 1) / 10))
            if page in pages:
                continue
            if len(pages) >= backfill_pages:
                break
            pages.add(page)
    # 과거에 실패한 게시물이 최신 페이지 밖으로 밀려도 rn으로 해당
    # 페이지를 다시 찾아 영구 누락되지 않게 한다.
    for failure in existing_failures.values():
        failure_rn = str(failure.get("rn") or "")
        if failure_rn.isdigit() and 1 <= int(failure_rn) <= board_count:
            retry_page = max(1, math.ceil((board_count - int(failure_rn) + 1) / 10))
            pages.add(retry_page)
    # backfill 모드도 지정한 페이지 수만 처리해 KRX 과부하와 Actions
    # 시간초과를 피한다. 다음 실행은 index.json의 nextRn부터 이어진다.

    posts_by_seq: dict[str, dict[str, str]] = (
        {post["bbsSeq"]: post for post in first_page}
        if mode == "incremental"
        else {}
    )
    scanned_backfill_rn = next_rn - 1
    for page in sorted(pages):
        if page == 1:
            rows, page_total = first_page, board_count
        else:
            rows, page_total = session.list_posts(page)
        if page_total != board_count:
            raise RuntimeError(
                f"KRX 목록 건수가 수집 중 변경되었습니다: {board_count} != {page_total}"
            )
        actual_rns = {int(post["rn"]) for post in rows}
        actual_sequences = [post["bbsSeq"] for post in rows]
        if len(actual_rns) != len(rows) or len(set(actual_sequences)) != len(rows):
            raise RuntimeError(f"KRX 목록 {page}페이지 rn 또는 bbsSeq가 중복되었습니다.")
        expected_low = max(1, board_count - page * 10 + 1)
        expected_high = board_count - (page - 1) * 10
        expected_rns = set(range(expected_low, expected_high + 1))
        if actual_rns != expected_rns:
            raise RuntimeError(
                f"KRX 목록 {page}페이지 rn 연속성 오류: "
                f"actual={sorted(actual_rns)}, expected={sorted(expected_rns)}"
            )
        for post in rows:
            posts_by_seq[post["bbsSeq"]] = post
        if page in backfill_page_numbers:
            eligible = [
                int(post["rn"])
                for post in rows
                if int(post["rn"]) >= next_rn
            ]
            if eligible:
                scanned_backfill_rn = max(scanned_backfill_rn, max(eligible))

    candidates: list[tuple[dict[str, str], dict[str, str]]] = []
    attachment_discovery_failures: list[tuple[dict[str, str], Exception]] = []
    recovered_attachment_keys: set[str] = set()
    newest_sequences = (
        {post["bbsSeq"] for post in first_page[: max(0, revision_checks)]}
        if mode == "incremental"
        else set()
    )
    for post in sorted(posts_by_seq.values(), key=lambda item: int(item["rn"])):
        discovery_key = f"krx:{post['bbsSeq']}:attachments"
        try:
            attachments = session.attachments(post["bbsSeq"])
            if not attachments:
                raise RuntimeError("PDF 첨부파일을 찾지 못했습니다.")
            recovered_attachment_keys.add(discovery_key)
        except Exception as exc:
            attachment_discovery_failures.append((post, exc))
            continue
        for attachment in attachments:
            source_key = f"krx:{post['bbsSeq']}:{attachment['attachFileSeq']}"
            force = post["bbsSeq"] in newest_sequences
            previous_source = sources.get(source_key) or {}
            if (
                source_key not in sources
                or force
                or source_key in missing_archive_source_keys
                or source_key in existing_failures
                or previous_source.get("parserVersion") != COLLECTOR_VERSION
            ):
                candidates.append((post, attachment))

    changed_months: set[str] = set()
    changed_source_years: set[str] = set()
    failures = dict(existing_failures)
    for discovery_key in recovered_attachment_keys:
        failures.pop(discovery_key, None)
    for post, exc in attachment_discovery_failures:
        discovery_key = f"krx:{post['bbsSeq']}:attachments"
        failures[discovery_key] = {
            "sourceKey": discovery_key,
            "bbsSeq": post["bbsSeq"],
            "rn": post["rn"],
            "attachFileSeq": None,
            "filename": None,
            "publishedDate": post["publishedDate"],
            "title": post["title"],
            "error": f"{type(exc).__name__}: {exc}",
            "lastAttemptAt": now_kst(),
        }
        print(f"::warning::{discovery_key} 처리 실패: {exc}")
    report_changes = 0
    source_changes = 0
    pdf_changes = 0
    for post, attachment in candidates:
        source_key = f"krx:{post['bbsSeq']}:{attachment['attachFileSeq']}"
        try:
            pdf_bytes = session.download_pdf(
                post["bbsSeq"], attachment["attachFileSeq"]
            )
            sha256 = hashlib.sha256(pdf_bytes).hexdigest()
            previous = sources.get(source_key)
            manual_override = select_manual_override(
                manual_overrides,
                source_key,
                sha256,
            )
            if manual_override is None:
                text, pages_count = pdf_to_text(pdf_bytes)
                report = parse_report(
                    text,
                    post,
                    attachment,
                    sha256,
                    len(pdf_bytes),
                    pages_count,
                )
            else:
                # 수기 전사는 등록된 원본 해시가 정확히 일치할 때만 사용한다.
                # 텍스트 레이어가 없어도 pdfinfo로 페이지 수를 독립 검증한다.
                pages_count = pdf_page_count(pdf_bytes)
                report = build_manual_override_report(
                    post,
                    attachment,
                    sha256,
                    len(pdf_bytes),
                    pages_count,
                    manual_override,
                )
            report["pdfUrl"] = pdf_url_for_trade_date(report["tradeDate"])
            validate_report(report)
            report_month_changes = upsert_report(months, report)
            if report_month_changes:
                changed_months.update(report_month_changes)
                report_changes += 1
            active_report = active_report_for_date(months, report["tradeDate"])
            is_active_report = bool(
                active_report
                and active_report.get("sourceKey") == source_key
                and active_report.get("sourceRevisionId")
                == report.get("sourceRevisionId")
                and active_report.get("sha256") == sha256
            )
            # 같은 거래일의 더 오래된 정정본은 출처 이력만
            # 갱신하고, 활성 보고서 PDF를 절대 덮어쓰지 않는다.
            if is_active_report:
                if archive_pdf(active_report, pdf_bytes):
                    pdf_changes += 1
            published_year = post["publishedDate"][:4]
            revision_id = report["sourceRevisionId"]
            source_core = {
                "sourceKey": source_key,
                "revisionId": revision_id,
                "bbsSeq": post["bbsSeq"],
                "rn": post["rn"],
                "attachFileSeq": attachment["attachFileSeq"],
                "title": post["title"],
                "publishedDate": post["publishedDate"],
                "tradeDate": report["tradeDate"],
                "filename": attachment["filename"],
                "sourcePageUrl": report["sourcePageUrl"],
                "sha256": sha256,
                "fileSize": len(pdf_bytes),
                "parserVersion": COLLECTOR_VERSION,
                "parseStatus": "valid",
                "supersedes": (
                    previous.get("revisionId")
                    if previous and previous.get("revisionId") != revision_id
                    else None
                ),
            }
            if is_active_report:
                source_core["pdfUrl"] = report["pdfUrl"]
            if report.get("provenance") is not None:
                source_core["provenance"] = report["provenance"]
            source_changed = not previous or any(
                previous.get(key) != value for key, value in source_core.items()
            )
            source_changed = source_changed or (
                previous is not None
                and ("pdfUrl" in previous) != ("pdfUrl" in source_core)
            )
            if source_changed:
                sources[source_key] = {**source_core, "retrievedAt": now_kst()}
                changed_source_years.add(published_year)
                if previous:
                    previous_year = str(previous.get("publishedDate") or "")[:4]
                    if re.fullmatch(r"20\d{2}", previous_year):
                        changed_source_years.add(previous_year)
                source_changes += 1
            failures.pop(source_key, None)
            print(
                f"반영: {report['tradeDate']} {attachment['filename']} "
                f"전체 {report['market']['totalVolume']:,}톤"
            )
        except Exception as exc:  # 개별 과거 PDF 실패가 전체 누적을 막지 않는다.
            failures[source_key] = {
                "sourceKey": source_key,
                "bbsSeq": post["bbsSeq"],
                "rn": post["rn"],
                "attachFileSeq": attachment["attachFileSeq"],
                "filename": attachment["filename"],
                "publishedDate": post["publishedDate"],
                "title": post["title"],
                "error": f"{type(exc).__name__}: {exc}",
                "lastAttemptAt": now_kst(),
            }
            print(f"::warning::{source_key} 처리 실패: {exc}")

    # 신규 정정본이 활성화되면 이전 출처의 pdfUrl을 제거해
    # 이전 SHA와 현재 거래일 PDF가 같은 링크를 가리키지 않게 한다.
    active_by_source = {
        str(report.get("sourceKey") or ""): report for report in report_items(months)
    }
    for source_key, source in list(sources.items()):
        active_report = active_by_source.get(source_key)
        expected_pdf_url = (
            active_report.get("pdfUrl") if active_report is not None else None
        )
        if source.get("pdfUrl") == expected_pdf_url and (
            expected_pdf_url is not None or "pdfUrl" not in source
        ):
            continue
        updated_source = dict(source)
        if expected_pdf_url is None:
            updated_source.pop("pdfUrl", None)
        else:
            updated_source["pdfUrl"] = expected_pdf_url
        updated_source["retrievedAt"] = now_kst()
        sources[source_key] = updated_source
        source_year = str(source.get("publishedDate") or "")[:4]
        if re.fullmatch(r"20\d{2}", source_year):
            changed_source_years.add(source_year)
        source_changes += 1

    orphan_pdf_changes = remove_orphan_pdf_archives(months)

    # 실패 건은 quality.json의 durable retry queue와 rn 기반 재탐색으로
    # 계속 재시도하고, 전체 백필 스캔 커서는 독립적으로 전진시킨다.
    next_rn_after = max(next_rn, scanned_backfill_rn + 1)
    if next_rn_after > board_count:
        next_rn_after = board_count + 1
    old_next_rn = int(next_rn_raw) if str(next_rn_raw or "").isdigit() else 1
    backfill_changed = (
        next_rn_after != old_next_rn
        or int((backfill or {}).get("boardCount") or 0) != board_count
    )
    failure_list = sorted(failures.values(), key=lambda item: item["sourceKey"])
    old_failure_signature = [
        (item.get("sourceKey"), item.get("error"))
        for item in old_quality.get("failures", [])
        if isinstance(item, dict)
    ]
    new_failure_signature = [
        (item.get("sourceKey"), item.get("error")) for item in failure_list
    ]
    failures_changed = old_failure_signature != new_failure_signature
    must_write = bool(
        changed_months
        or changed_source_years
        or pdf_changes
        or orphan_pdf_changes
        or backfill_changed
        or failures_changed
    )
    if not must_write:
        print("새 PDF·정정본·백필 진행 변경이 없습니다.")
        return 0

    for month in list(changed_months):
        if month in months and not months[month].get("items"):
            del months[month]
    index = build_index(
        months,
        old_index,
        board_count,
        next_rn_after,
        len(failure_list),
    )
    items = report_items(months)
    latest_payload = {
        "schemaVersion": SCHEMA_VERSION,
        "lastSync": index["lastSync"],
        "item": items[-1] if items else None,
    }
    completed_at = now_kst()
    quality_payload = {
        "schemaVersion": SCHEMA_VERSION,
        "collectorVersion": COLLECTOR_VERSION,
        "lastAttemptAt": completed_at,
        "lastCompletedAt": completed_at,
        "lastSuccessAt": (
            old_quality.get("lastSuccessAt") if failure_list else completed_at
        ),
        "status": "partial" if failure_list else "valid",
        "newOrRevisedReports": report_changes,
        "newOrRevisedSources": source_changes,
        "failures": failure_list,
    }

    # 파일을 교체하기 전에 완성될 스냅샷 전체를 메모리에서 검증한다.
    validate_dataset(months, sources, index, latest_payload, quality_payload)
    write_months(months, changed_months)
    write_sources(sources, changed_source_years)
    write_json(INDEX_PATH, index)
    write_json(LATEST_PATH, latest_payload)
    write_json(QUALITY_PATH, quality_payload)
    validate_storage()
    print(
        f"KRX 일일동향 저장 완료: 신규·정정 {report_changes}건, "
        f"PDF 저장 {pdf_changes}건·정리 {orphan_pdf_changes}건, "
        f"누적 {len(items)}거래일, 백필 {index['backfill']['progressPercent']}%"
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("incremental", "backfill", "validate"),
        default=os.getenv("KRX_DAILY_MODE", "incremental").strip().lower(),
    )
    parser.add_argument(
        "--backfill-pages",
        type=int,
        default=int(os.getenv("KRX_DAILY_BACKFILL_PAGES", "3")),
    )
    parser.add_argument(
        "--revision-checks",
        type=int,
        default=int(os.getenv("KRX_DAILY_REVISION_CHECKS", "3")),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print(f"KRX_DAILY_COLLECTOR_VERSION={COLLECTOR_VERSION}")
    if args.mode == "validate":
        return validate_storage()
    if not 1 <= args.backfill_pages <= 60:
        raise RuntimeError("backfill-pages는 1~60이어야 합니다.")
    if not 0 <= args.revision_checks <= 10:
        raise RuntimeError("revision-checks는 0~10이어야 합니다.")
    return collect(args.mode, args.backfill_pages, args.revision_checks)


if __name__ == "__main__":
    raise SystemExit(main())
