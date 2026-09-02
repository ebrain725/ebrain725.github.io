"use strict";

(() => {
  const TARGET_DATA_PATH = "data/krx-annual.json";
  const originalFetch = window.fetch.bind(window);

  const hasOwn = (object, key) => Object.prototype.hasOwnProperty.call(object || {}, key);
  const cleanText = (value) => String(value ?? "").replace(/\s+/g, " ").trim();

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

  function rowSector(row) {
    return cleanText(row?.sector ?? row?.division);
  }

  function rowIndustry(row) {
    return cleanText(row?.industry ?? row?.businessType);
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

  function setFinalBalance(row, value, reason = "") {
    row.metrics = row.metrics && typeof row.metrics === "object" ? { ...row.metrics } : {};
    row.metrics.finalBalance = value;
    if (hasOwn(row.metrics, "complianceBalance")) row.metrics.complianceBalance = value;
    updateMissingState(row, typeof value !== "number", reason);
  }

  function previousBasisForRow(row, rowsByExactKey, rowsByCompanyYear, currentByCompanyYear) {
    const year = rowYear(row);
    const companyId = rowCompanyId(row);
    if (!Number.isFinite(year) || !companyId) return null;

    const previousExactKey = `${year - 1}\u0001${companyId}\u0001${rowSector(row)}\u0001${rowIndustry(row)}`;
    const exactRows = rowsByExactKey.get(previousExactKey);
    if (exactRows?.length) return exactRows;

    const currentGroup = currentByCompanyYear.get(`${year}\u0001${companyId}`) || [];
    const previousGroup = rowsByCompanyYear.get(`${year - 1}\u0001${companyId}`) || [];

    // A company-level fallback is used only when the current year has one row.
    // This prevents the same prior-year total from being duplicated across split rows.
    if (currentGroup.length === 1 && previousGroup.length) return previousGroup;
    return null;
  }

  function recalculateFinalBalances(payload) {
    const rows = rawRowsFromPayload(payload);
    const rowsByExactKey = groupRows(rows, exactYearKey);
    const rowsByCompanyYear = groupRows(rows, companyYearKey);

    rows.forEach((row) => {
      const year = rowYear(row);
      const currentPreAllocation = metricValue(row, "preAllocation", ["initialAllocation"]);
      const previousRows = previousBasisForRow(
        row,
        rowsByExactKey,
        rowsByCompanyYear,
        rowsByCompanyYear,
      );

      if (!Number.isFinite(year) || year <= 2015) {
        setFinalBalance(row, null, "dependency:previous-year-outside-range");
        return;
      }
      if (typeof currentPreAllocation !== "number") {
        setFinalBalance(row, null, "dependency:current-pre-allocation");
        return;
      }
      if (!previousRows) {
        setFinalBalance(row, null, "dependency:previous-year-company-row");
        return;
      }

      const previousCarryover = aggregateMetric(previousRows, "carryover", ["carriedOver"]);
      const previousBorrow = aggregateMetric(previousRows, "borrow", ["borrowed"]);
      const previousVerifiedEmissions = aggregateMetric(
        previousRows,
        "verifiedEmissions",
        ["certifiedEmissions", "emissions"],
      );

      if (
        typeof previousCarryover !== "number"
        || typeof previousBorrow !== "number"
        || typeof previousVerifiedEmissions !== "number"
      ) {
        setFinalBalance(row, null, "dependency:previous-year-metrics");
        return;
      }

      const finalBalance = currentPreAllocation
        + previousCarryover
        - previousBorrow
        - previousVerifiedEmissions;
      setFinalBalance(row, finalBalance);
    });

    if (payload?.metrics?.finalBalance && typeof payload.metrics.finalBalance === "object") {
      payload.metrics.finalBalance = {
        ...payload.metrics.finalBalance,
        label: "과부족량",
        formula: "currentPreAllocation + previousCarryover - previousBorrow - previousVerifiedEmissions",
        formulaKo: "당해년도 사전할당량 + 전년도 이월량 - 전년도 차입량 - 전년도 인증배출량",
      };
    }

    payload.balanceCalculation = {
      version: "2.0.0",
      formula: "currentPreAllocation + previousCarryover - previousBorrow - previousVerifiedEmissions",
      formulaKo: "당해년도 사전할당량 + 전년도 이월량 - 전년도 차입량 - 전년도 인증배출량",
      firstAvailableYear: 2016,
      predecessorEntitiesCombined: false,
    };

    return payload;
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
      const transformed = recalculateFinalBalances(payload);
      const headers = new Headers(response.headers);
      headers.set("content-type", "application/json; charset=utf-8");
      headers.delete("content-length");
      return new Response(JSON.stringify(transformed), {
        status: response.status,
        statusText: response.statusText,
        headers,
      });
    } catch (error) {
      console.error("KRX 연간 과부족량 재계산 실패", error);
      return response;
    }
  };

  function moveBalanceCellsNextToAllocationType() {
    document.querySelectorAll("#annualRows tr").forEach((row) => {
      const allocationCell = row.querySelector("td.annual-allocation-type-cell");
      const balanceCell = row.querySelector("td.annual-balance-column");
      if (allocationCell && balanceCell && allocationCell.nextElementSibling !== balanceCell) {
        allocationCell.after(balanceCell);
      }
    });
  }

  function initColumnObserver() {
    moveBalanceCellsNextToAllocationType();
    const tbody = document.getElementById("annualRows");
    if (!tbody) return;
    const observer = new MutationObserver(() => moveBalanceCellsNextToAllocationType());
    observer.observe(tbody, { childList: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initColumnObserver, { once: true });
  } else {
    initColumnObserver();
  }
})();
