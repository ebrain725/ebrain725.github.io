"use strict";

(() => {
  const TARGET_DATA_PATH = "data/krx-annual.json";
  const inheritedFetch = window.fetch.bind(window);
  const numberFormat = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });
  const collator = new Intl.Collator("ko-KR", { numeric: true, sensitivity: "base" });
  let displayRows = [];

  const byId = (id) => document.getElementById(id);
  const hasOwn = (object, key) => Object.prototype.hasOwnProperty.call(object || {}, key);
  const cleanText = (value, fallback = "") => {
    const text = String(value ?? "").replace(/\s+/g, " ").trim();
    return text || fallback;
  };
  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

  function numberOrNull(value) {
    if (value === null || value === undefined || value === "" || value === "—" || value === "-") return null;
    const normalized = typeof value === "string" ? value.replaceAll(",", "").trim() : value;
    const number = Number(normalized);
    return Number.isFinite(number) ? number : null;
  }

  function metricValue(row, key) {
    const metrics = row?.metrics && typeof row.metrics === "object" ? row.metrics : {};
    if (hasOwn(metrics, key)) return numberOrNull(metrics[key]);
    if (hasOwn(row, key)) return numberOrNull(row[key]);
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

  function normalizeAliases(row) {
    return [
      row?.aliases,
      row?.companyAliases,
      row?.previousNames,
      row?.relatedCompanyNames,
    ].flatMap((value) => Array.isArray(value) ? value : value ? [value] : [])
      .map((value) => cleanText(value))
      .filter(Boolean);
  }

  function relationNames(row) {
    if (!Array.isArray(row?.entityRelations)) return [];
    return row.entityRelations
      .map((relation) => cleanText(relation?.counterpartName))
      .filter(Boolean);
  }

  function updateMissing(row, key, isMissing, reason) {
    if (Array.isArray(row.missing)) {
      const next = row.missing.filter((item) => item !== key);
      if (isMissing) next.push(key);
      row.missing = next;
    } else {
      row.missing = row.missing && typeof row.missing === "object" ? { ...row.missing } : {};
      row.missing[key] = isMissing;
    }
    row.missingReason = row.missingReason && typeof row.missingReason === "object"
      ? { ...row.missingReason }
      : {};
    if (isMissing) row.missingReason[key] = reason;
    else delete row.missingReason[key];
  }

  function recalculateEndingBalance(payload) {
    const rows = rawRowsFromPayload(payload);
    rows.forEach((row) => {
      const year = rowYear(row);
      const estimatedFreeAllocation = metricValue(row, "estimatedFreeAllocation")
        ?? metricValue(row, "adjustedAllocation");
      const previousCarryoverRaw = metricValue(row, "previousCarryover");
      const previousBorrowRaw = metricValue(row, "previousBorrow");
      const previousCarryover = year === 2015 && previousCarryoverRaw === null ? 0 : previousCarryoverRaw;
      const previousBorrow = year === 2015 && previousBorrowRaw === null ? 0 : previousBorrowRaw;
      const additionalAllocation = metricValue(row, "additionalAllocation");
      const cancellation = metricValue(row, "cancellation");
      const verifiedEmissions = metricValue(row, "verifiedEmissions");
      const inputs = [
        estimatedFreeAllocation,
        previousCarryover,
        previousBorrow,
        additionalAllocation,
        cancellation,
        verifiedEmissions,
      ];
      const endingBalance = inputs.every((value) => typeof value === "number")
        ? estimatedFreeAllocation
          + previousCarryover
          - previousBorrow
          + additionalAllocation
          - cancellation
          - verifiedEmissions
        : null;

      row.metrics = row.metrics && typeof row.metrics === "object" ? { ...row.metrics } : {};
      row.metrics.endingBalance = endingBalance;
      row.endingBalanceEstimate = {
        estimatedFreeAllocation,
        previousCarryover,
        previousBorrow,
        additionalAllocation,
        cancellation,
        verifiedEmissions,
        endingBalance,
      };
      if (row.procurementEstimate && typeof row.procurementEstimate === "object") {
        row.procurementEstimate = {
          ...row.procurementEstimate,
          endingBalance,
        };
      }
      updateMissing(
        row,
        "endingBalance",
        typeof endingBalance !== "number",
        "dependency:annual-ending-balance",
      );
    });

    payload.rows = rows;
    if (payload?.metrics?.fields) {
      payload.metrics.fields.endingBalance = {
        label: "최종 과부족량(종료)",
        formula: "estimatedFreeAllocation + previousCarryover - previousBorrow + additionalAllocation - cancellation - verifiedEmissions",
        formulaKo: "무상 사전할당 추정량 + 전년도 이월량 - 전년도 차입량 + 당해년도 추가할당량 - 당해년도 할당취소량 - 당해년도 인증배출량",
        nullRule: "필수 입력 중 하나라도 확인되지 않으면 null",
      };
    }
    payload.endingBalanceEstimate = {
      version: "1.0.0",
      label: "최종 과부족량(종료)",
      formula: "estimatedFreeAllocation + previousCarryover - previousBorrow + additionalAllocation - cancellation - verifiedEmissions",
      formulaKo: "무상 사전할당 추정량 + 전년도 이월량 - 전년도 차입량 + 당해년도 추가할당량 - 당해년도 할당취소량 - 당해년도 인증배출량",
      excludes: [
        "currentYearCarryover",
        "currentYearBorrow",
        "offsetIssued",
        "auctionAwards",
        "exchangeAndOtcTrades",
        "otherHoldings",
      ],
      disclaimer: "당해년도 이월·차입은 종료 과부족을 처리한 결과이고 업체별 실제 매입·보유량은 공개 원자료에 없어 제외한 추정치",
    };

    displayRows = normalizeDisplayRows(rows);
    return payload;
  }

  function mergeMetricValues(rows, key) {
    const values = rows.map((row) => metricValue(row, key));
    const numeric = values.filter((value) => typeof value === "number");
    return {
      value: numeric.length ? numeric.reduce((sum, value) => sum + value, 0) : null,
      completeCount: numeric.length,
      totalCount: rows.length,
    };
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
      grouped.get(key).push(row);
    });

    return [...grouped.values()].map((group) => {
      const ending = mergeMetricValues(group, "endingBalance");
      const start = mergeMetricValues(group, "finalBalance");
      const emissions = mergeMetricValues(group, "verifiedEmissions");
      const aliases = [...new Set(group.flatMap((row) => [
        rowCompanyName(row),
        ...normalizeAliases(row),
        ...relationNames(row),
      ]).filter(Boolean))];
      const names = group
        .map((row) => rowCompanyName(row))
        .sort((left, right) => collator.compare(left, right));
      return {
        year: rowYear(group[0]),
        sector: rowSector(group[0]),
        industry: rowIndustry(group[0]),
        companyId: rowCompanyId(group[0]),
        companyName: names[0],
        searchText: aliases.join(" ").toLocaleLowerCase("ko-KR"),
        metrics: {
          endingBalance: ending.value,
          finalBalance: start.value,
          verifiedEmissions: emissions.value,
        },
        coverage: {
          endingBalance: ending,
          finalBalance: start,
          verifiedEmissions: emissions,
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
    const response = await inheritedFetch(...args);
    if (!response.ok || !isAnnualDataRequest(args[0])) return response;
    try {
      const payload = await response.clone().json();
      const transformed = recalculateEndingBalance(payload);
      const headers = new Headers(response.headers);
      headers.set("content-type", "application/json; charset=utf-8");
      headers.delete("content-length");
      return new Response(JSON.stringify(transformed), {
        status: response.status,
        statusText: response.statusText,
        headers,
      });
    } catch (error) {
      console.error("KRX 연간 최종 과부족량 계산 실패", error);
      return response;
    }
  };

  function filteredDisplayRows() {
    const year = Number.parseInt(byId("annualYear")?.value, 10);
    const sector = byId("annualSector")?.value || "";
    const industry = byId("annualIndustry")?.value || "";
    const query = cleanText(byId("annualCompanySearch")?.value).toLocaleLowerCase("ko-KR");
    const status = byId("annualBalanceStatus")?.value || "";
    return displayRows.filter((row) => {
      const startBalance = row.metrics.finalBalance;
      const statusMatch = !status
        || (status === "unavailable" && startBalance === null)
        || (status === "surplus" && typeof startBalance === "number" && startBalance > 0)
        || (status === "shortage" && typeof startBalance === "number" && startBalance < 0)
        || (status === "balanced" && startBalance === 0);
      return row.year === year
        && (!sector || row.sector === sector)
        && (!industry || row.industry === industry)
        && (!query || row.searchText.includes(query))
        && statusMatch;
    });
  }

  function metricSummary(rows) {
    const numericRows = rows.filter((row) => typeof row.metrics.endingBalance === "number");
    if (!numericRows.length) return { value: null, partial: false };
    return {
      value: numericRows.reduce((sum, row) => sum + row.metrics.endingBalance, 0),
      partial: numericRows.length < rows.length
        || numericRows.some((row) => (
          row.coverage.endingBalance.completeCount < row.coverage.endingBalance.totalCount
        )),
    };
  }

  function endingBalanceMarkup(summary) {
    if (typeof summary.value !== "number") {
      return '<span class="annual-value-missing" aria-label="최종 과부족량 계산 불가">—</span>';
    }
    const rounded = Math.round(summary.value);
    const status = summary.partial ? "부분합" : rounded > 0 ? "잉여" : rounded < 0 ? "조달필요" : "균형";
    const className = summary.partial
      ? "annual-balance-partial"
      : rounded > 0 ? "annual-balance-positive" : rounded < 0 ? "annual-balance-negative" : "annual-balance-neutral";
    const formatted = rounded > 0
      ? `+${numberFormat.format(rounded)}`
      : rounded < 0 ? `−${numberFormat.format(Math.abs(rounded))}` : "0";
    const badge = summary.partial
      ? '<span class="annual-partial-badge" title="일부 업체만 계산되었습니다.">부분</span>'
      : "";
    return [
      `<span class="annual-balance-value ${className}" aria-label="종료 ${status} ${escapeHtml(formatted)}톤">`,
      `<small aria-hidden="true">${status}</small><b aria-hidden="true">${escapeHtml(formatted)}</b>`,
      badge,
      "</span>",
    ].join("");
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
    queues.forEach((queue) => queue.sort((left, right) => (
      (right.metrics.verifiedEmissions ?? Number.NEGATIVE_INFINITY)
      - (left.metrics.verifiedEmissions ?? Number.NEGATIVE_INFINITY)
      || collator.compare(left.companyId, right.companyId)
    )));
    return queues;
  }

  function decorateTable() {
    document.querySelectorAll("#annualRows td[colspan]").forEach((cell) => { cell.colSpan = 12; });
    const filtered = filteredDisplayRows();
    const companyQueues = makeCompanyQueues(filtered);
    let currentSector = "";
    let currentIndustry = "";

    document.querySelectorAll("#annualRows tr").forEach((tableRow) => {
      const label = cleanText(tableRow.querySelector(".annual-tree-label")?.textContent);
      if (!label) return;
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
        sourceRows = filtered.filter((row) => row.sector === currentSector && row.industry === currentIndustry);
      } else if (tableRow.classList.contains("annual-row-company")) {
        const key = `${currentSector}\u0001${currentIndustry}\u0001${label}`;
        const queue = companyQueues.get(key) || [];
        const companyRow = queue.shift();
        sourceRows = companyRow ? [companyRow] : [];
      }

      let endingCell = tableRow.querySelector("td.annual-ending-balance-column");
      if (!endingCell) {
        endingCell = document.createElement("td");
        endingCell.className = "annual-ending-balance-column";
        tableRow.append(endingCell);
      }
      endingCell.innerHTML = endingBalanceMarkup(metricSummary(sourceRows));
    });
  }

  function scheduleDecoration() {
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(() => {
          window.requestAnimationFrame(decorateTable);
        });
      });
    });
  }

  function initTableObserver() {
    scheduleDecoration();
    const tbody = byId("annualRows");
    if (!tbody) return;
    const observer = new MutationObserver(scheduleDecoration);
    observer.observe(tbody, { childList: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initTableObserver, { once: true });
  } else {
    initTableObserver();
  }
})();
