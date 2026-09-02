#!/usr/bin/env python3
"""One-time repository patch for the annual settled-balance dashboard column."""

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def update_javascript() -> None:
    path = Path("public/assets/krx-annual-balance-v2.js")
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        '    finalBalance: ["finalBalance", "complianceBalance"],\n  };',
        '    finalBalance: ["finalBalance", "complianceBalance"],\n'
        '    settledBalance: ["settledBalance"],\n  };',
        "metric alias",
    )
    text = replace_once(
        text,
        '    "previousVerifiedEmissions",\n    "finalBalance",\n  ];',
        '    "previousVerifiedEmissions",\n    "finalBalance",\n'
        '    "settledBalance",\n  ];',
        "display metric",
    )

    marker = "  function previousBasisForRow(row, rowsByExactKey, rowsByCompanyYear) {"
    helper = """  function settledBalanceForRow(row) {
    const adjustedAllocation = metricValue(row, "adjustedAllocation");
    const borrow = metricValue(row, "borrow");
    const verifiedEmissions = metricValue(row, "verifiedEmissions");
    const carryover = metricValue(row, "carryover");
    const inputs = [adjustedAllocation, borrow, verifiedEmissions, carryover];
    if (!inputs.every((value) => typeof value === "number")) return null;
    return adjustedAllocation + borrow - verifiedEmissions - carryover;
  }

"""
    if "function settledBalanceForRow" in text:
        raise SystemExit("settled balance helper already exists")
    text = replace_once(text, marker, helper + marker, "settled helper marker")

    text = replace_once(
        text,
        '      const currentPreAllocation = metricValue(row, "preAllocation");\n'
        '      const emptyComponents = {',
        '      const currentPreAllocation = metricValue(row, "preAllocation");\n'
        '      row.metrics = row.metrics && typeof row.metrics === "object" ? { ...row.metrics } : {};\n'
        '      row.metrics.settledBalance = settledBalanceForRow(row);\n'
        '      const emptyComponents = {',
        "settled calculation assignment",
    )

    marker = "    payload.balanceCalculation = {"
    metadata = """    if (payload?.metrics?.fields) {
      payload.metrics.fields.settledBalance = {
        label: "정산 과부족량",
        formula: "adjustedAllocation + borrow - verifiedEmissions - carryover",
        formulaKo: "해당 연도 조정할당량 + 해당 연도 차입량 - 해당 연도 인증배출량 - 해당 연도 이월량",
        nullRule: "null if any dependency is null",
      };
    }
"""
    if "payload.metrics.fields.settledBalance" in text:
        raise SystemExit("settled metadata already exists")
    text = replace_once(text, marker, metadata + marker, "settled metadata marker")
    text = replace_once(
        text,
        '      version: "4.0.0",',
        '      version: "4.1.0",',
        "calculation version",
    )

    marker = "  function moveBalanceCellsNextToAllocationType() {"
    markup = """  function settledBalanceMarkup(summary) {
    if (typeof summary.value !== "number") {
      return '<span class="annual-value-missing" aria-label="정산 과부족량 계산 불가">—</span>';
    }
    const rounded = Math.round(summary.value);
    const status = summary.partial ? "부분합" : rounded > 0 ? "과다" : rounded < 0 ? "부족" : "균형";
    const className = summary.partial
      ? "annual-balance-partial"
      : rounded > 0 ? "annual-balance-positive" : rounded < 0 ? "annual-balance-negative" : "annual-balance-neutral";
    const formatted = rounded > 0
      ? `+${numberFormat.format(rounded)}`
      : rounded < 0 ? `−${numberFormat.format(Math.abs(rounded))}` : "0";
    const badge = summary.partial
      ? '<span class="annual-partial-badge" title="일부 업체의 정산 과부족량만 합산했습니다.">부분</span>'
      : "";
    return [
      `<span class="annual-balance-value ${className}" aria-label="정산 ${status} ${escapeHtml(formatted)}톤">`,
      `<small aria-hidden="true">${status}</small><b aria-hidden="true">${escapeHtml(formatted)}</b>`,
      badge,
      "</span>",
    ].join("");
  }

  function normalizeAnnualTableColspans() {
    document.querySelectorAll("#annualRows td[colspan]").forEach((cell) => {
      cell.colSpan = 12;
    });
  }

"""
    if "function settledBalanceMarkup" in text:
        raise SystemExit("settled balance markup already exists")
    text = replace_once(text, marker, markup + marker, "settled markup marker")

    text = replace_once(
        text,
        '      const balanceCell = tableRow.querySelector("td.annual-balance-column");\n'
        '      const preAllocationCell = balanceCell?.nextElementSibling;\n'
        '      if (!balanceCell || !preAllocationCell) return;',
        '      const balanceCell = tableRow.querySelector("td.annual-balance-column");\n'
        '      const preAllocationCell = balanceCell?.nextElementSibling;\n'
        '      if (!balanceCell || !preAllocationCell) return;\n\n'
        '      let settledCell = tableRow.querySelector("td.annual-settled-balance-column");\n'
        '      if (!settledCell) {\n'
        '        settledCell = document.createElement("td");\n'
        '        settledCell.className = "annual-settled-balance-column";\n'
        '        tableRow.append(settledCell);\n'
        '      }',
        "settled table cell creation",
    )
    text = replace_once(
        text,
        '      balanceCell.innerHTML = balanceMarkup(metricSummary(sourceRows, "finalBalance"));\n'
        '    });',
        '      balanceCell.innerHTML = balanceMarkup(metricSummary(sourceRows, "finalBalance"));\n'
        '      settledCell.innerHTML = settledBalanceMarkup(metricSummary(sourceRows, "settledBalance"));\n'
        '    });',
        "settled table cell value",
    )
    text = replace_once(
        text,
        '  function refreshTableEnhancements() {\n'
        '    moveBalanceCellsNextToAllocationType();\n'
        '    decorateAnnualTable();\n'
        '  }',
        '  function refreshTableEnhancements() {\n'
        '    normalizeAnnualTableColspans();\n'
        '    moveBalanceCellsNextToAllocationType();\n'
        '    decorateAnnualTable();\n'
        '  }',
        "table refresh",
    )

    path.write_text(text, encoding="utf-8")


