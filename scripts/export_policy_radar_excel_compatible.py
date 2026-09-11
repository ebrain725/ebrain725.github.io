#!/usr/bin/env python3
"""Build a desktop-Excel-compatible policy-radar workbook from an artifact_tool template."""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import tempfile
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import export_policy_radar_excel as legacy

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_B64 = ROOT / "scripts/policy_excel_template.b64"
OUTPUT = ROOT / "public/data/ets-policy-radar-events.xlsx"
FIXED_OUTPUT = ROOT / "public/data/ets-policy-radar-events-fixed.xlsx"
MANIFEST = ROOT / "public/data/policy-radar-excel.json"
KST = timezone(timedelta(hours=9))
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DATE_RE = re.compile(r"^(20\d{2})-(\d{2})-(\d{2})$")

EXPECTED_SHEETS = [
    "안내", "전체 이벤트", "기후부 보도자료", "기후부 공지사항",
    "산업부 보도자료", "산업부 공지사항", "한국거래소 공지사항",
    "뉴스", "기관일정", "발의법률안", "국회 세미나",
]
STYLE = {
    "header": 14, "text": 38, "text_alt": 59, "date": 72,
    "date_alt": 74, "num": 76, "num_alt": 77, "url": 80, "url_alt": 82,
}


def safe_text(value: Any, limit: int = 32767) -> str:
    if value is None:
        raw = ""
    elif isinstance(value, list):
        raw = ", ".join(dict.fromkeys(safe_text(item, 500) for item in value if safe_text(item, 500)))
    elif isinstance(value, dict):
        raw = json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    else:
        raw = str(value)
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    output: list[str] = []
    units = 0
    for char in raw:
        code = ord(char)
        valid = code in (9, 10, 13) or 0x20 <= code <= 0xD7FF or 0xE000 <= code <= 0xFFFD or 0x10000 <= code <= 0x10FFFF
        if not valid:
            continue
        cost = 2 if code > 0xFFFF else 1
        if units + cost > limit:
            break
        output.append(char)
        units += cost
    return "".join(output)


legacy.text = safe_text


def column_name(index: int) -> str:
    result = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def date_serial(value: Any) -> int | None:
    match = DATE_RE.match(safe_text(value)[:10])
    if not match:
        return None
    try:
        return (date(*map(int, match.groups())) - date(1899, 12, 30)).days
    except ValueError:
        return None


def value_kind(header: str) -> str:
    if "URL" in header:
        return "url"
    if header in legacy.DATE_HEADERS:
        return "date"
    if "건수" in header or "점수" in header:
        return "num"
    return "text"


def style_id(kind: str, alternate: bool) -> int:
    return STYLE[kind + ("_alt" if alternate else "")]


def cell_xml(reference: str, value: Any, kind: str, style: int) -> str:
    if value in (None, ""):
        return f'<c r="{reference}" s="{style}"/>'
    if kind == "date":
        serial = date_serial(value)
        if serial is not None:
            return f'<c r="{reference}" s="{style}" t="n"><v>{serial}</v></c>'
    if kind == "num" and isinstance(value, (int, float)) and math.isfinite(float(value)):
        return f'<c r="{reference}" s="{style}" t="n"><v>{float(value):.15g}</v></c>'
    text = safe_text(value)
    preserve = ' xml:space="preserve"' if text[:1].isspace() or text[-1:].isspace() or "\n" in text else ""
    return f'<c r="{reference}" s="{style}" t="inlineStr"><is><t{preserve}>{escape(text)}</t></is></c>'


