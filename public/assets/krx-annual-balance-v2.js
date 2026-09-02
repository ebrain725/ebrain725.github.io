"use strict";

(() => {
  const TARGET_DATA_PATH = "data/krx-annual.json";
  const originalFetch = window.fetch.bind(window);
  const numberFormat = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });
  const collator = new Intl.Collator("ko-KR", { numeric: true, sensitivity: "base" });
  let displayRows = [];

  const hasOwn = (object, key) => Object.prototype.hasOwnProperty.call(object || {}, key);
  const cleanText = (value, fallback = "") => {
    const text = String(value ?? "").replace(/\s+/g, " ").trim();
    return text || fallback;
  };

  function numberOrNull(value) {
    if (value === null || value === undefined || value === "" || value === "—" || value === "-") return null;
    const normalized = typeof value === "string" ? value.replaceAll(",", "").trim() : value;
    const number = Number(normalized);
    return Number.isFinite(number) ? number : null;
  }

  function metricValue(row, key, aliases = []) {
    const metrics = row?.metrics && typeof row.metrics === "object" ? row.metrics : {};
    for (const candidate of [key, ...aliases]) {
      if (hasOwn(metrics, candidate)) return numberOrNull(metrics[candidate]);
    }
    for (const candidate of [key, ...aliases]) {
      if (hasOwn(row, candidate)) return numberOrNull(row[candidate]);
    }
    return null;
  }

  function rawRowsFromPayload(payload) {
    const source = payload?.rows ?? payload?.items ?? payload?.data ?? [];
    if (Array.isArray(source)) return source;
    if (source && typeof source === "object") {
      return Object.values(source).flatMap((value) => Array.isArray(value) ? value : []);
    }
    return [];
  }

  function rowYear(row) {
    return Number.parseInt(row?.year ?? row?.complianceYear, 10);
  }

  function rowCompanyId(row) {
    return cleanText(row?.companyId ?? row?.entityId);
  }

  function rowCompanyName(row) {
    return cleanText(row?.companyName ?? row?.entityName ?? row?.name);
  }

  function rowSector(row) {
    return cleanText(row?.sector ?? row?.division, "미분류");
  }

  function rowIndustry(row) {
    return cleanText(row?.industry ?? row?.businessType, "미분류");
  }

  function companyYearKey(row) {
    const year = rowYear(row);
    const companyId = rowCompanyId(row);
    if (!Number.isFinite(year) || !companyId) return "";
    return `${year}\u0001${companyId}`;
  }

  function exactYearKey(row) {
    const base = companyYearKey(row);
    if (!base) return "";
    return `${base}\u0001${rowSector(row)}\u0001${rowIndustry(row)}`;
  }

  function groupRows(rows, keyFactory) {
    const grouped = new Map();
    rows.forEach((row) => {
      const key = keyFactory(row);
      if (!key) return;
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(row);
    });
    return grouped;
  }

  function aggregateMetric(rows, key, aliases = []) {
    if (!Array.isArray(rows) || !rows.length) return null;
    const values = rows.map((row) => metricValue(row, key, aliases));
    if (!values.every((value) => typeof value === "number")) return null;
    return values.reduce((sum, value) => sum + value, 0);
  }

  function updateMissingState(row, isMissing, reason = "") {
    if (Array.isArray(row.missing)) {
      const next = row.missing.filter((key) => key !== "finalBalance" && key !== "complianceBalance");
      if (isMissing) next.push("finalBalance");
      row.missing = next;
    } else {
      row.missing = row.missing && typeof row.missing === "object" ? { ...row.missing } : {};
      row.missing.finalBalance = isMissing;
      if (hasOwn(row.missing, "complianceBalance")) row.missing.complianceBalance = isMissing;
    }

    row.missingReason = row.missingReason && typeof row.missingReason === "object"
      ? { ...row.missingReason }
      : {};
    if (isMissing) row.missingReason.finalBalance = reason || "dependency:previous-year";
    else delete row.missingReason.finalBalance;
  }

  function setCalculatedMetrics(row, appliedPreAllocation, finalBalance, reason = "") {
    row.metrics = row.metrics && typeof row.metrics === "object" ? { ...row.metrics } : {};
    row.metrics.appliedPreAllocation = appliedPreAllocation;
    row.metrics.finalBalance = finalBalance;
    if (hasOwn(row.metrics, "complianceBalance")) row.metrics.complianceBalance = finalBalance;
    updateMissingState(row, typeof finalBalance !== "number", reason);
  }

  function previousBasisForRow(row, rowsByExactKey, rowsByCompanyYear) {
    const year = rowYear(row);
    const companyId = rowCompanyId(row);
    if (!Number.isFinite(year) || !companyId) return null;

    const previousExactKey = `${year - 1}\u0001${companyId}\u0001${rowSector(row)}\u0001${rowIndustry(row)}`;
    const exactRows = rowsByExactKey.get(previousExactKey);
    if (exactRows?.length) return exactRows;

    const currentGroup = rowsByCompanyYear.get(`${year}\u0001${companyId}`) || [];
    const previousGroup = rowsByCompanyYear.get(`${year - 1}\u0001${companyId}`) || [];

    // 회사가 해당 연도에 한 행인 경우에만 회사 단위 전년도 합계로 보완합니다.
    // 여러 부문·업종으로 분할된 경우 같은 전년도 값이 중복 적용되는 것을 막습니다.
    if (currentGroup.length === 1 && previousGroup.length) return previousGroup;
    return null;
  }

  function recalculateAnnualMetrics(payload) {
    const rows = rawRowsFromPayload(payload);
    const rowsByExactKey = groupRows(rows, exactYearKey);
    const rowsByCompanyYear = groupRows(rows, companyYearKey);

    rows.forEach((row) => {
      const year = rowYear(row);
      const currentPreAllocation = metricValue(row, "preAllocation", ["initialAllocation"]);
      const previousRows = previousBasisForRow(row, rowsByExactKey, rowsByCompanyYear);

      if (!Number.isFinite(year) || year <= 2015) {
        setCalculatedMetrics(row, null, null, "dependency:previous-year-outside-range");
        return;
      }
      if (typeof currentPreAllocation !== "number") {
        setCalculatedMetrics(row, null, null, "dependency:current-pre-allocation");
        return;
      }
      if (!previousRows) {
        setCalculatedMetrics(row, null, null, "dependency:previous-year-company-row");
        return;
      }

      const previousAdditionalAllocation = aggregateMetric(
        previousRows,
        "additionalAllocation",
        ["additional"],
      );
      const previousCancellation = aggregateMetric(
        previousRows,
        "cancellation",
        ["allocationCancellation", "cancelledAllocation"],
      );
      const previousCarryover = aggregateMetric(previousRows, "carryover", ["carriedOver"]);
      const previousBorrow = aggregateMetric(previousRows, "borrow", ["borrowed"]);
      const previousVerifiedEmissions = aggregateMetric(
        previousRows,
        "verifiedEmissions",
        ["certifiedEmissions", "emissions"],
      );

      if (
        typeof previousAdditionalAllocation !== "number"
        || typeof previousCancellation !== "number"
      ) {
        setCalculatedMetrics(row, null, null, "dependency:previous-year-allocation-adjustments");
        return;
      }

      const appliedPreAllocation = currentPreAllocation
        + previousAdditionalAllocation
        - previousCancellation;

      if (
        typeof previousCarryover !== "number"
        || typeof previousBorrow !== "number"
        || typeof previousVerifiedEmissions !== "number"
      ) {
        setCalculatedMetrics(row, appliedPreAllocation, null, "dependency:previous-year-compliance-metrics");
        return;
      }

      const finalBalance = appliedPreAllocation
        + previousCarryover
        - previousBorrow
        - previousVerifiedEmissions;
      setCalculatedMetrics(row, appliedPreAllocation, finalBalance);
    });

    if (payload?.metrics?.finalBalance && typeof payload.metrics.finalBalance === "object") {
      payload.metrics.finalBalance = {
        ...payload.metrics.finalBalance,
        label: "과부족량",
        formula: "appliedPreAllocation + previousCarryover - previousBorrow - previousVerifiedEmissions",
        formulaKo: "반영 사전할당량 + 전년도 이월량 - 전년도 차입량 - 전년도 인증배출량",
      };
    }

    payload.balanceCalculation = {
      version: "3.0.0",
      appliedPreAllocationFormula: "currentPreAllocation + previousAdditionalAllocation - previousCancellation",
      appliedPreAllocationFormulaKo: "당해년도 사전할당량 + 전년도 추가할당량 - 전년도 할당취소량",
      formula: "appliedPreAllocation + previousCarryover - previousBorrow - previousVerifiedEmissions",
      formulaKo: "반영 사전할당량 + 전년도 이월량 - 전년도 차입량 - 전년도 인증배출량",
      firstAvailableYear: 2016,
      predecessorEntitiesCombined: false,
    };

    displayRows = normalizeDisplayRows(rows);
    return payload;
  }

  function normalizeAliases(row) {
    return [row?.aliases, row?.companyAliases, row?.previousNames, row?.relatedCompanyNames]
      .flatMap((value) => Array.isArray(value) ? value : value ? [value] : [])
      .map((value) => cleanText(value))
      .filter(Boolean);
  }

  function relationNames(row) {
    if (!Array.isArray(row?.entityRelations)) return [];
    return row.entityRelations
      .map((relation) => cleanText(relation?.counterpartName))
      .filter(Boolean);
  }

  function mergeMetricValues(rows, key, aliases = []) {
    const values = rows
      .map((row) => metricValue(row, key, aliases))
      .filter((value) => typeof value === "number");
    return values.length ? values.reduce((sum, value) => sum + value, 0) : null;
  }

  function normalizeDisplayRows(rows) {
    const grouped = new Map();
    rows.forEach((row, index) => {
      const year = rowYear(row);
      const companyName = rowCompanyName(row);
      if (!Number.isFinite(year) || !companyName) return;
      const companyId = rowCompanyId(row) || `${companyName}-${index}`;
      const sector = rowSector(row);
      const industry = rowIndustry(row);
      const key = `${year}\u0001${sector}\u0001${industry}\u0001${companyId}`;
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push({
        source: row,
        year,
        sector,
        industry,
        companyId,
        companyName,
        searchText: [companyName, ...normalizeAliases(row), ...relationNames(row)]
          .join(" ")
          .toLocaleLowerCase("ko-KR"),
      });
    });

    return [...grouped.values()].map((group) => {
      const sources = group.map((item) => item.source);
      const names = group.map((item) => item.companyName).sort((left, right) => collator.compare(left, right));
      const appliedValues = sources
        .map((row) => metricValue(row, "appliedPreAllocation"))
        .filter((value) => typeof value === "number");
      return {
        year: group[0].year,
        sector: group[0].sector,
        industry: group[0].industry,
        companyId: group[0].companyId,
        companyName: names[0],
        searchText: [...new Set(group.map((item) => item.searchText))].join(" "),
        metrics: {
          preAllocation: mergeMetricValues(sources, "preAllocation", ["initialAllocation"]),
          appliedPreAllocation: appliedValues.length
            ? appliedValues.reduce((sum, value) => sum + value, 0)
            : null,
          verifiedEmissions: mergeMetricValues(
            sources,
            "verifiedEmissions",
            ["certifiedEmissions", "emissions"],
          ),
          finalBalance: mergeMetricValues(sources, "finalBalance", ["complianceBalance"]),
        },
        appliedCoverage: {
          completeCount: appliedValues.length,
          totalCount: sources.length,
        },
      };
    });
  }

  function isAnnualDataRequest(input) {
    const rawUrl = typeof input === "string" ? input : input?.url;
    if (!rawUrl) return false;
    try {
      const requestUrl = new URL(rawUrl, document.baseURI);
      const targetUrl = new URL(TARGET_DATA_PATH, document.baseURI);
      return requestUrl.origin === targetUrl.origin && requestUrl.pathname === targetUrl.pathname;
    } catch {
      return String(rawUrl).includes(TARGET_DATA_PATH);
    }
  }

  window.fetch = async (...args) => {
    const response = await originalFetch(...args);
    if (!response.ok || !isAnnualDataRequest(args[0])) return response;

    try {
      const payload = await response.clone().json();
      const transformed = recalculateAnnualMetrics(payload);
      const headers = new Headers(response.headers);
      headers.set("content-type", "application/json; charset=utf-8");
      headers.delete("content-length");
      return new Response(JSON.stringify(transformed), {
        status: response.status,
        statusText: response.statusText,
        headers,
      });
    } catch (error) {
      console.error("KRX 연간 과부족량·사전할당 반영값 계산 실패", error);
      return response;
    }
  };

  function balanceMatches(row, status) {
    if (!status) return true;
    const value = row.metrics.finalBalance;
    if (status === "unavailable") return value === null;
    if (typeof value !== "number") return false;
    if (status === "surplus") return value > 0;
    if (status === "shortage") return value < 0;
    if (status === "balanced") return value === 0;
    return true;
  }

  function filteredDisplayRows() {
    const year = Number.parseInt(document.getElementById("annualYear")?.value, 10);
    const sector = document.getElementById("annualSector")?.value || "";
    const industry = document.getElementById("annualIndustry")?.value || "";
    const query = cleanText(document.getElementById("annualCompanySearch")?.value)
      .toLocaleLowerCase("ko-KR");
    const balanceStatus = document.getElementById("annualBalanceStatus")?.value || "";

    return displayRows.filter((row) => (
      row.year === year
      && (!sector || row.sector === sector)
      && (!industry || row.industry === industry)
      && (!query || row.searchText.includes(query))
      && balanceMatches(row, balanceStatus)
    ));
  }

  function appliedSummary(rows) {
    const numericRows = rows.filter((row) => typeof row.metrics.appliedPreAllocation === "number");
    if (!numericRows.length) return { value: null, partial: false };
    return {
      value: numericRows.reduce((sum, row) => sum + row.metrics.appliedPreAllocation, 0),
      partial: numericRows.length < rows.length
        || numericRows.some((row) => row.appliedCoverage.completeCount < row.appliedCoverage.totalCount),
    };
  }

  function companyQueueKey(row) {
    return `${row.sector}\u0001${row.industry}\u0001${row.companyName}`;
  }

  function makeCompanyQueues(rows) {
    const queues = new Map();
    rows.forEach((row) => {
      const key = companyQueueKey(row);
      if (!queues.has(key)) queues.set(key, []);
      queues.get(key).push(row);
    });
    queues.forEach((queue) => queue.sort((left, right) => {
      const emissionsLeft = left.metrics.verifiedEmissions ?? Number.NEGATIVE_INFINITY;
      const emissionsRight = right.metrics.verifiedEmissions ?? Number.NEGATIVE_INFINITY;
      return emissionsRight - emissionsLeft || collator.compare(left.companyId, right.companyId);
    }));
    return queues;
  }

  function appliedMarkup(summary) {
    const span = document.createElement("span");
    span.className = "annual-applied-preallocation";
    if (summary.partial) span.classList.add("partial");

    if (typeof summary.value !== "number") {
      span.classList.add("missing");
      span.textContent = "(—)";
      span.setAttribute("aria-label", "전년도 추가할당과 할당취소를 반영한 사전할당량 계산 불가");
      span.title = "전년도 자료가 없거나 계산에 필요한 값이 없습니다.";
      return span;
    }

    const formatted = numberFormat.format(Math.round(summary.value));
    span.textContent = `(${formatted})`;
    span.setAttribute(
      "aria-label",
      `전년도 추가할당과 할당취소를 반영한 사전할당량 ${formatted}톤${summary.partial ? ", 부분 집계" : ""}`,
    );
    span.title = summary.partial
      ? "전년도 추가할당·할당취소 반영값(일부 원자료 결측)"
      : "전년도 추가할당·할당취소 반영값";
    return span;
  }

  function moveBalanceCellsNextToAllocationType() {
    document.querySelectorAll("#annualRows tr").forEach((row) => {
      const allocationCell = row.querySelector("td.annual-allocation-type-cell");
      const balanceCell = row.querySelector("td.annual-balance-column");
      if (allocationCell && balanceCell && allocationCell.nextElementSibling !== balanceCell) {
        allocationCell.after(balanceCell);
      }
    });
  }

  function renderAppliedPreAllocations() {
    const filtered = filteredDisplayRows();
    const companyQueues = makeCompanyQueues(filtered);
    let currentSector = "";
    let currentIndustry = "";

    document.querySelectorAll("#annualRows tr").forEach((tableRow) => {
      const balanceCell = tableRow.querySelector("td.annual-balance-column");
      const preAllocationCell = balanceCell?.nextElementSibling;
      if (!preAllocationCell) return;

      preAllocationCell.querySelector(".annual-applied-preallocation")?.remove();
      const label = cleanText(tableRow.querySelector(".annual-tree-label")?.textContent);
      let sourceRows = [];

      if (tableRow.classList.contains("annual-row-total")) {
        currentSector = "";
        currentIndustry = "";
        sourceRows = filtered;
      } else if (tableRow.classList.contains("annual-row-sector")) {
        currentSector = label;
        currentIndustry = "";
        sourceRows = filtered.filter((row) => row.sector === currentSector);
      } else if (tableRow.classList.contains("annual-row-industry")) {
        currentIndustry = label;
        sourceRows = filtered.filter((row) => (
          row.sector === currentSector && row.industry === currentIndustry
        ));
      } else if (tableRow.classList.contains("annual-row-company")) {
        const key = `${currentSector}\u0001${currentIndustry}\u0001${label}`;
        const queue = companyQueues.get(key) || [];
        const companyRow = queue.shift();
        sourceRows = companyRow ? [companyRow] : [];
      }

      preAllocationCell.classList.add("annual-preallocation-with-applied");
      preAllocationCell.append(appliedMarkup(appliedSummary(sourceRows)));
    });
  }

  function refreshTableEnhancements() {
    moveBalanceCellsNextToAllocationType();
    renderAppliedPreAllocations();
  }

  function initTableObserver() {
    refreshTableEnhancements();
    const tbody = document.getElementById("annualRows");
    if (!tbody) return;
    let scheduled = false;
    const observer = new MutationObserver(() => {
      if (scheduled) return;
      scheduled = true;
      window.requestAnimationFrame(() => {
        scheduled = false;
        refreshTableEnhancements();
      });
    });
    observer.observe(tbody, { childList: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initTableObserver, { once: true });
  } else {
    initTableObserver();
  }
})();
