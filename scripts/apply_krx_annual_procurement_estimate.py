#!/usr/bin/env python3
"""Apply the paid-allocation-adjusted annual-start procurement estimate.

This is a deployment post-processor for ``public/data/krx-annual.json``. It
preserves every source metric, adds the paid/free allocation estimates and
replaces ``metrics.finalBalance`` with the dashboard's conservative annual-
start procurement position.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
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


def company_id(row: dict[str, Any]) -> str:
    return str(row.get("companyId", row.get("entityId", ""))).strip()


def normalized_allocation_type(row: dict[str, Any]) -> str | None:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    value = row.get(
        "allocationType",
        row.get("paidAllocation", metrics.get("allocationType")),
    )
    text = str(value or "").lower().replace(" ", "")
    if text in {"y", "yes", "paid", "auction", "유상", "유상할당"}:
        return "paid"
    if text in {"n", "no", "free", "무상", "무상할당"}:
        return "free"
    if text in {"mixed", "혼합", "유상/무상", "유상·무상"}:
        return "mixed"
    return None


def paid_rate(row: dict[str, Any]) -> float | None:
    year = row_year(row)
    pre_allocation = metric(row, "preAllocation")
    if year is None or not 2015 <= year <= 2025:
        return None
    if year <= 2017:
        return 0.0
    allocation_type = normalized_allocation_type(row)
    if allocation_type == "free":
        return 0.0
    if allocation_type == "paid":
        return 0.03 if year <= 2020 else 0.10
    if pre_allocation == 0:
        return 0.0
    return None


def round_tonnes(value: float) -> int:
    # Source quantities are non-negative; match JavaScript Math.round.
    return int(math.floor(value + 0.5))


def strict_sum(rows: list[dict[str, Any]], key: str) -> int | float | None:
    if not rows:
        return None
    values = [metric(row, key) for row in rows]
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


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

    by_company_year: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            continue
        year = row_year(row)
        entity_id = company_id(row)
        if year is not None and entity_id:
            by_company_year[(entity_id, year)].append(row)

    for row in rows:
        if not isinstance(row, dict):
            continue
        year = row_year(row)
        entity_id = company_id(row)
        pre_allocation = metric(row, "preAllocation")
        rate = paid_rate(row)
        paid_estimate = (
            round_tonnes(float(pre_allocation) * rate)
            if pre_allocation is not None and rate is not None
            else None
        )
        free_estimate = (
            pre_allocation - paid_estimate
            if pre_allocation is not None and paid_estimate is not None
            else None
        )
        previous_rows = (
            by_company_year.get((entity_id, year - 1), [])
            if year is not None and entity_id
            else []
        )
        previous_carryover = strict_sum(previous_rows, "carryover")
        previous_borrow = strict_sum(previous_rows, "borrow")
        previous_emissions = strict_sum(previous_rows, "verifiedEmissions")
        inputs = (
            free_estimate,
            previous_carryover,
            previous_borrow,
            previous_emissions,
        )
        final_balance = (
            free_estimate
            + previous_carryover
            - previous_borrow
            - previous_emissions
            if all(value is not None for value in inputs)
            else None
        )

        metrics = (
            dict(row.get("metrics"))
            if isinstance(row.get("metrics"), dict)
            else {}
        )
        if "sourceAdjustedAllocation" not in metrics:
            metrics["sourceAdjustedAllocation"] = metric(row, "adjustedAllocation")
        if "sourceFinalBalance" not in metrics:
            metrics["sourceFinalBalance"] = metric(row, "finalBalance")
        metrics["paidAllocationRate"] = rate
        metrics["estimatedPaidAllocation"] = paid_estimate
        metrics["estimatedFreeAllocation"] = free_estimate
        metrics["adjustedAllocation"] = free_estimate
        metrics["previousCarryover"] = previous_carryover
        metrics["previousBorrow"] = previous_borrow
        metrics["previousVerifiedEmissions"] = previous_emissions
        metrics["finalBalance"] = final_balance
        if "complianceBalance" in metrics:
            metrics["complianceBalance"] = final_balance
        row["metrics"] = metrics
        row["procurementEstimate"] = {
            "method": "gross-preallocation-less-paid-share",
            "paidAllocationRate": rate,
            "estimatedPaidAllocation": paid_estimate,
            "estimatedFreeAllocation": free_estimate,
            "previousCarryover": previous_carryover,
            "previousBorrow": previous_borrow,
            "previousVerifiedEmissions": previous_emissions,
            "finalBalance": final_balance,
        }
        update_missing(
            row,
            "adjustedAllocation",
            free_estimate is None,
            "dependency:allocation-method",
        )
        update_missing(
            row,
            "finalBalance",
            final_balance is None,
            "dependency:annual-start-procurement-position",
        )

    fields = payload.setdefault("metrics", {}).setdefault("fields", {})
    fields["adjustedAllocation"] = {
        "label": "무상 사전할당 추정",
        "formula": "preAllocation - estimatedPaidAllocation",
        "formulaKo": "총 사전할당량 - 유상분 추정량",
        "nullRule": "유상할당 방식 또는 총 사전할당량이 확인되지 않으면 null",
    }
    fields["estimatedPaidAllocation"] = {
        "label": "유상분 추정",
        "formula": "preAllocation * paidAllocationRate",
        "formulaKo": "총 사전할당량 × 계획기간 유상할당률",
        "nullRule": "유상할당 방식 또는 총 사전할당량이 확인되지 않으면 null",
    }
    fields["estimatedFreeAllocation"] = {
        "label": "무상 사전할당 추정",
        "formula": "preAllocation - estimatedPaidAllocation",
        "formulaKo": "총 사전할당량 - 유상분 추정량",
        "nullRule": "유상할당 방식 또는 총 사전할당량이 확인되지 않으면 null",
    }
    fields["finalBalance"] = {
        "label": "연초 추정 과부족량",
        "formula": (
            "estimatedFreeAllocation + previousCarryover - previousBorrow "
            "- previousVerifiedEmissions"
        ),
        "formulaKo": (
            "무상 사전할당 추정량 + 전년도 이월량 - 전년도 차입량 "
            "- 전년도 인증배출량"
        ),
        "nullRule": "필수 입력 중 하나라도 확인되지 않으면 null",
    }
    payload["procurementEstimate"] = {
        "version": "5.0.0",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "label": "연초 추정 과부족량",
        "purpose": "유상할당 차감 후 연초 기준 조달 필요량을 보수적으로 추정",
        "paidAllocationRates": {
            "2015-2017": {"paidSubject": 0, "freeSubject": 0},
            "2018-2020": {"paidSubject": 0.03, "freeSubject": 0},
            "2021-2025": {"paidSubject": 0.10, "freeSubject": 0},
        },
        "balanceFormula": (
            "estimatedFreeAllocation + previousCarryover - previousBorrow "
            "- previousVerifiedEmissions"
        ),
        "excluded": [
            "auctionAwards",
            "exchangePurchasesAndSales",
            "otcPurchasesAndSales",
            "otherHoldings",
            "currentYearAdditionalAllocation",
            "currentYearCancellation",
        ],
        "disclaimer": (
            "공개 원자료만 사용한 조달판단용 추정치이며 공식 보유잔액 "
            "또는 최종 이행부족량이 아님"
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
    print(f"Applied annual procurement estimate to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
