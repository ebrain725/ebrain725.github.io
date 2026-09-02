"use strict";

(() => {
  const TARGET_DATA_PATH = "data/krx-annual.json";
  const originalFetch = window.fetch.bind(window);
  const numberFormat = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });
  const collator = new Intl.Collator("ko-KR", { numeric: true, sensitivity: "base" });
  const DISPLAY_START_YEAR = 2015;

  const CALCULATION_GROUPS = [
    {
      id: "posco-holdings-posco",
      companyId: "calculation-group-posco-holdings-posco",
      displayName: "포스코홀딩스 주식회사 + 주식회사 포스코(통합)",
      memberNames: [
        "포스코홀딩스 주식회사",
        "주식회사 포스코홀딩스",
        "포스코홀딩스(주)",
        "(주)포스코홀딩스",
        "주식회사 포스코",
        "포스코 주식회사",
        "(주)포스코",
        "포스코(주)",
      ],
    },
  ];

  const METRIC_ALIASES = {
    preAllocation: ["preAllocation", "initialAllocation"],
    additionalAllocation: ["additionalAllocation", "additional"],
    cancellation: ["cancellation", "allocationCancellation", "cancelledAllocation"],
    adjustedAllocation: ["adjustedAllocation", "netAllocation"],
    verifiedEmissions: ["verifiedEmissions", "certifiedEmissions", "emissions"],
    carryover: ["carryover", "carriedOver"],
    carryoverAllowance: ["carryoverAllowance"],
    carryoverOffset: ["carryoverOffset"],
    borrow: ["borrow", "borrowed"],
    offsetIssued: ["offsetIssued", "offsetIssuance"],
    appliedPreAllocation: ["appliedPreAllocation"],
    previousAdditionalAllocation: ["previousAdditionalAllocation"],
    previousCancellation: ["previousCancellation"],
    previousCarryover: ["previousCarryover"],
    previousBorrow: ["previousBorrow"],
    previousVerifiedEmissions: ["previousVerifiedEmissions"],
    finalBalance: ["finalBalance", "complianceBalance"],
    settledBalance: ["settledBalance"],
  };

  const GROUP_SUM_METRICS = [
    "preAllocation",
    "additionalAllocation",
    "cancellation",
    "verifiedEmissions",
    "carryover",
    "carryoverAllowance",
    "carryoverOffset",
    "borrow",
    "offsetIssued",
  ];

  const DISPLAY_METRICS = [
    "preAllocation",
    "additionalAllocation",
    "cancellation",
    "adjustedAllocation",
    "verifiedEmissions",
    "carryover",
    "borrow",
    "offsetIssued",
    "appliedPreAllocation",
    "previousAdditionalAllocation",
    "previousCancellation",
    "previousCarryover",
    "previousBorrow",
    "previousVerifiedEmissions",
    "finalBalance",
    "settledBalance",
  ];

  let displayRows = [];
  let historyBound = false;

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
    const aliases = METRIC_ALIASES[key] || [key];
    const metrics = row?.metrics && typeof row.metrics === "object" ? row.metrics : {};
    for (const candidate of aliases) {
      if (hasOwn(metrics, candidate)) return numberOrNull(metrics[candidate]);
    }
    for (const candidate of aliases) {
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

  function normalizedCompanyKey(value) {
    return cleanText(value)
      .normalize("NFKC")
      .replaceAll("㈜", "주식회사")
      .replace(/\(\s*주\s*\)/g, "주식회사")
      .replace(/\s+/g, "")
      .toLocaleLowerCase("ko-KR");
  }

  function groupMemberKeys(group) {
    return new Set(group.memberNames.map((name) => normalizedCompanyKey(name)));
  }

  function rowMatchesCalculationGroup(row, memberKeys) {
    return [rowCompanyName(row), ...normalizeAliases(row)]
      .some((name) => memberKeys.has(normalizedCompanyKey(name)));
  }

  function strictMetricSum(rows, key) {
    if (!rows.length) return null;
    const values = rows.map((row) => metricValue(row, key));
    if (!values.every((value) => typeof value === "number")) return null;
    return values.reduce((sum, value) => sum + value, 0);
  }

  function dependencyValue(values, names, signs) {
    if (!names.every((name) => typeof values[name] === "number")) return null;
    return names.reduce((sum, name, index) => sum + values[name] * signs[index], 0);
  }

  function chooseClassification(rows) {
    const ranked = [...rows].sort((left, right) => {
      const leftEmissions = metricValue(left, "verifiedEmissions") ?? Number.NEGATIVE_INFINITY;
      const rightEmissions = metricValue(right, "verifiedEmissions") ?? Number.NEGATIVE_INFINITY;
      const leftAllocation = metricValue(left, "preAllocation") ?? Number.NEGATIVE_INFINITY;
      const rightAllocation = metricValue(right, "preAllocation") ?? Number.NEGATIVE_INFINITY;
      return rightEmissions - leftEmissions
        || rightAllocation - leftAllocation
        || collator.compare(rowCompanyName(left), rowCompanyName(right));
    });
    return ranked[0] || {};
  }

  function mergeAllocationTypes(rows) {
    const normalized = rows.map((row) => {
      const value = cleanText(row?.allocationType ?? row?.paidAllocation ?? row?.metrics?.allocationType)
        .toLowerCase()
        .replaceAll(" ", "");
      if (["y", "yes", "paid", "auction", "유상", "유상할당"].includes(value)) return "Y";
      if (["n", "no", "free", "무상", "무상할당"].includes(value)) return "N";
      if (["mixed", "혼합", "유상/무상", "유상·무상"].includes(value)) return "mixed";
      return "";
    });
    const types = new Set(normalized.filter(Boolean));
    if (types.has("mixed") || types.size > 1) return "mixed";
    return types.size === 1 ? [...types][0] : null;
  }

  function uniqueObjects(values) {
    const seen = new Set();
    return values.filter((value) => {
      if (!value || typeof value !== "object") return false;
      const key = JSON.stringify(value);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function buildCalculationGroupRow(group, year, rows) {
    const classification = chooseClassification(rows);
    const metrics = {};
    GROUP_SUM_METRICS.forEach((key) => { metrics[key] = strictMetricSum(rows, key); });
    metrics.adjustedAllocation = dependencyValue(
      metrics,
      ["preAllocation", "additionalAllocation", "cancellation"],
      [1, 1, -1],
    );
    if (metrics.carryover === null) {
      metrics.carryover = dependencyValue(
        metrics,
        ["carryoverAllowance", "carryoverOffset"],
        [1, 1],
      );
    }
    metrics.finalBalance = null;

    const aliases = [...new Set([
      ...group.memberNames,
      ...rows.flatMap((row) => [rowCompanyName(row), ...normalizeAliases(row)]),
    ].filter(Boolean))];
    const sourceNames = [...new Set(rows.map((row) => rowCompanyName(row)).filter(Boolean))];
    const relatedNames = [...new Set([
      ...group.memberNames,
      ...rows.flatMap((row) => Array.isArray(row?.relatedCompanyNames) ? row.relatedCompanyNames : []),
    ].map((value) => cleanText(value)).filter(Boolean))];
    const rawClassifications = uniqueObjects(
      rows.flatMap((row) => Array.isArray(row?.rawClassifications) ? row.rawClassifications : []),
    );
    const entityRelations = uniqueObjects(
      rows.flatMap((row) => Array.isArray(row?.entityRelations) ? row.entityRelations : []),
    );
    const sources = [...new Set(rows.flatMap((row) => Array.isArray(row?.sources) ? row.sources : []))];
    const qualityFlags = [...new Set([
      "calculationGroupMerged",
      ...rows.flatMap((row) => Array.isArray(row?.qualityFlags) ? row.qualityFlags : []),
    ])];
    const missing = Object.fromEntries(
      Object.entries(metrics).map(([key, value]) => [key, typeof value !== "number"]),
    );

    return {
      year,
      sector: rowSector(classification),
      industry: rowIndustry(classification),
      companyId: group.companyId,
      companyName: group.displayName,
      aliases,
      relatedCompanyNames: relatedNames,
      entityRelations,
      rawClassifications,
      allocationType: mergeAllocationTypes(rows),
      metrics,
      missing,
      missingReason: {},
      qualityFlags,
      sources,
      calculationGroup: {
        id: group.id,
        memberNames: group.memberNames,
        sourceNames,
        sourceRowCount: rows.length,
      },
    };
  }

  function mergeCalculationGroups(sourceRows) {
    let remaining = [...sourceRows];
    const mergedRows = [];

    CALCULATION_GROUPS.forEach((group) => {
      const memberKeys = groupMemberKeys(group);
      const matched = remaining.filter((row) => rowMatchesCalculationGroup(row, memberKeys));
      remaining = remaining.filter((row) => !rowMatchesCalculationGroup(row, memberKeys));
      const byYear = new Map();
      matched.forEach((row) => {
        const year = rowYear(row);
        if (!Number.isFinite(year)) return;
        if (!byYear.has(year)) byYear.set(year, []);
        byYear.get(year).push(row);
      });
      byYear.forEach((rows, year) => mergedRows.push(buildCalculationGroupRow(group, year, rows)));
    });

    return [...remaining, ...mergedRows];
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

  function aggregateMetric(rows, key) {
    if (!Array.isArray(rows) || !rows.length) return null;
    const values = rows.map((row) => metricValue(row, key));
    if (!values.every((value) => typeof value === "number")) return null;
    return values.reduce((sum, value) => sum + value, 0);
  }

  function settledBalanceForRow(row) {
    const adjustedAllocation = metricValue(row, "adjustedAllocation");
    const borrow = metricValue(row, "borrow");
    const verifiedEmissions = metricValue(row, "verifiedEmissions");
    const carryover = metricValue(row, "carryover");
    const inputs = [adjustedAllocation, borrow, verifiedEmissions, carryover];
    if (!inputs.every((value) => typeof value === "number")) return null;
    return adjustedAllocation + borrow - verifiedEmissions - carryover;
  }

  function previousBasisForRow(row, rowsByExactKey, rowsByCompanyYear) {
    const year = rowYear(row);
    const companyId = rowCompanyId(row);
    if (!Number.isFinite(year) || !companyId) return null;

    const exactKey = `${year - 1}\u0001${companyId}\u0001${rowSector(row)}\u0001${rowIndustry(row)}`;
    const exactRows = rowsByExactKey.get(exactKey);
    if (exactRows?.length) return exactRows;

    const currentRows = rowsByCompanyYear.get(`${year}\u0001${companyId}`) || [];
    const previousRows = rowsByCompanyYear.get(`${year - 1}\u0001${companyId}`) || [];
    if (currentRows.length === 1 && previousRows.length) return previousRows;
    return null;
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

  function setCalculatedMetrics(row, components, reason = "") {
    row.metrics = row.metrics && typeof row.metrics === "object" ? { ...row.metrics } : {};
    Object.assign(row.metrics, components);
    row.annualStartBalanceCalculated = true;
    if (hasOwn(row.metrics, "complianceBalance")) {
      row.metrics.complianceBalance = components.finalBalance;
    }
    updateMissingState(row, typeof components.finalBalance !== "number", reason);
  }

  function recalculateAnnualMetrics(payload) {
    const sourceRows = rawRowsFromPayload(payload);
    const rows = mergeCalculationGroups(sourceRows);
    const rowsByExactKey = groupRows(rows, exactYearKey);
    const rowsByCompanyYear = groupRows(rows, companyYearKey);

    rows.forEach((row) => {
      const year = rowYear(row);
      const currentPreAllocation = metricValue(row, "preAllocation");
      row.metrics = row.metrics && typeof row.metrics === "object" ? { ...row.metrics } : {};
      row.metrics.settledBalance = settledBalanceForRow(row);
      const emptyComponents = {
        currentPreAllocation,
        previousAdditionalAllocation: null,
        previousCancellation: null,
        appliedPreAllocation: null,
        previousCarryover: null,
        previousBorrow: null,
        previousVerifiedEmissions: null,
        finalBalance: null,
      };

      if (!Number.isFinite(year) || year <= DISPLAY_START_YEAR) {
        setCalculatedMetrics(row, emptyComponents, "dependency:previous-year-outside-range");
        return;
      }
      if (typeof currentPreAllocation !== "number") {
        setCalculatedMetrics(row, emptyComponents, "dependency:current-pre-allocation");
        return;
      }

      const previousRows = previousBasisForRow(row, rowsByExactKey, rowsByCompanyYear);
      if (!previousRows) {
        setCalculatedMetrics(row, emptyComponents, "dependency:previous-year-company-row");
        return;
      }

      const previousAdditionalAllocation = aggregateMetric(previousRows, "additionalAllocation");
      const previousCancellation = aggregateMetric(previousRows, "cancellation");
      const previousCarryover = aggregateMetric(previousRows, "carryover");
      const previousBorrow = aggregateMetric(previousRows, "borrow");
      const previousVerifiedEmissions = aggregateMetric(previousRows, "verifiedEmissions");

      const components = {
        currentPreAllocation,
        previousAdditionalAllocation,
        previousCancellation,
        appliedPreAllocation: null,
        previousCarryover,
        previousBorrow,
        previousVerifiedEmissions,
        finalBalance: null,
      };

      if (
        typeof previousAdditionalAllocation !== "number"
        || typeof previousCancellation !== "number"
      ) {
        setCalculatedMetrics(
          row,
          components,
          "dependency:previous-year-allocation-adjustments",
        );
        return;
      }

      components.appliedPreAllocation = currentPreAllocation
        + previousAdditionalAllocation
        - previousCancellation;

      if (
        typeof previousCarryover !== "number"
        || typeof previousBorrow !== "number"
        || typeof previousVerifiedEmissions !== "number"
      ) {
        setCalculatedMetrics(
          row,
          components,
          "dependency:previous-year-compliance-metrics",
        );
        return;
      }

      // 연초 과부족량: 당해년도 사전할당을 전년도 추가·취소로 먼저 수정한 뒤,
      // 전년도 이월·차입과 전년도 인증배출량을 반영한다.
      components.finalBalance = components.appliedPreAllocation
        + previousCarryover
        - previousBorrow
        - previousVerifiedEmissions;
      setCalculatedMetrics(row, components);
    });

    payload.rows = rows;
    if (payload?.metrics?.fields?.finalBalance) {
      payload.metrics.fields.finalBalance = {
        ...payload.metrics.fields.finalBalance,
        label: "연초 과부족량",
        formula: "(currentPreAllocation + previousAdditionalAllocation - previousCancellation) + previousCarryover - previousBorrow - previousVerifiedEmissions",
        formulaKo: "(당해년도 사전할당량 + 전년도 추가할당량 - 전년도 할당취소량) + 전년도 이월량 - 전년도 차입량 - 전년도 인증배출량",
      };
    }
    if (payload?.metrics?.fields) {
      payload.metrics.fields.settledBalance = {
        label: "정산 과부족량",
        formula: "adjustedAllocation + borrow - verifiedEmissions - carryover",
        formulaKo: "해당 연도 조정할당량 + 해당 연도 차입량 - 해당 연도 인증배출량 - 해당 연도 이월량",
        nullRule: "null if any dependency is null",
      };
    }
    payload.balanceCalculation = {
      version: "4.1.0",
      label: "연초 과부족량",
      appliedPreAllocationFormula: "currentPreAllocation + previousAdditionalAllocation - previousCancellation",
      appliedPreAllocationFormulaKo: "당해년도 사전할당량 + 전년도 추가할당량 - 전년도 할당취소량",
      formula: "appliedPreAllocation + previousCarryover - previousBorrow - previousVerifiedEmissions",
      formulaKo: "반영 사전할당량 + 전년도 이월량 - 전년도 차입량 - 전년도 인증배출량",
      firstAvailableYear: 2016,
      calculationGroups: CALCULATION_GROUPS.map((group) => ({
        id: group.id,
        companyId: group.companyId,
        displayName: group.displayName,
        memberNames: group.memberNames,
      })),
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
      const names = group.map((row) => rowCompanyName(row)).sort((left, right) => collator.compare(left, right));
      const metrics = {};
      const coverage = {};
      DISPLAY_METRICS.forEach((key) => {
        const merged = mergeMetricValues(group, key);
        metrics[key] = merged.value;
        coverage[key] = { completeCount: merged.completeCount, totalCount: merged.totalCount };
      });
      const aliases = [...new Set(group.flatMap((row) => [
        rowCompanyName(row),
        ...normalizeAliases(row),
        ...relationNames(row),
      ]).filter(Boolean))];
      return {
        year: rowYear(group[0]),
        sector: rowSector(group[0]),
        industry: rowIndustry(group[0]),
        companyId: rowCompanyId(group[0]),
        companyName: names[0],
        aliases,
        searchText: aliases.join(" ").toLocaleLowerCase("ko-KR"),
        normalizedSearchKeys: aliases.map((name) => normalizedCompanyKey(name)),
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
      console.error("KRX 연간 연초 과부족량·업체통합 계산 실패", error);
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

  function metricSummary(rows, key) {
    const numericRows = rows.filter((row) => typeof row.metrics[key] === "number");
    if (!numericRows.length) return { value: null, partial: false };
    return {
      value: numericRows.reduce((sum, row) => sum + row.metrics[key], 0),
      partial: numericRows.length < rows.length
        || numericRows.some((row) => row.coverage[key]?.completeCount < row.coverage[key]?.totalCount),
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
      ? "당해년도 사전할당 + 전년도 추가할당 - 전년도 할당취소(일부 결측)"
      : "당해년도 사전할당 + 전년도 추가할당 - 전년도 할당취소";
    return span;
  }

  function balanceMarkup(summary) {
    if (typeof summary.value !== "number") {
      return '<span class="annual-value-missing" aria-label="연초 과부족량 계산 불가">—</span>';
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
      ? '<span class="annual-partial-badge" title="일부 업체의 연초 과부족량만 합산했습니다.">부분</span>'
      : "";
    return [
      `<span class="annual-balance-value ${className}" aria-label="연초 ${status} ${escapeHtml(formatted)}톤">`,
      `<small aria-hidden="true">${status}</small><b aria-hidden="true">${escapeHtml(formatted)}</b>`,
      badge,
      "</span>",
    ].join("");
  }

  function settledBalanceMarkup(summary) {
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

  function moveBalanceCellsNextToAllocationType() {
    document.querySelectorAll("#annualRows tr").forEach((row) => {
      const allocationCell = row.querySelector("td.annual-allocation-type-cell");
      const balanceCell = row.querySelector("td.annual-balance-column");
      if (allocationCell && balanceCell && allocationCell.nextElementSibling !== balanceCell) {
        allocationCell.after(balanceCell);
      }
    });
  }

  function decorateAnnualTable() {
    const filtered = filteredDisplayRows();
    const companyQueues = makeCompanyQueues(filtered);
    let currentSector = "";
    let currentIndustry = "";

    document.querySelectorAll("#annualRows tr").forEach((tableRow) => {
      const balanceCell = tableRow.querySelector("td.annual-balance-column");
      const preAllocationCell = balanceCell?.nextElementSibling;
      if (!balanceCell || !preAllocationCell) return;

      let settledCell = tableRow.querySelector("td.annual-settled-balance-column");
      if (!settledCell) {
        settledCell = document.createElement("td");
        settledCell.className = "annual-settled-balance-column";
        tableRow.append(settledCell);
      }

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
        if (companyRow?.calculationGroup) {
          tableRow.classList.add("annual-row-calculation-group");
          const labelElement = tableRow.querySelector(".annual-tree-label");
          if (labelElement && !tableRow.querySelector(".annual-calculation-group-badge")) {
            const badge = document.createElement("span");
            badge.className = "annual-calculation-group-badge";
            badge.textContent = "통합계산";
            badge.title = companyRow.calculationGroup.memberNames.join(" + ");
            labelElement.after(badge);
          }
        }
      }

      preAllocationCell.querySelector(".annual-applied-preallocation")?.remove();
      preAllocationCell.classList.add("annual-preallocation-with-applied");
      preAllocationCell.append(appliedMarkup(metricSummary(sourceRows, "appliedPreAllocation")));
      balanceCell.innerHTML = balanceMarkup(metricSummary(sourceRows, "finalBalance"));
      settledCell.innerHTML = settledBalanceMarkup(metricSummary(sourceRows, "settledBalance"));
    });
  }

  function refreshTableEnhancements() {
    normalizeAnnualTableColspans();
    moveBalanceCellsNextToAllocationType();
    decorateAnnualTable();
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

  function formatMetric(value) {
    return typeof value === "number" ? numberFormat.format(Math.round(value)) : "—";
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
    if (!normalized) return { kind: "empty", rows: [] };

    const exactIds = new Set(
      displayRows
        .filter((row) => row.normalizedSearchKeys.includes(normalized))
        .map((row) => row.companyId),
    );
    if (exactIds.size === 1) return { kind: "match", companyId: [...exactIds][0] };

    const partialIds = new Set(
      displayRows
        .filter((row) => row.normalizedSearchKeys.some((key) => key.includes(normalized)))
        .map((row) => row.companyId),
    );
    if (partialIds.size === 1) return { kind: "match", companyId: [...partialIds][0] };
    if (partialIds.size > 1) return { kind: "ambiguous", count: partialIds.size };
    return { kind: "none", rows: [] };
  }

  function renderHistory(companyId) {
    const rows = historyRowsForCompany(companyId);
    const tbody = document.getElementById("annualHistoryRows");
    const status = document.getElementById("annualHistoryStatus");
    if (!tbody || !status) return;

    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="12">연도별 자료를 찾지 못했습니다.</td></tr>';
      status.textContent = "조회 가능한 연도별 자료가 없습니다.";
      return;
    }

    tbody.innerHTML = rows.map((row) => {
      const metrics = row.metrics;
      const balance = metrics.finalBalance;
      return [
        "<tr>",
        `<th scope="row">${row.year}년</th>`,
        `<td class="annual-history-company">${escapeHtml(row.companyName)}</td>`,
        `<td>${escapeHtml(row.sector)}</td>`,
        `<td>${escapeHtml(row.industry)}</td>`,
        `<td>${formatMetric(metrics.preAllocation)}</td>`,
        `<td class="annual-history-applied">${formatMetric(metrics.appliedPreAllocation)}</td>`,
        `<td>${formatMetric(metrics.previousAdditionalAllocation)}</td>`,
        `<td>${formatMetric(metrics.previousCancellation)}</td>`,
        `<td>${formatMetric(metrics.previousCarryover)}</td>`,
        `<td>${formatMetric(metrics.previousBorrow)}</td>`,
        `<td>${formatMetric(metrics.previousVerifiedEmissions)}</td>`,
        `<td class="annual-history-balance ${balanceClass(balance)}">${formatBalance(balance)}</td>`,
        "</tr>",
      ].join("");
    }).join("");

    const first = rows[0];
    const last = rows[rows.length - 1];
    const mergedText = rows.some((row) => row.calculationGroup)
      ? " · 포스코홀딩스와 포스코 통합계산"
      : "";
    status.textContent = `${last.companyName} · ${first.year}~${last.year}년 · ${rows.length}개 연도${mergedText}`;
  }

  function runHistorySearch() {
    const input = document.getElementById("annualHistoryCompanySearch");
    const tbody = document.getElementById("annualHistoryRows");
    const status = document.getElementById("annualHistoryStatus");
    if (!input || !tbody || !status) return;

    const result = resolveHistoryCompany(input.value);
    if (result.kind === "empty") {
      status.textContent = "업체명을 입력하세요.";
      tbody.innerHTML = '<tr><td colspan="12">업체를 선택하면 연도별 히스토리가 표시됩니다.</td></tr>';
      return;
    }
    if (result.kind === "ambiguous") {
      status.textContent = `유사한 업체가 ${result.count}개입니다. 자동완성 목록에서 정확한 업체명을 선택하세요.`;
      tbody.innerHTML = '<tr><td colspan="12">정확한 업체명을 선택해 주세요.</td></tr>';
      return;
    }
    if (result.kind === "none") {
      status.textContent = "일치하는 업체를 찾지 못했습니다.";
      tbody.innerHTML = '<tr><td colspan="12">검색 결과가 없습니다.</td></tr>';
      return;
    }
    renderHistory(result.companyId);
  }

  function populateHistoryOptions() {
    const datalist = document.getElementById("annualHistoryCompanyOptions");
    const input = document.getElementById("annualHistoryCompanySearch");
    const searchButton = document.getElementById("annualHistorySearchButton");
    const clearButton = document.getElementById("annualHistoryClearButton");
    if (!datalist || !input || !searchButton || !clearButton) return;

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
  }

  function initializeHistorySearch() {
    populateHistoryOptions();
    if (historyBound) return;
    const input = document.getElementById("annualHistoryCompanySearch");
    const searchButton = document.getElementById("annualHistorySearchButton");
    const clearButton = document.getElementById("annualHistoryClearButton");
    const tbody = document.getElementById("annualHistoryRows");
    const status = document.getElementById("annualHistoryStatus");
    if (!input || !searchButton || !clearButton || !tbody || !status) return;

    historyBound = true;
    searchButton.addEventListener("click", runHistorySearch);
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      runHistorySearch();
    });
    input.addEventListener("change", runHistorySearch);
    clearButton.addEventListener("click", () => {
      input.value = "";
      status.textContent = "업체명을 입력하세요.";
      tbody.innerHTML = '<tr><td colspan="12">업체를 선택하면 연도별 히스토리가 표시됩니다.</td></tr>';
      input.focus();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initTableObserver, { once: true });
  } else {
    initTableObserver();
  }
})();