def update_html() -> None:
    path = Path("public/krx-annual.html")
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "20260902-company-history-v8",
        "20260902-settled-balance-v1",
    )
    text = replace_once(
        text,
        "선택 연도의 업체합계, 부문, 업종, 업체별 할당·배출·연초 과부족 현황. 단위는 톤입니다.",
        "선택 연도의 업체합계, 부문, 업종, 업체별 할당·배출·연초 및 정산 과부족 현황. 단위는 톤입니다.",
        "table caption",
    )
    text = replace_once(
        text,
        '              <th scope="col">상쇄발행</th>\n',
        '              <th scope="col">상쇄발행</th>\n'
        '              <th scope="col" class="annual-settled-balance-column" '
        'title="해당 연도의 조정할당량 + 차입량 − 인증배출량 − 이월량으로 계산한 원자료 기준 정산값입니다.">정산 과부족량</th>\n',
        "settled table header",
    )
    text = replace_once(
        text,
        '<tr class="annual-loading-row"><td colspan="11">연간 자료를 불러오고 있습니다.</td></tr>',
        '<tr class="annual-loading-row"><td colspan="12">연간 자료를 불러오고 있습니다.</td></tr>',
        "loading colspan",
    )
    path.write_text(text, encoding="utf-8")


def update_css() -> None:
    path = Path("public/assets/krx-annual-balance-v2.css")
    text = path.read_text(encoding="utf-8")
    if ".annual-settled-balance-column" in text:
        raise SystemExit("settled balance styles already exist")
    text += """

.annual-table th.annual-settled-balance-column,
.annual-table td.annual-settled-balance-column {
  background: #f2f6f7;
  font-weight: 850;
}

.annual-table th.annual-settled-balance-column {
  background: #eaf0f2;
}

.annual-row-total > td.annual-settled-balance-column { background: #e5eff0; }
.annual-row-sector > td.annual-settled-balance-column { background: #f0f5f5; }
.annual-row-industry > td.annual-settled-balance-column { background: #f6f9f9; }
.annual-row-company:hover > td.annual-settled-balance-column { background: #edf4f4; }
"""
    path.write_text(text, encoding="utf-8")


def main() -> None:
    update_javascript()
    update_html()
    update_css()


if __name__ == "__main__":
    main()