def worksheet_xml(headers: list[str], rows: list[list[Any]], widths: list[float]) -> str:
    kinds = [value_kind(header) for header in headers]
    xml_rows = [
        '<row r="1" ht="24" customHeight="1">'
        + "".join(cell_xml(f"{column_name(index)}1", header, "text", STYLE["header"]) for index, header in enumerate(headers))
        + "</row>"
    ]
    for row_number, values in enumerate(rows, 2):
        alternate = row_number % 2 == 1
        cells = []
        for column_index, kind in enumerate(kinds):
            value = values[column_index] if column_index < len(values) else ""
            cells.append(cell_xml(f"{column_name(column_index)}{row_number}", value, kind, style_id(kind, alternate)))
        xml_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    last = f"{column_name(len(headers) - 1)}{max(1, len(rows) + 1)}"
    columns = "".join(
        f'<col min="{index + 1}" max="{index + 1}" width="{min(max(float(width), 3), 120):.2f}" customWidth="1"/>'
        for index, width in enumerate(widths)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{NS}"><dimension ref="A1:{last}"/>'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        '<selection pane="bottomLeft" activeCell="A2" sqref="A2"/></sheetView></sheetViews>'
        f'<sheetFormatPr defaultRowHeight="18"/><cols>{columns}</cols><sheetData>{"".join(xml_rows)}</sheetData>'
        f'<autoFilter ref="A1:{last}"/><pageMargins left="0.25" right="0.25" top="0.5" bottom="0.5" header="0.2" footer="0.2"/>'
        '</worksheet>'
    )


def build_specs(policy: dict[str, Any], bills: dict[str, Any], seminars: dict[str, Any]) -> tuple[list[tuple[str, list[str], list[list[Any]], list[float]]], dict[str, int]]:
    grouped = {tab: [] for tab in legacy.TAB_ORDER}
    for item in policy.get("items", []):
        if isinstance(item, dict):
            grouped[legacy.policy_tab(item)].append(item)
    for values in grouped.values():
        values.sort(key=lambda item: (safe_text(legacy.first(item, "publishedAt", "date")), safe_text(item.get("title"))), reverse=True)

    schedules = [item for item in policy.get("institutionSchedules", []) if isinstance(item, dict)]
    schedules.sort(key=lambda item: (safe_text(legacy.first(item, "startDate", "date")), safe_text(item.get("startTime")), safe_text(item.get("title"))), reverse=True)
    bill_items = [item for item in bills.get("items", []) if isinstance(item, dict)]
    bill_items.sort(key=lambda item: (safe_text(item.get("proposedDate")), safe_text(item.get("title"))), reverse=True)
    seminar_items = [item for item in seminars.get("items", []) if isinstance(item, dict)]
    seminar_items.sort(key=lambda item: (safe_text(item.get("startDate")), safe_text(item.get("startTime")), safe_text(item.get("title"))), reverse=True)

    ph = ["게시일", "분류", "세부 게시판", "직접·연관", "제목", "요약", "일치 주제·키워드", "출처", "중복 건수", "원문 URL", "ID"]
    sh = ["행사 시작일", "행사 종료일", "시간", "상태", "행사 유형", "제목", "주최기관", "장소", "근거·요약", "자료 게시일", "출처", "중복 건수", "원문 URL", "ID"]
    bh = ["의안번호", "제안일", "최근 처리일", "현재 단계", "제목", "발의자", "발의자 구분", "소관위원회", "주요 분류", "관련도", "관련도 점수", "관련 사유", "제안이유·주요내용", "처리결과", "원문 URL", "의안 ID"]
    mh = ["행사 시작일", "행사 종료일", "시간", "상태", "행사 유형", "제목", "주최", "장소", "게시·최초수집일", "검색 키워드", "관련도", "관련 사유", "요약", "출처", "원문 URL", "ID"]
    uh = ["탭", "기준일", "시작일", "종료일", "시간", "상태", "분류", "제목", "요약·근거", "기관·주최·발의자", "소관·장소", "키워드", "출처", "원문 URL", "ID"]

    sheets = []
    counts: dict[str, int] = {}
    all_rows: list[list[Any]] = []
    for tab in legacy.TAB_ORDER:
        rows = [legacy.policy_row(item) for item in grouped[tab]]
        counts[tab] = len(rows)
        sheets.append((tab, ph, rows, [12, 24, 20, 12, 44, 80, 42, 24, 12, 52, 28]))
        all_rows.extend(legacy.unified(tab, item, "policy") for item in grouped[tab])
    counts["기관일정"] = len(schedules)
    sheets.append(("기관일정", sh, [legacy.schedule_row(item) for item in schedules], [12, 12, 10, 12, 14, 44, 28, 28, 80, 12, 24, 12, 52, 28]))
    all_rows.extend(legacy.unified("기관일정", item, "schedule") for item in schedules)
    counts["발의법률안"] = len(bill_items)
    sheets.append(("발의법률안", bh, [legacy.bill_row(item) for item in bill_items], [14, 12, 12, 12, 48, 26, 14, 24, 18, 12, 12, 32, 80, 28, 52, 28]))
    all_rows.extend(legacy.unified("발의법률안", item, "bill") for item in bill_items)
    counts["국회의원 세미나 일정"] = len(seminar_items)
    sheets.append(("국회 세미나", mh, [legacy.seminar_row(item) for item in seminar_items], [12, 12, 10, 12, 14, 46, 32, 28, 14, 30, 12, 34, 70, 26, 52, 26]))
    all_rows.extend(legacy.unified("국회의원 세미나 일정", item, "schedule") for item in seminar_items)

    all_rows.sort(key=lambda row: (safe_text(row[2] or row[1]), safe_text(row[4]), safe_text(row[7])), reverse=True)
    counts["전체 이벤트"] = len(all_rows)
    generated = datetime.now(KST).isoformat(timespec="seconds")
    info = [["파일명", OUTPUT.name], ["생성시각(KST)", generated], ["설명", "정책 레이더의 모든 탭을 시트별로 정리한 엑셀 파일"], ["전체 이벤트 건수", len(all_rows)], ["호환성", "artifact_tool 템플릿 기반 OOXML"]]
    info.extend([[f"{key} 건수", value] for key, value in counts.items() if key != "전체 이벤트"])
    return [
        ("안내", ["항목", "내용"], info, [28, 90]),
        ("전체 이벤트", uh, all_rows, [22, 12, 12, 12, 10, 12, 24, 46, 80, 30, 30, 42, 24, 52, 28]),
        *sheets,
    ], counts


