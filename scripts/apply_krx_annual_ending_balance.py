#!/usr/bin/env python3
"""Add the final year-end balance estimate to public/data/krx-annual.json.

Run after ``apply_krx_annual_procurement_estimate.py``. The calculation uses
the paid-allocation-adjusted free allocation estimate, prior-year carryover
and borrowing, and current-year allocation adjustments and verified emissions.
Current-year carryover and borrowing are excluded because they are settlement
outcomes of the year-end position.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "public" / "data" / "krx-annual.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args()


def number_or_none(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return value
    if isinstance(value, str):
        text = value.replace(",", "").strip()
        if not text or text in {"—", "-"}:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


def metric(row: dict[str, Any], key: str) -> int | float | None:
    metrics = row.get("metrics")
    if isinstance(metrics, dict) and key in metrics:
        return number_or_none(metrics.get(key))
    return number_or_none(row.get(key))


def row_year(row: dict[str, Any]) -> int | None:
    try:
        return int(row.get("year", row.get("complianceYear")))
    except (TypeError, ValueError):
        return None


def update_missing(
    row: dict[str, Any],
    key: str,
    missing: bool,
    reason: str,
) -> None:
    current = row.get("missing")
    if isinstance(current, list):
        values = [item for item in current if item != key]
        if missing:
            values.append(key)
        row["missing"] = values
    else:
        values = dict(current) if isinstance(current, dict) else {}
        values[key] = missing
        row["missing"] = values

    reasons = (
        dict(row.get("missingReason"))
        if isinstance(row.get("missingReason"), dict)
        else {}
    )
    if missing:
        reasons[key] = reason
    else:
        reasons.pop(key, None)
    row["missingReason"] = reasons


def apply(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError("krx-annual.json rows must be an array")

    for row in rows:
        if not isinstance(row, dict):
            continue
        year = row_year(row)
        free_allocation = metric(row, "estimatedFreeAllocation")
        if free_allocation is None:
            free_allocation = metric(row, "adjustedAllocation")
        previous_carryover = metric(row, "previousCarryover")
        previous_borrow = metric(row, "previousBorrow")
        if year == 2015 and previous_carryover is None:
            previous_carryover = 0
        if year == 2015 and previous_borrow is None:
            previous_borrow = 0
        additional_allocation = metric(row, "additionalAllocation")
        cancellation = metric(row, "cancellation")
        verified_emissions = metric(row, "verifiedEmissions")

        inputs = (
            free_allocation,
            previous_carryover,
            previous_borrow,
            additional_allocation,
            cancellation,
            verified_emissions,
        )
        ending_balance = (
            free_allocation
            + previous_carryover
            - previous_borrow
            + additional_allocation
            - cancellation
            - verified_emissions
            if all(value is not None for value in inputs)
            else None
        )

        metrics = (
            dict(row.get("metrics"))
            if isinstance(row.get("metrics"), dict)
            else {}
        )
        metrics["endingBalance"] = ending_balance
        row["metrics"] = metrics
        row["endingBalanceEstimate"] = {
            "estimatedFreeAllocation": free_allocation,
            "previousCarryover": previous_carryover,
            "previousBorrow": previous_borrow,
            "additionalAllocation": additional_allocation,
            "cancellation": cancellation,
            "verifiedEmissions": verified_emissions,
            "endingBalance": ending_balance,
        }
        if isinstance(row.get("procurementEstimate"), dict):
            row["procurementEstimate"] = {
                **row["procurementEstimate"],
                "endingBalance": ending_balance,
            }
        update_missing(
            row,
            "endingBalance",
            ending_balance is None,
            "dependency:annual-ending-balance",
        )

    fields = payload.setdefault("metrics", {}).setdefault("fields", {})
    fields["endingBalance"] = {
        "label": "최종 과부족량(종료)",
        "formula": (
            "estimatedFreeAllocation + previousCarryover - previousBorrow "
            "+ additionalAllocation - cancellation - verifiedEmissions"
        ),
        "formulaKo": (
            "무상 사전할당 추정량 + 전년도 이월량 - 전년도 차입량 "
            "+ 당해년도 추가할당량 - 당해년도 할당취소량 "
            "- 당해년도 인증배출량"
        ),
        "nullRule": "필수 입력 중 하나라도 확인되지 않으면 null",
    }
    payload["endingBalanceEstimate"] = {
        "version": "1.0.0",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "label": "최종 과부족량(종료)",
        "formula": (
            "estimatedFreeAllocation + previousCarryover - previousBorrow "
            "+ additionalAllocation - cancellation - verifiedEmissions"
        ),
        "formulaKo": (
            "무상 사전할당 추정량 + 전년도 이월량 - 전년도 차입량 "
            "+ 당해년도 추가할당량 - 당해년도 할당취소량 "
            "- 당해년도 인증배출량"
        ),
        "excluded": [
            "currentYearCarryover",
            "currentYearBorrow",
            "offsetIssued",
            "auctionAwards",
            "exchangeAndOtcTrades",
            "otherHoldings",
        ],
        "disclaimer": (
            "당해년도 이월·차입은 종료 과부족 처리 결과이고 업체별 실제 "
            "매입·보유량은 공개 원자료에 없어 제외한 추정치"
        ),
    }
    return payload


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()
    output_path = (args.output or args.input).resolve()
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    transformed = apply(payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if args.pretty:
        text = json.dumps(transformed, ensure_ascii=False, indent=2) + "\n"
    else:
        text = json.dumps(
            transformed,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    output_path.write_text(text, encoding="utf-8")
    print(f"Applied annual ending balance estimate to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
