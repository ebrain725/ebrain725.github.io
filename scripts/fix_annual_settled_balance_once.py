#!/usr/bin/env python3
"""Correct the annual settled-balance formula to exclude same-year carryover and borrowing."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "public" / "assets" / "krx-annual-balance-v2.js"
HTML_PATH = ROOT / "public" / "krx-annual.html"
META_PATH = ROOT / "public" / "data" / "krx-annual-settled-balance.json"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def update_javascript() -> None:
    text = JS_PATH.read_text(encoding="utf-8")

    old_function = '''  function settledBalanceForRow(row) {
    const adjustedAllocation = metricValue(row, "adjustedAllocation");
    const borrow = metricValue(row, "borrow");
    const verifiedEmissions = metricValue(row, "verifiedEmissions");
    const carryover = metricValue(row, "carryover");
    const inputs = [adjustedAllocation, borrow, verifiedEmissions, carryover];
    if (!inputs.every((value) => typeof value === "number")) return null;
    return adjustedAllocation + borrow - verifiedEmissions - carryover;
  }
'''
    new_function = '''  function settledBalanceForRow(row) {
    const adjustedAllocation = metricValue(row, "adjustedAllocation");
    const verifiedEmissions = metricValue(row, "verifiedEmissions");
    const inputs = [adjustedAllocation, verifiedEmissions];
    if (!inputs.every((value) => typeof value === "number")) return null;

    // 해당 연도의 이월량과 차입량은 과부족을 처리한 결과이므로
    // 원자료 기준 정산 과부족량 산식에서 제외한다.
    return adjustedAllocation - verifiedEmissions;
  }
'''
    text = replace_once(text, old_function, new_function, "settled balance function")

    old_formula = "adjustedAllocation + borrow - verifiedEmissions - carryover"
    new_formula = "adjustedAllocation - verifiedEmissions"
    formula_count = text.count(old_formula)
    if formula_count < 1:
        raise SystemExit("settled balance formula metadata was not found")
    text = text.replace(old_formula, new_formula)

    old_formula_ko = (
        "해당 연도 조정할당량 + 해당 연도 차입량 - "
        "해당 연도 인증배출량 - 해당 연도 이월량"
    )
    new_formula_ko = "해당 연도 조정할당량 - 해당 연도 인증배출량"
    text = replace_once(text, old_formula_ko, new_formula_ko, "Korean formula metadata")

    if 'version: "4.1.0"' in text:
        text = replace_once(text, 'version: "4.1.0"', 'version: "4.2.0"', "calculation version")

    JS_PATH.write_text(text, encoding="utf-8")


def update_html() -> None:
    text = HTML_PATH.read_text(encoding="utf-8")
    text = text.replace("20260902-settled-balance-v1", "20260902-settled-balance-v2")

    old_tooltip = (
        "해당 연도의 조정할당량 + 차입량 − 인증배출량 − 이월량으로 "
        "계산한 원자료 기준 정산값입니다."
    )
    new_tooltip = (
        "해당 연도의 조정할당량 − 인증배출량으로 계산한 원자료 기준 과부족입니다. "
        "해당 연도 이월량과 차입량은 정산 처리 결과이므로 제외합니다."
    )
    text = replace_once(text, old_tooltip, new_tooltip, "settled balance tooltip")
    HTML_PATH.write_text(text, encoding="utf-8")


def update_metadata() -> None:
    payload = {
        "schemaVersion": "1.1.0",
        "label": "정산 과부족량",
        "displayPosition": "업체현황(연간) 마지막 열",
        "formula": "adjustedAllocation - verifiedEmissions",
        "formulaKo": "해당 연도 조정할당량 - 해당 연도 인증배출량",
        "expandedFormulaKo": (
            "해당 연도 사전할당량 + 해당 연도 추가할당량 - "
            "해당 연도 할당취소량 - 해당 연도 인증배출량"
        ),
        "excludedMetrics": [
            {
                "metric": "borrow",
                "label": "해당 연도 차입량",
                "reason": "부족분을 다음 연도 배출권으로 보전한 정산 처리 결과",
            },
            {
                "metric": "carryover",
                "label": "해당 연도 이월량",
                "reason": "잉여분을 다음 연도로 넘긴 정산 처리 결과",
            },
        ],
        "sourceData": "data/krx-annual.json",
        "unit": "tCO2eq",
        "nullRule": "조정할당량 또는 인증배출량이 없으면 null",
    }
    META_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    update_javascript()
    update_html()
    update_metadata()


if __name__ == "__main__":
    main()