def template_bytes(path: Path) -> bytes:
    return base64.b64decode("".join(path.read_text(encoding="ascii").split()))


def write_workbook(specs: list[tuple[str, list[str], list[list[Any]], list[float]]], output: Path, template_path: Path) -> None:
    if [name for name, *_ in specs] != EXPECTED_SHEETS:
        raise RuntimeError("시트 순서가 템플릿과 일치하지 않습니다.")
    raw = template_bytes(template_path)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as handle:
        source_path = Path(handle.name)
        handle.write(raw)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, dir=output.parent, suffix=".xlsx") as handle:
        target_path = Path(handle.name)
    try:
        with zipfile.ZipFile(source_path) as source:
            root = ET.fromstring(source.read("xl/workbook.xml"))
            names = [node.attrib.get("name", "") for node in root.findall(f".//{{{NS}}}sheet")]
            if names != EXPECTED_SHEETS:
                raise RuntimeError(f"템플릿 시트 오류: {names}")
            replacements = {f"xl/worksheets/sheet{index}.xml" for index in range(1, len(specs) + 1)}
            with zipfile.ZipFile(target_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as target:
                for info in source.infolist():
                    if info.filename not in replacements:
                        target.writestr(info, source.read(info.filename))
                for index, (_, headers, rows, widths) in enumerate(specs, 1):
                    target.writestr(f"xl/worksheets/sheet{index}.xml", worksheet_xml(headers, rows, widths).encode("utf-8"))
        os.replace(target_path, output)
        output.chmod(0o644)
    finally:
        source_path.unlink(missing_ok=True)
        target_path.unlink(missing_ok=True)


def validate(path: Path, expected_rows: int) -> None:
    if not path.is_file() or path.stat().st_size < 10_000:
        raise RuntimeError("XLSX 파일이 없거나 비정상적으로 작습니다.")
    with zipfile.ZipFile(path) as archive:
        if archive.testzip():
            raise RuntimeError("XLSX ZIP 무결성 오류")
        for name in archive.namelist():
            if name.endswith((".xml", ".rels")):
                ET.fromstring(archive.read(name))
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        if len(workbook.findall(f".//{{{NS}}}sheet")) != 11:
            raise RuntimeError("XLSX 시트 수 오류")
        sheet2 = ET.fromstring(archive.read("xl/worksheets/sheet2.xml"))
        rows = len(sheet2.findall(f".//{{{NS}}}row")) - 1
        if rows != expected_rows:
            raise RuntimeError(f"전체 이벤트 행 수 오류: {rows} != {expected_rows}")


def generate(policy_path: Path, bills_path: Path, seminars_path: Path, output: Path, fixed_output: Path, manifest_path: Path, template_path: Path) -> dict[str, Any]:
    policy = legacy.load(policy_path)
    bills = legacy.load(bills_path)
    seminars = legacy.load(seminars_path)
    specs, counts = build_specs(policy, bills, seminars)
    write_workbook(specs, output, template_path)
    validate(output, counts["전체 이벤트"])
    fixed_output.write_bytes(output.read_bytes())
    validate(fixed_output, counts["전체 이벤트"])
    result = {
        "schemaVersion": "2.0",
        "generatedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "file": f"data/{output.name}",
        "fixedFile": f"data/{fixed_output.name}",
        "fileName": output.name,
        "fileBytes": output.stat().st_size,
        "sheetCount": len(specs),
        "totalEventCount": counts["전체 이벤트"],
        "counts": counts,
        "generator": "artifact_tool-template-v2",
        "sourceLastSync": {
            "정책자료": safe_text(policy.get("lastSync")),
            "발의법률안": safe_text(bills.get("lastSync")),
            "국회 세미나": safe_text(seminars.get("lastSync")),
        },
    }
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def self_test(template_path: Path) -> None:
    policy = {"items": [{"section": "press", "publishedAt": "2026-09-09", "title": "배출권 & 시험", "summary": "특수문자 <>&\n두 줄", "source": "기후부 보도자료", "url": "https://example.com/?a=1&b=2"}], "institutionSchedules": [{"startDate": "2026-09-10", "title": "일정"}]}
    bills = {"items": [{"billId": "b", "proposedDate": "2026-01-01", "title": "법안"}]}
    seminars = {"items": [{"id": "s", "startDate": "2026-09-11", "title": "세미나"}]}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name, document in (("p.json", policy), ("b.json", bills), ("s.json", seminars)):
            (root / name).write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        result = generate(root / "p.json", root / "b.json", root / "s.json", root / "a.xlsx", root / "fixed.xlsx", root / "m.json", template_path)
        assert result["sheetCount"] == 11 and result["totalEventCount"] == 4
    print("POLICY_RADAR_COMPATIBLE_EXCEL_SELF_TEST=PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy-path", type=Path, default=legacy.POLICY)
    parser.add_argument("--bill-path", type=Path, default=legacy.BILLS)
    parser.add_argument("--seminar-path", type=Path, default=legacy.SEMINARS)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--fixed-output", type=Path, default=FIXED_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--template-b64", type=Path, default=TEMPLATE_B64)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test(args.template_b64)
        return 0
    result = generate(args.policy_path, args.bill_path, args.seminar_path, args.output, args.fixed_output, args.manifest, args.template_b64)
    print("POLICY_RADAR_COMPATIBLE_EXCEL_RESULT=" + json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
