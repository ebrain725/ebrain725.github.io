"use strict";

(() => {
  const TARGET_DATA_PATH = "data/krx-annual.json";
  const inheritedFetch = window.fetch.bind(window);
  const numberFormat = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });
  const collator = new Intl.Collator("ko-KR", { numeric: true, sensitivity: "base" });
  const START_YEAR = 2015;
  const END_YEAR = 2025;
  const DISPLAY_KEYS = [
    "preAllocation",
    "estimatedPaidAllocation",
    "estimatedFreeAllocation",
    "previousCarryover",
    "previousBorrow",
    "previousVerifiedEmissions",
    "finalBalance",
  ];

  let displayRows = [];
  let historyBound = false;

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

  function normalizedCompanyKey(value) {
    return cleanText(value)
      .normalize("NFKC")
      .replaceAll("㈜", "주식회사")
      .replace(/\(\s*주\s*\)/g, "주식회사")
      .replace(/\s+/g, "")
      .toLocaleLowerCase("ko-KR");
  }

  function normalizedAllocationType(row) {
    const value = cleanText(row?.allocationType ?? row?.paidAllocation ?? row?.metrics?.allocationType)
      .toLowerCase()
      .replaceAll(" ", "");
    if (["y", "yes", "paid", "auction", "유상", "유상할당"].includes(value)) return "paid";
    if (["n", "no", "free", "무상", "무상할당"].includes(value)) return "free";
    if (["mixed", "혼합", "유상/무상", "유상·무상"].includes(value)) return "mixed";
    return null;
  }

  function paidAllocationRate(row) {
    const year = rowYear(row);
    const preAllocation = metricValue(row, "preAllocation");
    if (!Number.isFinite(year) || year < START_YEAR || year > END_YEAR) return null;
    if (year <= 2017) return 0;
    const type = normalizedAllocationType(row);
    if (type === "free") return 0;
    if (type === "paid") return year <= 2020 ? 0.03 : 0.10;
    if (preAllocation === 0) return 0;
    return null;
  }

  function updateMissing(row, key, isMissing, reason = "") {
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
    if (isMissing) row.missingReason[key] = reason || "dependency:procurement-estimate";
    else delete row.missingReason[key];
  }

  function recalculateProcurementEstimate(payload) {
    const rows = rawRowsFromPayload(payload);
    rows.forEach((row) => {
      const preAllocation = metricValue(row, "preAllocation");
      const rate = paidAllocationRate(row);
      const paidEstimate = typeof preAllocation === "number" && typeof rate === "number"
        ? Math.round(preAllocation * rate)
        : null;
      const freeEstimate = typeof preAllocation === "number" && typeof paidEstimate === "number"
        ? preAllocation - paidEstimate
        : null;
      const previousCarryover = metricValue(row, "previousCarryover");
      const previousBorrow = metricValue(row, "previousBorrow");
      const previousVerifiedEmissions = metricValue(row, "previousVerifiedEmissions");
      const finalInputs = [
        freeEstimate,
        previousCarryover,
        previousBorrow,
        previousVerifiedEmissions,
      ];
      const finalBalance = finalInputs.every((value) => typeof value === "number")
        ? freeEstimate + previousCarryover - previousBorrow - previousVerifiedEmissions
        : null;

      row.metrics = row.metrics && typeof row.metrics === "object" ? { ...row.metrics } : {};
      if (!hasOwn(row.metrics, "sourceAdjustedAllocation")) {
        row.metrics.sourceAdjustedAllocation = metricValue(row, "adjustedAllocation");
      }
      row.metrics.paidAllocationRate = rate;
      row.metrics.estimatedPaidAllocation = paidEstimate;
      row.metrics.estimatedFreeAllocation = freeEstimate;
      row.metrics.adjustedAllocation = freeEstimate;
      row.metrics.finalBalance = finalBalance;
      if (hasOwn(row.metrics, "complianceBalance")) row.metrics.complianceBalance = finalBalance;
      row.procurementEstimate = {
        method: "gross-preallocation-less-paid-share",
        paidAllocationRate: rate,
        estimatedPaidAllocation: paidEstimate,
        estimatedFreeAllocation: freeEstimate,
        previousCarryover,
        previousBorrow,
        previousVerifiedEmissions,
        finalBalance,
      };

      updateMissing(row, "adjustedAllocation", typeof freeEstimate !== "number", "dependency:allocation-method");
      updateMissing(row, "finalBalance", typeof finalBalance !== "number", "dependency:annual-start-procurement-position");
    });

    payload.rows = rows;
    if (payload?.metrics?.fields) {
      payload.metrics.fields.adjustedAllocation = {
        label: "무상 사전할당 추정",
        formula: "preAllocation - estimatedPaidAllocation",
        formulaKo: "총 사전할당량 - 유상분 추정량",
        nullRule: "유상할당 방식 또는 총 사전할당량이 확인되지 않으면 null",
      };
      payload.metrics.fields.estimatedPaidAllocation = {
        label: "유상분 추정",
        formula: "preAllocation * paidAllocationRate",
        formulaKo: "총 사전할당량 × 계획기간 유상할당률",
        nullRule: "유상할당 방식 또는 총 사전할당량이 확인되지 않으면 null",
      };
      payload.metrics.fields.finalBalance = {
        label: "연초 추정 과부족량",
        formula: "estimatedFreeAllocation + previousCarryover - previousBorrow - previousVerifiedEmissions",
        formulaKo: "무상 사전할당 추정량 + 전년도 이월량 - 전년도 차입량 - 전년도 인증배출량",
        nullRule: "필수 입력 중 하나라도 확인되지 않으면 null",
      };
    }
    payload.procurementEstimate = {
      version: "5.0.0",
      label: "연초 추정 과부족량",
      purpose: "유상할당 차감 후 연초 기준 조달 필요량을 보수적으로 추정",
      paidAllocationRates: {
        "2015-2017": { paidSubject: 0, freeSubject: 0 },
        "2018-2020": { paidSubject: 0.03, freeSubject: 0 },
        "2021-2025": { paidSubject: 0.10, freeSubject: 0 },
      },
      paidEstimateFormula: "preAllocation * paidAllocationRate",
      freeEstimateFormula: "preAllocation - estimatedPaidAllocation",
      balanceFormula: "estimatedFreeAllocation + previousCarryover - previousBorrow - previousVerifiedEmissions",
      excluded: [
        "auctionAwards",
        "exchangePurchasesAndSales",
        "otcPurchasesAndSales",
        "otherHoldings",
        "currentYearAdditionalAllocation",
        "currentYearCancellation",
      ],
      disclaimer: "공개 원자료만 사용한 조달판단용 추정치이며 공식 보유잔액 또는 최종 이행부족량이 아님",
    };

    displayRows = normalizeDisplayRows(rows);
    initializeHistorySearch();
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
      const metrics = {};
      const coverage = {};
      DISPLAY_KEYS.forEach((key) => {
        const merged = mergeMetricValues(group, key);
        metrics[key] = merged.value;
        coverage[key] = {
          completeCount: merged.completeCount,
          totalCount: merged.totalCount,
        };
      });
      const rateValues = group
        .map((row) => metricValue(row, "paidAllocationRate"))
        .filter((value) => typeof value === "number");
      metrics.paidAllocationRate = rateValues.length === group.length
        && new Set(rateValues).size === 1
        ? rateValues[0]
        : null;
      coverage.paidAllocationRate = {
        completeCount: rateValues.length,
        totalCount: group.length,
      };
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
        aliases,
        searchText: aliases.join(" ").toLocaleLowerCase("ko-KR"),
        normalizedSearchKeys: aliases.map((name) => normalizedCompanyKey(name)),
        allocationTypes: group.map((row) => normalizedAllocationType(row)),
        metrics,
        coverage,
        calculationGroup: group.find((row) => row?.calculationGroup)?.calculationGroup || null,
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
      const transformed = recalculateProcurementEstimate(payload);
      const headers = new Headers(response.headers);
      headers.set("content-type", "application/json; charset=utf-8");
      headers.delete("content-length");
      return new Response(JSON.stringify(transformed), {
        status: response.status,
        statusText: response.statusText,
        headers,
      });
    } catch (error) {
      console.error("KRX 연간 유상할당 차감 산식 적용 실패", error);
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
      const balance = row.metrics.finalBalance;
      const statusMatch = !status
        || (status === "unavailable" && balance === null)
        || (status === "surplus" && typeof balance === "number" && balance > 0)
        || (status === "shortage" && typeof balance === "number" && balance < 0)
        || (status === "balanced" && balance === 0);
      return row.year === year
        && (!sector || row.sector === sector)
        && (!industry || row.industry === industry)
        && (!query || row.searchText.includes(query))
        && statusMatch;
    });
  }

  function metricSummary(rows, key) {
    const numericRows = rows.filter((row) => typeof row.metrics[key] === "number");
    if (!numericRows.length) return { value: null, partial: false, completeCount: 0, totalCount: rows.length };
    return {
      value: numericRows.reduce((sum, row) => sum + row.metrics[key], 0),
      partial: numericRows.length < rows.length
        || numericRows.some((row) => row.coverage[key]?.completeCount < row.coverage[key]?.totalCount),
      completeCount: numericRows.length,
      totalCount: rows.length,
    };
  }

  function allocationSummary(rows) {
    const rateRows = rows.filter((row) => typeof row.metrics.paidAllocationRate === "number");
    const rates = new Set(rateRows.map((row) => row.metrics.paidAllocationRate));
    const paid = metricSummary(rows, "estimatedPaidAllocation");
    let label = "확인 필요";
    let className = "unknown";
    if (rateRows.length === rows.length && rates.size === 1) {
      const rate = [...rates][0];
      if (rate === 0) {
        label = "전량 무상";
        className = "free";
      } else {
        label = `유상 ${Math.round(rate * 100)}%`;
        className = "paid";
      }
    } else if (rateRows.length) {
      label = "혼합";
      className = "mixed";
    }
    return { label, className, paid };
  }

  function allocationMarkup(rows) {
    if (!rows.length) return '<span class="annual-value-missing">—</span>';
    const summary = allocationSummary(rows);
    const paidText = typeof summary.paid.value === "number"
      ? `유상분 추정 ${numberFormat.format(Math.round(summary.paid.value))}톤`
      : "유상분 계산 불가";
    const partial = summary.paid.partial
      ? '<span class="annual-partial-badge" title="일부 업체만 계산되었습니다.">부분</span>'
      : "";
    return [
      '<span class="annual-allocation-method">',
      `<b class="annual-allocation-badge ${summary.className}">${escapeHtml(summary.label)}</b>`,
      `<small title="${escapeHtml(paidText)}">${escapeHtml(paidText)}</small>`,
      partial,
      "</span>",
    ].join("");
  }

  function balanceMarkup(summary) {
    if (typeof summary.value !== "number") {
      return '<span class="annual-value-missing" aria-label="연초 추정 과부족량 계산 불가">—</span>';
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
      ? '<span class="annual-partial-badge" title="일부 업체의 추정치만 합산했습니다.">부분</span>'
      : "";
    return [
      `<span class="annual-balance-value ${className}" aria-label="${status} ${escapeHtml(formatted)}톤">`,
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
      (right.metrics.previousVerifiedEmissions ?? Number.NEGATIVE_INFINITY)
      - (left.metrics.previousVerifiedEmissions ?? Number.NEGATIVE_INFINITY)
      || collator.compare(left.companyId, right.companyId)
    )));
    return queues;
  }

  function normalizeTableColspans() {
    document.querySelectorAll("#annualRows td[colspan]").forEach((cell) => { cell.colSpan = 11; });
  }

  function decorateAnnualTable() {
    normalizeTableColspans();
    const filtered = filteredDisplayRows();
    const companyQueues = makeCompanyQueues(filtered);
    let currentSector = "";
    let currentIndustry = "";

    document.querySelectorAll("#annualRows tr").forEach((tableRow) => {
      const allocationCell = tableRow.querySelector("td.annual-allocation-type-cell");
      const balanceCell = tableRow.querySelector("td.annual-balance-column");
      if (!allocationCell || !balanceCell) return;
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
        sourceRows = filtered.filter((row) => row.sector === currentSector && row.industry === currentIndustry);
      } else if (tableRow.classList.contains("annual-row-company")) {
        const key = `${currentSector}\u0001${currentIndustry}\u0001${label}`;
        const queue = companyQueues.get(key) || [];
        const companyRow = queue.shift();
        sourceRows = companyRow ? [companyRow] : [];
      }

      allocationCell.innerHTML = allocationMarkup(sourceRows);
      balanceCell.innerHTML = balanceMarkup(metricSummary(sourceRows, "finalBalance"));
    });
  }

  function refreshTable() {
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(decorateAnnualTable);
    });
  }

  function initTableObserver() {
    decorateAnnualTable();
    const tbody = byId("annualRows");
    if (!tbody) return;
    const observer = new MutationObserver(refreshTable);
    observer.observe(tbody, { childList: true });
  }

  function formatMetric(value) {
    return typeof value === "number" ? numberFormat.format(Math.round(value)) : "—";
  }

  function formatRate(value) {
    return typeof value === "number" ? `${Math.round(value * 100)}%` : "—";
  }

  function formatBalance(value) {
    if (typeof value !== "number") return "—";
    const rounded = Math.round(value);
    if (rounded > 0) return `+${numberFormat.format(rounded)}`;
    if (rounded < 0) return `−${numberFormat.format(Math.abs(rounded))}`;
    return "0";
  }

  function balanceClass(value) {
    if (typeof value !== "number") return "";
    if (value > 0) return "annual-history-positive";
    if (value < 0) return "annual-history-negative";
    return "annual-history-neutral";
  }

  function historyRowsForCompany(companyId) {
    return displayRows
      .filter((row) => row.companyId === companyId)
      .sort((left, right) => left.year - right.year || collator.compare(left.industry, right.industry));
  }

  function resolveHistoryCompany(query) {
    const normalized = normalizedCompanyKey(query);
    if (!normalized) return { kind: "empty" };
    const exactIds = new Set(displayRows
      .filter((row) => row.normalizedSearchKeys.includes(normalized))
      .map((row) => row.companyId));
    if (exactIds.size === 1) return { kind: "match", companyId: [...exactIds][0] };
    const partialIds = new Set(displayRows
      .filter((row) => row.normalizedSearchKeys.some((key) => key.includes(normalized)))
      .map((row) => row.companyId));
    if (partialIds.size === 1) return { kind: "match", companyId: [...partialIds][0] };
    if (partialIds.size > 1) return { kind: "ambiguous", count: partialIds.size };
    return { kind: "none" };
  }

  function renderHistory(companyId) {
    const rows = historyRowsForCompany(companyId);
    const tbody = byId("annualHistoryRows");
    const status = byId("annualHistoryStatus");
    if (!tbody || !status) return;
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="12">연도별 자료를 찾지 못했습니다.</td></tr>';
      status.textContent = "조회 가능한 연도별 자료가 없습니다.";
      return;
    }

    tbody.innerHTML = rows.map((row) => {
      const metrics = row.metrics;
      const balance = metrics.finalBalance;
      const allocationLabel = formatRate(metrics.paidAllocationRate) === "0%"
        ? "전량 무상"
        : formatRate(metrics.paidAllocationRate) === "—"
          ? "확인 필요"
          : `유상 ${formatRate(metrics.paidAllocationRate)}`;
      return [
        "<tr>",
        `<th scope="row">${row.year}년</th>`,
        `<td class="annual-history-company">${escapeHtml(row.companyName)}</td>`,
        `<td>${escapeHtml(row.sector)}</td>`,
        `<td>${escapeHtml(row.industry)}</td>`,
        `<td>${formatMetric(metrics.preAllocation)}</td>`,
        `<td>${escapeHtml(allocationLabel)}</td>`,
        `<td class="annual-history-paid">${formatMetric(metrics.estimatedPaidAllocation)}</td>`,
        `<td class="annual-history-applied">${formatMetric(metrics.estimatedFreeAllocation)}</td>`,
        `<td>${formatMetric(metrics.previousCarryover)}</td>`,
        `<td>${formatMetric(metrics.previousBorrow)}</td>`,
        `<td>${formatMetric(metrics.previousVerifiedEmissions)}</td>`,
        `<td class="annual-history-balance ${balanceClass(balance)}">${formatBalance(balance)}</td>`,
        "</tr>",
      ].join("");
    }).join("");

    const first = rows[0];
    const last = rows[rows.length - 1];
    status.textContent = `${last.companyName} · ${first.year}~${last.year}년 · 유상할당 차감 연초 추정 ${rows.length}개 연도`;
  }

  function runHistorySearch() {
    const input = byId("annualHistoryCompanySearch");
    const tbody = byId("annualHistoryRows");
    const status = byId("annualHistoryStatus");
    if (!input || !tbody || !status) return;
    const result = resolveHistoryCompany(input.value);
    if (result.kind === "empty") {
      status.textContent = "업체명을 입력하세요.";
      tbody.innerHTML = '<tr><td colspan="12">업체를 선택하면 연도별 히스토리가 표시됩니다.</td></tr>';
    } else if (result.kind === "ambiguous") {
      status.textContent = `유사한 업체가 ${result.count}개입니다. 자동완성 목록에서 정확한 업체명을 선택하세요.`;
      tbody.innerHTML = '<tr><td colspan="12">정확한 업체명을 선택해 주세요.</td></tr>';
    } else if (result.kind === "none") {
      status.textContent = "일치하는 업체를 찾지 못했습니다.";
      tbody.innerHTML = '<tr><td colspan="12">검색 결과가 없습니다.</td></tr>';
    } else {
      renderHistory(result.companyId);
    }
  }

  function initializeHistorySearch() {
    const input = byId("annualHistoryCompanySearch");
    const searchButton = byId("annualHistorySearchButton");
    const clearButton = byId("annualHistoryClearButton");
    const datalist = byId("annualHistoryCompanyOptions");
    if (!input || !searchButton || !clearButton || !datalist) return;

    const latestByCompany = new Map();
    displayRows.forEach((row) => {
      const current = latestByCompany.get(row.companyId);
      if (!current || row.year > current.year) latestByCompany.set(row.companyId, row);
    });
    const names = [...latestByCompany.values()]
      .map((row) => row.companyName)
      .sort((left, right) => collator.compare(left, right));
    datalist.replaceChildren(...names.map((name) => {
      const option = document.createElement("option");
      option.value = name;
      return option;
    }));
    input.disabled = false;
    searchButton.disabled = false;
    clearButton.disabled = false;

    if (historyBound) return;
    historyBound = true;
    searchButton.addEventListener("click", () => window.setTimeout(runHistorySearch, 0));
    input.addEventListener("change", () => window.setTimeout(runHistorySearch, 0));
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      window.setTimeout(runHistorySearch, 0);
    });
    clearButton.addEventListener("click", () => window.setTimeout(() => {
      input.value = "";
      byId("annualHistoryStatus").textContent = "업체명을 입력하세요.";
      byId("annualHistoryRows").innerHTML = '<tr><td colspan="12">업체를 선택하면 연도별 히스토리가 표시됩니다.</td></tr>';
      input.focus();
    }, 0));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initTableObserver, { once: true });
  } else {
    initTableObserver();
  }
})();
