#!/usr/bin/env python3
"""국회 전체 의안에서 국내 배출권거래제 관련 법안을 선별해 저장한다."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "public" / "data" / "bills.json"
API_ROOT = "https://open.assembly.go.kr/portal/openapi"
ASSEMBLY_TERM = os.getenv("ASSEMBLY_TERM", "제22대").strip() or "제22대"
PAGE_SIZE = 100
REQUEST_TIMEOUT_SECONDS = 25
MAX_PAGES = 300
KST = timezone(timedelta(hours=9))

TITLE_QUERIES = (
    "배출권",
    "온실가스",
    "탄소중립",
    "기후위기",
    "탄소시장",
    "국제감축",
    "기후대응기금",
    "탄소국경",
)

STRONG_ETS = re.compile(
    r"온실가스\s*배출권|탄소\s*배출권|배출권\s*거래제|"
    r"(?<![A-Za-z0-9])K\s*[-_]?\s*ETS(?![A-Za-z0-9])|할당대상업체",
    re.IGNORECASE,
)
ETS_SCHEME = re.compile(
    r"할당|무상|유상|경매|거래|상쇄|외부사업|국제감축실적|이월|차입|제출|인증|"
    r"시장안정|예비분|중개|위탁|선물|불공정거래|과징금|배출허용총량",
    re.IGNORECASE,
)
INDIRECT_POLICY = re.compile(
    r"탄소중립|기후위기|기후대응기금|국제감축|탄소국경조정|(?<![A-Za-z0-9])CBAM(?![A-Za-z0-9])",
    re.IGNORECASE,
)
FALSE_POSITIVE = re.compile(
    r"자동차.{0,12}배출가스|대기오염물질|대기관리권역|대기환경보전|"
    r"폐기물|오수|악취|방류|탄소포인트|탄소섬유|탄소나노튜브|활성탄|일산화탄소",
    re.IGNORECASE,
)


def now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def clean_text(value: object, limit: int = 6000) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(str(value or "")))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def field(row: dict[str, Any], *names: str) -> str:
    upper = {str(key).upper(): value for key, value in row.items()}
    for name in names:
        value = upper.get(name.upper())
        if value is not None and str(value).strip():
            return clean_text(value)
    return ""


def iso_date(value: object) -> str:
    text = clean_text(value, 40)
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 8:
        candidate = f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    else:
        match = re.search(r"(20\d{2})[.\-/년\s]+(\d{1,2})[.\-/월\s]+(\d{1,2})", text)
        candidate = f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}" if match else ""
    try:
        datetime.strptime(candidate, "%Y-%m-%d")
        return candidate
    except (TypeError, ValueError):
        return ""


def api_rows(endpoint: str, api_key: str, params: dict[str, object]) -> tuple[list[dict], int]:
    query = urllib.parse.urlencode({
        "KEY": api_key,
        "Type": "json",
        "pIndex": 1,
        "pSize": PAGE_SIZE,
        **params,
    })
    request = urllib.request.Request(
        f"{API_ROOT}/{endpoint}?{query}",
        headers={"User-Agent": "ETS-LIVE-DASHBOARD/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))

    container = next(
        (value for key, value in payload.items() if str(key).upper() == endpoint.upper()),
        None,
    )
    if not isinstance(container, list):
        result = payload.get("RESULT") if isinstance(payload, dict) else None
        message = result.get("MESSAGE") if isinstance(result, dict) else "응답 형식이 올바르지 않습니다."
        raise RuntimeError(f"{endpoint}: {message}")

    rows: list[dict] = []
    total = 0
    result_code = ""
    result_message = ""
    for block in container:
        if not isinstance(block, dict):
            continue
        if isinstance(block.get("row"), list):
            rows.extend(item for item in block["row"] if isinstance(item, dict))
        for head in block.get("head", []) if isinstance(block.get("head"), list) else []:
            if not isinstance(head, dict):
                continue
            try:
                total = max(total, int(head.get("list_total_count") or 0))
            except (TypeError, ValueError):
                pass
            if isinstance(head.get("RESULT"), dict):
                result_code = str(head["RESULT"].get("CODE", ""))
                result_message = str(head["RESULT"].get("MESSAGE", ""))
    if result_code and result_code not in {"INFO-000", "INFO-200"}:
        raise RuntimeError(f"{endpoint}: {result_message or result_code}")
    return rows, total


def fetch_all_rows(endpoint: str, api_key: str, params: dict[str, object]) -> list[dict]:
    output: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        rows, total = api_rows(endpoint, api_key, {**params, "pIndex": page, "pSize": PAGE_SIZE})
        output.extend(rows)
        if not rows or (total > 0 and len(output) >= total) or (total <= 0 and len(rows) < PAGE_SIZE):
            return output
    raise RuntimeError(f"{endpoint}: 최대 {MAX_PAGES}페이지를 초과해 응답을 중단했습니다.")


def row_key(row: dict[str, Any]) -> str:
    bill_id = field(row, "BILL_ID")
    bill_no = field(row, "BILL_NO")
    if bill_id:
        return bill_id
    return f"{ASSEMBLY_TERM}:{bill_no}" if bill_no else ""


def title_candidate(row: dict[str, Any]) -> bool:
    title = field(row, "BILL_NM", "BILL_NAME")
    return bool(STRONG_ETS.search(title) or INDIRECT_POLICY.search(title) or re.search(r"온실가스|탄소시장", title))


def proximity_match(text: str) -> bool:
    for match in STRONG_ETS.finditer(text):
        window = text[max(0, match.start() - 80) : match.end() + 80]
        if ETS_SCHEME.search(window):
            return True
    return False


def relevance_for(title: str, summary: str) -> tuple[str, int, str] | None:
    combined = f"{title} {summary}".strip()
    strong_title = bool(STRONG_ETS.search(title))
    if FALSE_POSITIVE.search(combined) and not strong_title and not proximity_match(summary):
        return None
    if re.search(r"온실가스\s*배출권의\s*할당\s*및\s*거래에\s*관한\s*법률", title):
        return "직접", 100, "배출권거래법 제·개정안"
    if strong_title:
        return "직접", 90, "법안명에 배출권거래제 핵심어 포함"
    if proximity_match(summary):
        return "높음", 65, "주요내용에서 배출권 제도와 세부 조치가 함께 확인됨"
    if INDIRECT_POLICY.search(combined) and re.search(r"배출권|할당대상업체|유상할당|탄소시장|국제감축실적|배출허용총량|상쇄배출권", summary, re.IGNORECASE):
        return "간접", 40, "배출권 수급·재정에 영향을 줄 수 있는 연관 법안"
    return None


def category_for(text: str) -> tuple[str, list[str]]:
    rules = (
        ("유상경매·재정", r"유상경매|경매수입|기후대응기금"),
        ("할당·감축", r"유상할당|무상할당|할당계획|배출허용총량|벤치마크"),
        ("시장안정·보호", r"시장안정|예비분|불공정거래|과징금|시세조종"),
        ("상쇄·국제감축", r"상쇄|외부사업|국제감축"),
        ("이행·정산", r"배출량\s*인증|배출권\s*제출|이월|차입"),
        ("통상·연계", r"탄소국경|CBAM|해외\s*ETS|국제\s*연계"),
        ("시장·거래", r"거래참여자|중개|위탁|선물|거래정보|배출권\s*거래"),
    )
    topics = [name for name, pattern in rules if re.search(pattern, text, re.IGNORECASE)]
    return (topics[0] if topics else "제도·거버넌스"), topics


def all_dates(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key, value in row.items():
        upper = str(key).upper()
        if upper.endswith("_DT") or "DATE" in upper:
            parsed = iso_date(value)
            if parsed:
                values.append(parsed)
    return values


def lifecycle_for(row: dict[str, Any]) -> dict[str, str]:
    fields = {
        "proposed": ("PPSL_DT", "PROPOSE_DT"),
        "committeeReceived": ("JRCMIT_RCEPT_DT",),
        "committeePresented": ("JRCMIT_PRSNT_DT",),
        "committeeCommented": ("JRCMIT_CMMT_DT",),
        "committeeProcessed": ("JRCMIT_PROC_DT",),
        "lawPresented": ("LAW_PRSNT_DT", "LWPR_PRSNT_DT"),
        "lawProcessed": ("LAW_PROC_DT", "LWPR_PROC_DT"),
        "plenaryPresented": ("RGS_PRSNT_DT", "PLNMT_DT"),
        "plenaryResolved": ("RGS_RSLN_DT", "RGS_CONF_DT"),
        "governmentTransferred": ("GVRN_TRSF_DT", "LWPR_GVRN_TRSF_DT"),
        "promulgated": ("PROM_DT", "ANOT_DT"),
    }
    return {name: iso_date(field(row, *source_names)) for name, source_names in fields.items()}


def status_for(row: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
    raw_stage = field(row, "PROC_STAGE_CD", "PROC_STAGE", "STAGE")
    committee_result = field(row, "JRCMIT_PROC_RSLT", "JRCMIT_PROC_RESULT")
    law_result = field(row, "LAW_PROC_RSLT", "LAW_PROC_RESULT_CD")
    plenary_result = field(row, "RGS_CONF_RSLT", "GENERAL_RESULT", "PROC_RESULT", "PROC_RSLT")
    raw_result = " | ".join(value for value in (committee_result, law_result, plenary_result) if value)
    combined = f"{raw_stage} {committee_result} {law_result} {plenary_result}"
    lifecycle = lifecycle_for(row)
    if re.search(r"공포", combined) or field(row, "PROM_LAW_NM", "PROM_DT", "ANOT_DT"):
        status = "공포"
    elif re.search(r"대안\s*반영", combined):
        status = "대안반영"
    elif re.search(r"철회", combined):
        status = "철회"
    elif re.search(r"부결", combined):
        status = "부결"
    elif re.search(r"폐기|임기만료", combined):
        status = "폐기"
    elif lifecycle["promulgated"] or re.search(r"공포", raw_stage):
        status = "공포"
    elif lifecycle["governmentTransferred"] or re.search(r"정부\s*이송", raw_stage):
        status = "정부이송"
    elif re.search(r"가결|본회의\s*통과", plenary_result):
        status = "본회의 통과"
    elif re.search(r"본회의", raw_stage) or lifecycle["plenaryPresented"] or lifecycle["plenaryResolved"]:
        status = "본회의"
    elif re.search(r"법사|체계.?자구", raw_stage) or lifecycle["lawPresented"] or lifecycle["lawProcessed"]:
        status = "법사위"
    elif re.search(r"위원회|소관위", raw_stage) or any(lifecycle[key] for key in ("committeeReceived", "committeePresented", "committeeCommented", "committeeProcessed")):
        status = "소관위"
    else:
        status = "접수"
    return status, raw_stage, raw_result, committee_result, law_result, plenary_result


def official_url(row: dict[str, Any], bill_id: str, previous_url: str = "") -> str:
    value = field(row, "LINK_URL", "DETAIL_LINK", "BILL_URL", "URL")
    if value.startswith("http://"):
        value = "https://" + value[7:]
    if value.startswith("https://") and urllib.parse.urlparse(value).hostname and urllib.parse.urlparse(value).hostname.endswith("assembly.go.kr"):
        return value
    if previous_url.startswith("https://") and urllib.parse.urlparse(previous_url).hostname and urllib.parse.urlparse(previous_url).hostname.endswith("assembly.go.kr"):
        return previous_url
    return f"https://likms.assembly.go.kr/bill/billDetail.do?billId={urllib.parse.quote(bill_id)}" if bill_id else "https://likms.assembly.go.kr/bill/main.do"


def fetch_summary(api_key: str, bill_no: str, bill_id: str) -> str:
    rows, _ = api_rows("BPMBILLSUMMARY", api_key, {"BILL_NO": bill_no, "pIndex": 1, "pSize": 10})
    matches = [row for row in rows if not bill_id or field(row, "BILL_ID") == bill_id]
    target = matches[0] if matches else (rows[0] if rows else {})
    return field(target, "SUMMARY", "BILL_SUMMARY")


def load_existing() -> dict[str, Any]:
    try:
        payload = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_document(items: list[dict], warnings: list[str]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "lastSync": now_iso(),
        "source": "대한민국 국회 열린국회정보 의안정보 통합 API V2",
        "assemblyTerm": ASSEMBLY_TERM,
        "warning": " | ".join(warnings) if warnings else None,
        "items": items,
    }
    temporary = OUTPUT_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT_PATH)


def build_item(
    row: dict[str, Any],
    summary: str,
    existing: dict[str, Any] | None,
    summary_synced: bool = False,
) -> dict[str, Any] | None:
    previous = existing or {}
    bill_id = field(row, "BILL_ID") or clean_text(previous.get("billId", ""))
    bill_no = field(row, "BILL_NO") or clean_text(previous.get("billNo", ""))
    title = field(row, "BILL_NM", "BILL_NAME") or clean_text(previous.get("title", ""))
    summary_value = clean_text(summary) or clean_text(previous.get("summary", ""))
    if not bill_id or not title:
        return None

    relevance = relevance_for(title, summary_value)
    if not relevance:
        return None
    relevance_level, relevance_score, relevance_reason = relevance

    calculated_status, new_stage, new_raw_result, new_committee_result, new_law_result, new_plenary_result = status_for(row)
    current_lifecycle = lifecycle_for(row)
    previous_lifecycle = previous.get("lifecycle", {}) if isinstance(previous.get("lifecycle"), dict) else {}
    lifecycle = {
        name: value or iso_date(previous_lifecycle.get(name, ""))
        for name, value in current_lifecycle.items()
    }
    has_status_update = bool(
        new_stage
        or new_raw_result
        or field(row, "PROM_LAW_NM")
        or any(value for name, value in current_lifecycle.items() if name != "proposed")
    )
    status = calculated_status if has_status_update else clean_text(previous.get("status", "")) or "접수"
    raw_stage = new_stage or clean_text(previous.get("rawStage", ""))
    raw_result = new_raw_result or clean_text(previous.get("rawResult", ""))
    committee_result = new_committee_result or clean_text(previous.get("committeeResult", ""))
    law_result = new_law_result or clean_text(previous.get("lawResult", ""))
    plenary_result = new_plenary_result or clean_text(previous.get("plenaryResult", ""))
    proposed_date = lifecycle["proposed"] or iso_date(previous.get("proposedDate", ""))
    previous_last_action = iso_date(previous.get("lastActionDate", ""))
    dates = [*all_dates(row), *lifecycle.values(), previous_last_action, proposed_date]
    last_action_date = max((value for value in dates if value), default="")
    proposer_kind = field(row, "PPSR_KND", "PROPOSER_KIND") or clean_text(previous.get("proposerKind", ""))
    proposer = field(row, "PPSR_NM", "PROPOSER", "RST_PROPOSER") or clean_text(previous.get("proposer", "")) or "제안자 확인 중"
    committee = field(row, "JRCMIT_NM", "COMMITTEE") or clean_text(previous.get("committee", "")) or "소관위 미정"
    url = official_url(row, bill_id, clean_text(previous.get("url", "")))
    primary_category, topics = category_for(f"{title} {summary_value}")
    timestamp = now_iso()
    canonical = {
        "title": title,
        "proposedDate": proposed_date,
        "proposerKind": proposer_kind,
        "proposer": proposer,
        "committee": committee,
        "status": status,
        "rawStage": raw_stage,
        "rawResult": raw_result,
        "committeeResult": committee_result,
        "lawResult": law_result,
        "plenaryResult": plenary_result,
        "lastActionDate": last_action_date,
        "lifecycle": lifecycle,
        "summary": summary_value,
        "url": url,
    }
    content_hash = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    history = list(previous.get("history", [])) if isinstance(previous.get("history"), list) else []
    if previous and previous.get("contentHash") and previous.get("contentHash") != content_hash:
        history.append({
            "changedAt": timestamp,
            "status": previous.get("status", ""),
            "committee": previous.get("committee", ""),
            "lastActionDate": previous.get("lastActionDate", ""),
        })
    return {
        "billId": bill_id,
        "billNo": bill_no,
        "assemblyTerm": ASSEMBLY_TERM,
        "title": title,
        "proposedDate": proposed_date,
        "proposerKind": proposer_kind,
        "proposer": proposer,
        "committee": committee,
        "status": status,
        "rawStage": raw_stage,
        "rawResult": raw_result,
        "committeeResult": committee_result,
        "lawResult": law_result,
        "plenaryResult": plenary_result,
        "lifecycle": lifecycle,
        "lastActionDate": last_action_date,
        "summary": summary_value,
        "summarySyncedAt": timestamp if summary_synced else previous.get("summarySyncedAt", ""),
        "primaryCategory": primary_category,
        "topics": topics,
        "relevanceLevel": relevance_level,
        "relevanceScore": relevance_score,
        "relevanceReason": relevance_reason,
        "url": url,
        "firstSeenAt": previous.get("firstSeenAt") or timestamp,
        "lastSeenAt": timestamp,
        "lastChangedAt": timestamp if not previous or previous.get("contentHash") != content_hash else previous.get("lastChangedAt", timestamp),
        "contentHash": content_hash,
        "history": history[-12:],
    }


def main() -> int:
    api_key = os.getenv("ASSEMBLY_API_KEY", "").strip()
    existing_document = load_existing()
    existing_items = [
        item for item in existing_document.get("items", [])
        if isinstance(item, dict) and str(item.get("assemblyTerm") or ASSEMBLY_TERM) == ASSEMBLY_TERM
    ]
    existing_by_id: dict[str, dict[str, Any]] = {}
    for item in existing_items:
        key = clean_text(item.get("billId", ""))
        if not key and clean_text(item.get("billNo", "")):
            key = f"{item.get('assemblyTerm') or ASSEMBLY_TERM}:{item.get('billNo')}"
        if key:
            existing_by_id[key] = item
    if not api_key:
        message = "ASSEMBLY_API_KEY가 없어 국회법안 수집을 건너뜁니다."
        print(message, file=sys.stderr)
        if not OUTPUT_PATH.exists():
            save_document(existing_items, [message])
        return 0

    warnings: list[str] = []
    candidates: dict[str, dict] = {}
    successful_queries = 0
    for keyword in TITLE_QUERIES:
        try:
            rows = fetch_all_rows("ALLBILLV2", api_key, {"ERACO": ASSEMBLY_TERM, "BILL_NM": keyword})
            successful_queries += 1
            for row in rows:
                if row_key(row) and title_candidate(row):
                    candidates[row_key(row)] = row
        except Exception as exc:
            warnings.append(f"{keyword} 검색 실패: {exc}")

    if successful_queries == 0:
        print("국회 API 검색이 모두 실패했습니다. 기존 bills.json은 보존합니다.", file=sys.stderr)
        return 1
    if successful_queries < len(TITLE_QUERIES):
        try:
            for row in fetch_all_rows("ALLBILLV2", api_key, {"ERACO": ASSEMBLY_TERM}):
                if row_key(row) and title_candidate(row):
                    candidates[row_key(row)] = row
        except Exception as exc:
            warnings.append(f"전체 의안 보완조회 실패: {exc}")

    if not candidates:
        print("국회 API에서 배출권 관련 후보 의안을 찾지 못했습니다. 기존 bills.json은 보존합니다.", file=sys.stderr)
        return 1

    summaries: dict[str, str] = {}
    summary_success: set[str] = set()
    pending: dict[Any, str] = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        for key, row in candidates.items():
            previous_summary = clean_text(existing_by_id.get(key, {}).get("summary", ""))
            summaries[key] = previous_summary
            bill_no = field(row, "BILL_NO")
            if bill_no:
                pending[executor.submit(fetch_summary, api_key, bill_no, field(row, "BILL_ID"))] = key
        for future in as_completed(pending):
            key = pending[future]
            try:
                refreshed = clean_text(future.result())
                if refreshed:
                    summaries[key] = refreshed
                summary_success.add(key)
            except Exception as exc:
                warnings.append(f"{field(candidates[key], 'BILL_NO')} 주요내용 조회 실패: {exc}")

    updated: dict[str, dict] = dict(existing_by_id)
    for key, row in candidates.items():
        item = build_item(row, summaries.get(key, ""), existing_by_id.get(key), key in summary_success)
        if item:
            updated[key] = item
        elif key in summary_success:
            updated.pop(key, None)

    items = sorted(
        updated.values(),
        key=lambda item: (
            str(item.get("lastActionDate") or item.get("proposedDate") or ""),
            int(item.get("relevanceScore") or 0),
            str(item.get("billNo") or ""),
        ),
        reverse=True,
    )[:120]
    save_document(items, warnings[:20])
    print(f"국회 배출권 관련 법안 {len(items)}건 저장")
    if warnings:
        print("일부 국회 API 경고: " + " | ".join(warnings[:5]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
