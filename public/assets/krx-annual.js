"use strict";

(() => {
  const DATA_URL = "data/krx-annual.json";
  const DISPLAY_YEARS = Array.from({ length: 11 }, (_, index) => 2015 + index);
  const DEFAULT_YEAR = 2025;
  const METRIC_KEYS = [
    "preAllocation",
    "additionalAllocation",
    "cancellation",
    "adjustedAllocation",
    "verifiedEmissions",
    "carryover",
    "borrow",
    "offsetIssued",
    "finalBalance",
  ];
  const METRIC_ALIASES = {
    preAllocation: ["preAllocation", "initialAllocation"],
    additionalAllocation: ["additionalAllocation", "additional"],
    cancellation: ["cancellation", "allocationCancellation", "cancelledAllocation"],
    adjustedAllocation: ["adjustedAllocation", "netAllocation"],
    verifiedEmissions: ["verifiedEmissions", "certifiedEmissions", "emissions"],
    carryover: ["carryover", "carriedOver"],
    borrow: ["borrow", "borrowed"],
    offsetIssued: ["offsetIssued", "offsetIssuance"],
    finalBalance: ["finalBalance", "complianceBalance"],
  };
  const numberFormat = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });
  const collator = new Intl.Collator("ko-KR", { numeric: true, sensitivity: "base" });
  const state = {
    payload: null,
    rows: [],
    invalidRowCount: 0,
    year: DEFAULT_YEAR,
    sector: "",
    industry: "",
    query: "",
    balanceStatus: "",
    sectorOrder: [],
    expanded: new Set(["root"]),
    lastTree: null,
  };

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

  function isExplicitlyMissing(raw, key) {
    const missing = raw?.missing;
    const aliases = METRIC_ALIASES[key] || [key];
    if (Array.isArray(missing)) return aliases.some((alias) => missing.includes(alias));
    if (missing && typeof missing === "object") return aliases.some((alias) => missing[alias] === true);
    return false;
  }

  function readMetric(raw, key) {
    if (isExplicitlyMissing(raw, key)) return null;
    const aliases = METRIC_ALIASES[key] || [key];
    const metrics = raw?.metrics && typeof raw.metrics === "object" ? raw.metrics : {};
    for (const alias of aliases) {
      if (hasOwn(metrics, alias)) return numberOrNull(metrics[alias]);
    }
    for (const alias of aliases) {
      if (hasOwn(raw, alias)) return numberOrNull(raw[alias]);
    }
    return null;
  }

  function normalizeAllocationType(value) {
    if (value === true) return "paid";
    if (value === false) return "free";
    const normalized = cleanText(value).toLowerCase().replaceAll(" ", "");
    if (["y", "yes", "paid", "auction", "유상", "유상할당"].includes(normalized)) return "paid";
    if (["n", "no", "free", "무상", "무상할당"].includes(normalized)) return "free";
    if (["mixed", "혼합", "유상/무상", "유상·무상"].includes(normalized)) return "mixed";
    return null;
  }

  function normalizeAliases(raw) {
    const candidates = [
      raw?.aliases,
      raw?.companyAliases,
      raw?.previousNames,
      raw?.relatedCompanyNames,
    ].flatMap((value) => {
      if (Array.isArray(value)) return value;
      return value ? [value] : [];
    });
    return candidates.map((value) => cleanText(value)).filter(Boolean);
  }

  function normalizeRelations(raw) {
    if (!Array.isArray(raw?.entityRelations)) return [];
    return raw.entityRelations.map((relation) => {
      const sourceUrl = cleanText(relation?.sourceUrl);
      return {
        relationType: cleanText(relation?.relationType),
        effectiveDate: cleanText(relation?.effectiveDate),
        direction: relation?.direction === "from" ? "from" : relation?.direction === "to" ? "to" : "",
        counterpartName: cleanText(relation?.counterpartName),
        sourceTitle: cleanText(relation?.sourceTitle, "공식 근거"),
        sourceUrl: sourceUrl.startsWith("https://") ? sourceUrl : "",
      };
    }).filter((relation) => relation.relationType && relation.direction && relation.counterpartName);
  }

  function normalizeRow(raw, position) {
    if (!raw || typeof raw !== "object") return null;
    const year = Number.parseInt(raw.year ?? raw.complianceYear, 10);
    const companyName = cleanText(raw.companyName ?? raw.entityName ?? raw.name);
    if (!DISPLAY_YEARS.includes(year) || !companyName) return null;

    const metrics = {};
    METRIC_KEYS.forEach((key) => { metrics[key] = readMetric(raw, key); });
    if (metrics.adjustedAllocation === null) {
      const inputs = [metrics.preAllocation, metrics.additionalAllocation, metrics.cancellation];
      if (inputs.every((value) => typeof value === "number")) {
        metrics.adjustedAllocation = metrics.preAllocation + metrics.additionalAllocation - metrics.cancellation;
      }
    }
    if (metrics.finalBalance === null) {
      const inputs = [metrics.adjustedAllocation, metrics.borrow, metrics.verifiedEmissions, metrics.carryover];
      if (inputs.every((value) => typeof value === "number")) {
        metrics.finalBalance = metrics.adjustedAllocation + metrics.borrow - metrics.verifiedEmissions - metrics.carryover;
      }
    }

    const allocationType = normalizeAllocationType(
      raw.allocationType ?? raw.paidAllocation ?? raw.metrics?.allocationType,
    );
    const aliases = normalizeAliases(raw);
    const relations = normalizeRelations(raw);
    const sector = cleanText(raw.sector ?? raw.division, "미분류");
    const industry = cleanText(raw.industry ?? raw.businessType, "미분류");
    const companyId = cleanText(raw.companyId ?? raw.entityId, `${companyName}-${position}`);
    return {
      year,
      sector,
      industry,
      companyId,
      companyName,
      allocationType,
      relations,
      metrics,
      searchText: [companyName, ...aliases, ...relations.map((relation) => relation.counterpartName)]
        .join(" ")
        .toLocaleLowerCase("ko-KR"),
    };
  }

  function mergeMetricValues(rows, key) {
    const values = rows.map((row) => row.metrics[key]).filter((value) => typeof value === "number");
    return values.length ? values.reduce((sum, value) => sum + value, 0) : null;
  }

  function mergeAllocationTypes(rows) {
    const values = rows.map((row) => row.allocationType);
    const types = new Set(values.filter(Boolean));
    const hasMissing = values.some((value) => !value);
    if (types.has("mixed") || types.size > 1) return "mixed";
    if (types.size === 1 && hasMissing) return "partial";
    return types.size === 1 ? [...types][0] : null;
  }

  function coalesceRows(rows) {
    const grouped = new Map();
    rows.forEach((row) => {
      const key = [row.year, row.sector, row.industry, row.companyId].join("\u0001");
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(row);
    });
    return [...grouped.values()].map((group) => {
      if (group.length === 1) return group[0];
      const first = group[0];
      const metrics = {};
      METRIC_KEYS.forEach((key) => { metrics[key] = mergeMetricValues(group, key); });
      return {
        ...first,
        companyName: group.map((row) => row.companyName).sort((a, b) => collator.compare(a, b))[0],
        allocationType: mergeAllocationTypes(group),
        metrics,
        searchText: [...new Set(group.map((row) => row.searchText))].join(" "),
      };
    });
  }

  function rawRowsFromPayload(payload) {
    const source = payload?.rows ?? payload?.items ?? payload?.data ?? [];
    if (Array.isArray(source)) return source;
    if (source && typeof source === "object") return Object.values(source).flatMap((value) => Array.isArray(value) ? value : []);
    return [];
  }

  function normalizePayload(payload) {
    const rawRows = rawRowsFromPayload(payload);
    const normalized = [];
    let invalid = 0;
    rawRows.forEach((row, index) => {
      const result = normalizeRow(row, index);
      if (result) normalized.push(result);
      else invalid += 1;
    });
    state.invalidRowCount = invalid;
    return coalesceRows(normalized);
  }

  function readTotalEntry(entry) {
    if (entry === null || entry === undefined) return null;
    if (typeof entry !== "object") return numberOrNull(entry);
    for (const key of ["total", "totalQuantity", "officialTotal", "allowanceTotal", "value"]) {
      if (hasOwn(entry, key)) return numberOrNull(entry[key]);
    }
    return null;
  }

  function officialTotalForYear(year) {
    const sources = [
      state.payload?.meta?.totals,
      state.payload?.meta?.annualCaps,
      state.payload?.totals,
      state.payload?.meta?.annualTotals,
    ];
    for (const source of sources) {
      if (Array.isArray(source)) {
        const item = source.find((entry) => Number.parseInt(entry?.year, 10) === year);
        const value = readTotalEntry(item);
        if (value !== null) return value;
      } else if (source && typeof source === "object") {
        const entry = source[String(year)] ?? source[`${year}년`] ?? source[`${year}년 총수량`];
        const value = readTotalEntry(entry);
        if (value !== null) return value;
      }
    }
    return null;
  }

  function setStatus(kind, title, detail) {
    const alert = byId("annualStatusAlert");
    alert.setAttribute("role", kind === "error" ? "alert" : "status");
    alert.classList.toggle("error", kind === "error");
    byId("annualStatusTitle").textContent = title;
    byId("annualStatusDetail").textContent = detail;
    alert.hidden = false;
  }

  function clearStatus() {
    byId("annualStatusAlert").hidden = true;
  }

  function setControlsDisabled(disabled) {
    ["annualYear", "annualSector", "annualIndustry", "annualCompanySearch", "annualBalanceStatus", "annualResetFilters"]
      .forEach((id) => { byId(id).disabled = disabled; });
    if (disabled) byId("annualExpandAll").disabled = true;
  }

  function replaceSelectOptions(select, values, allLabel, selectedValue) {
    const fragment = document.createDocumentFragment();
    if (allLabel !== null) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = allLabel;
      fragment.append(option);
    }
    values.forEach((value) => {
      const option = document.createElement("option");
      option.value = String(value);
      option.textContent = allLabel === null ? `${value}년` : String(value);
      fragment.append(option);
    });
    select.replaceChildren(fragment);
    const validValues = new Set([...select.options].map((option) => option.value));
    select.value = validValues.has(String(selectedValue)) ? String(selectedValue) : "";
  }

  function renderYearOptions() {
    replaceSelectOptions(byId("annualYear"), DISPLAY_YEARS, null, state.year);
  }

  function rowsForSelectedYear() {
    return state.rows.filter((row) => row.year === state.year);
  }

  function updateSectorOptions() {
    const values = [...new Set(rowsForSelectedYear().map((row) => row.sector))].sort(compareSectors);
    replaceSelectOptions(byId("annualSector"), values, "전체 부문", state.sector);
    state.sector = byId("annualSector").value;
  }

  function updateIndustryOptions() {
    const values = [...new Set(rowsForSelectedYear()
      .filter((row) => !state.sector || row.sector === state.sector)
      .map((row) => row.industry))]
      .sort((a, b) => collator.compare(a, b));
    replaceSelectOptions(byId("annualIndustry"), values, "전체 업종", state.industry);
    state.industry = byId("annualIndustry").value;
  }

  function balanceMatches(row) {
    const value = row.metrics.finalBalance;
    if (!state.balanceStatus) return true;
    if (state.balanceStatus === "unavailable") return value === null;
    if (typeof value !== "number") return false;
    if (state.balanceStatus === "surplus") return value > 0;
    if (state.balanceStatus === "shortage") return value < 0;
    if (state.balanceStatus === "balanced") return value === 0;
    return true;
  }

  function filteredCompanyRows() {
    const query = state.query.toLocaleLowerCase("ko-KR");
    return rowsForSelectedYear().filter((row) => (
      (!state.sector || row.sector === state.sector)
      && (!state.industry || row.industry === state.industry)
      && (!query || row.searchText.includes(query))
      && balanceMatches(row)
    ));
  }

  function aggregateRows(rows) {
    const metrics = {};
    const coverage = {};
    METRIC_KEYS.forEach((key) => {
      const numeric = rows.map((row) => row.metrics[key]).filter((value) => typeof value === "number");
      metrics[key] = numeric.length ? numeric.reduce((sum, value) => sum + value, 0) : null;
      coverage[key] = { completeCount: numeric.length, totalCount: rows.length };
    });
    return { metrics, coverage, allocationType: mergeAllocationTypes(rows) };
  }

  function compareSectors(left, right) {
    const leftIndex = state.sectorOrder.indexOf(left);
    const rightIndex = state.sectorOrder.indexOf(right);
    const leftRank = leftIndex === -1 ? Number.MAX_SAFE_INTEGER : leftIndex;
    const rightRank = rightIndex === -1 ? Number.MAX_SAFE_INTEGER : rightIndex;
    return leftRank - rightRank || collator.compare(left, right);
  }

  function makeGroupNode(kind, key, label, depth, rows) {
    const aggregate = aggregateRows(rows);
    return {
      kind,
      key,
      label,
      depth,
      rows,
      metrics: aggregate.metrics,
      coverage: aggregate.coverage,
      allocationType: aggregate.allocationType,
      companyCount: new Set(rows.map((row) => row.companyId)).size,
      children: [],
    };
  }

  function buildTree(rows) {
    const root = makeGroupNode("total", "root", "업체 합계", 0, rows);
    const sectorNames = [...new Set(rows.map((row) => row.sector))].sort(compareSectors);
    root.children = sectorNames.map((sector) => {
      const sectorRows = rows.filter((row) => row.sector === sector);
      const sectorNode = makeGroupNode("sector", `sector:${sector}`, sector, 1, sectorRows);
      const industryNames = [...new Set(sectorRows.map((row) => row.industry))].sort((a, b) => collator.compare(a, b));
      sectorNode.children = industryNames.map((industry) => {
        const industryRows = sectorRows.filter((row) => row.industry === industry);
        const industryNode = makeGroupNode(
          "industry",
          `industry:${sector}:${industry}`,
          industry,
          2,
          industryRows,
        );
        industryNode.children = [...industryRows]
          .sort((a, b) => {
            const emissionsA = a.metrics.verifiedEmissions ?? Number.NEGATIVE_INFINITY;
            const emissionsB = b.metrics.verifiedEmissions ?? Number.NEGATIVE_INFINITY;
            return emissionsB - emissionsA || collator.compare(a.companyName, b.companyName);
          })
          .map((row) => ({
            kind: "company",
            key: `company:${row.year}:${row.sector}:${row.industry}:${row.companyId}`,
            label: row.companyName,
            depth: 3,
            rows: [row],
            metrics: row.metrics,
            coverage: Object.fromEntries(METRIC_KEYS.map((key) => [key, {
              completeCount: typeof row.metrics[key] === "number" ? 1 : 0,
              totalCount: 1,
            }])),
            allocationType: row.allocationType,
            relations: row.relations,
            companyCount: 1,
            children: [],
          }));
        return industryNode;
      });
      return sectorNode;
    });
    return root;
  }

  function expandableNodes(root) {
    const result = [];
    const visit = (node) => {
      if (node.children.length) result.push(node);
      node.children.forEach(visit);
    };
    visit(root);
    return result;
  }

  function flattenVisibleTree(root) {
    const result = [];
    const visit = (node) => {
      result.push(node);
      if (node.children.length && state.expanded.has(node.key)) node.children.forEach(visit);
    };
    visit(root);
    return result;
  }

  function expandFilteredBranches(root) {
    state.expanded.add("root");
    if (state.sector || state.industry || state.query || state.balanceStatus) {
      root.children.forEach((sector) => {
        state.expanded.add(sector.key);
        sector.children.forEach((industry) => state.expanded.add(industry.key));
      });
    }
  }

  function metricMarkup(node, key) {
    const value = node.metrics[key];
    if (typeof value !== "number") return '<span class="annual-value-missing" aria-label="자료 없음">—</span>';
    const formatted = numberFormat.format(Math.round(value));
    const coverage = node.coverage[key];
    const partial = node.kind !== "company" && coverage.completeCount < coverage.totalCount;
    if (!partial) return escapeHtml(formatted);
    const coverageLabel = `부분 집계, 자료 확인 ${coverage.completeCount}/${coverage.totalCount}개`;
    return [
      `<span class="annual-metric-value" aria-label="${escapeHtml(formatted)}톤, ${escapeHtml(coverageLabel)}">`,
      `<span aria-hidden="true">${escapeHtml(formatted)}</span>`,
      `<span class="annual-partial-badge" title="${escapeHtml(coverageLabel)}" aria-hidden="true">부분</span>`,
      "</span>",
    ].join("");
  }

  function allocationTypeMarkup(type) {
    const labels = {
      paid: ["유상", "paid"],
      free: ["무상", "free"],
      mixed: ["혼합", "mixed"],
      partial: ["일부 미상", "partial"],
    };
    if (!labels[type]) return '<span class="annual-value-missing" aria-label="유상여부 자료 없음">—</span>';
    const [label, className] = labels[type];
    return `<span class="annual-allocation-badge ${className}">${label}</span>`;
  }

  function balanceMarkup(node) {
    const value = node.metrics.finalBalance;
    if (typeof value !== "number") return '<span class="annual-value-missing" aria-label="과부족량 계산 불가">—</span>';
    const rounded = Math.round(value);
    const coverage = node.coverage.finalBalance;
    const partial = node.kind !== "company" && coverage.completeCount < coverage.totalCount;
    const status = partial ? "부분합" : rounded > 0 ? "과다" : rounded < 0 ? "부족" : "균형";
    const className = partial
      ? "annual-balance-partial"
      : rounded > 0 ? "annual-balance-positive" : rounded < 0 ? "annual-balance-negative" : "annual-balance-neutral";
    const formatted = rounded > 0
      ? `+${numberFormat.format(rounded)}`
      : rounded < 0 ? `−${numberFormat.format(Math.abs(rounded))}` : "0";
    const coverageText = partial ? `, 부분 집계, 계산 가능 ${coverage.completeCount}/${coverage.totalCount}개` : "";
    const badge = partial
      ? `<span class="annual-partial-badge" title="계산 가능 ${coverage.completeCount}/${coverage.totalCount}개">부분</span>`
      : "";
    return [
      `<span class="annual-balance-value ${className}" aria-label="${status} ${escapeHtml(formatted)}톤${escapeHtml(coverageText)}">`,
      `<small aria-hidden="true">${status}</small><b aria-hidden="true">${escapeHtml(formatted)}</b>`,
      badge,
      "</span>",
    ].join("");
  }

  function treeCellMarkup(node) {
    const kindLabels = {
      total: "전체 업체 합계",
      sector: "부문",
      industry: "업종",
      company: "업체",
    };
    const expanded = node.children.length ? state.expanded.has(node.key) : false;
    const toggle = node.children.length
      ? [
        `<button type="button" class="annual-tree-toggle" data-node-toggle="${escapeHtml(node.key)}"`,
        ` aria-expanded="${expanded}" aria-label="${escapeHtml(node.label)} 하위 항목 ${expanded ? "접기" : "펼치기"}"></button>`,
      ].join("")
      : '<span class="annual-tree-spacer" aria-hidden="true"></span>';
    const count = node.kind === "company" ? "" : `<span class="annual-tree-count">${node.companyCount}개</span>`;
    const relationBadge = relationBadgeMarkup(node);
    return [
      `<div class="annual-tree-content" style="--tree-depth:${node.depth}">`,
      toggle,
      `<span class="sr-only">${escapeHtml(kindLabels[node.kind] || "항목")}: </span>`,
      `<span class="annual-tree-label" title="${escapeHtml(node.label)}">${escapeHtml(node.label)}</span>`,
      relationBadge,
      count,
      "</div>",
    ].join("");
  }

  function relationBadgeMarkup(node) {
    if (node.kind !== "company" || !Array.isArray(node.relations) || !node.relations.length) return "";
    const relation = node.relations[0];
    const labels = relation.relationType === "흡수합병"
      ? { from: "합병 전", to: "합병 승계" }
      : relation.relationType === "인적분할"
        ? { from: "분할 존속", to: "분할 신설" }
        : { from: "관계 전", to: "관계 후" };
    const label = node.relations.length > 1 ? `승계 ${node.relations.length}건` : labels[relation.direction];
    const date = relation.effectiveDate ? relation.effectiveDate.replaceAll("-", ".") : "날짜 미상";
    const detail = `${date} ${relation.relationType} · 연관 업체 ${relation.counterpartName}`;
    if (!relation.sourceUrl) {
      return `<span class="annual-relation-badge" aria-label="${escapeHtml(detail)}">${escapeHtml(label)}</span>`;
    }
    return [
      `<a class="annual-relation-badge" href="${escapeHtml(relation.sourceUrl)}" target="_blank" rel="noopener noreferrer"`,
      ` aria-label="${escapeHtml(detail)}, ${escapeHtml(relation.sourceTitle)} 새 창">${escapeHtml(label)}</a>`,
    ].join("");
  }

  function rowMarkup(node) {
    const metricCells = [
      "preAllocation",
      "additionalAllocation",
      "cancellation",
      "adjustedAllocation",
      "verifiedEmissions",
      "carryover",
      "borrow",
      "offsetIssued",
    ].map((key) => {
      const className = key === "adjustedAllocation" ? ' class="annual-adjusted-column"' : "";
      return `<td${className}>${metricMarkup(node, key)}</td>`;
    }).join("");
    return [
      `<tr class="annual-row-${node.kind}">`,
      `<td class="annual-tree-cell" role="rowheader">${treeCellMarkup(node)}</td>`,
      `<td class="annual-allocation-type-cell">${allocationTypeMarkup(node.allocationType)}</td>`,
      metricCells,
      `<td class="annual-balance-column">${balanceMarkup(node)}</td>`,
      "</tr>",
    ].join("");
  }

  function renderOfficialTotal() {
    const total = officialTotalForYear(state.year);
    byId("annualOfficialTotal").textContent = total === null ? "—" : `${numberFormat.format(Math.round(total))}톤`;
    byId("annualOfficialTotalYear").textContent = total === null
      ? `${state.year}년 · 원자료 없음`
      : `${state.year}년 · 공식 총수량`;
  }

  function updateExpandButton(root) {
    const button = byId("annualExpandAll");
    const nodes = expandableNodes(root);
    const allExpanded = nodes.length > 0 && nodes.every((node) => state.expanded.has(node.key));
    button.disabled = nodes.length === 0;
    button.setAttribute("aria-pressed", String(allExpanded));
    button.textContent = allExpanded ? "계층 전체 접기" : "계층 전체 펼치기";
  }

  function renderTable({ expandForFilter = false } = {}) {
    clearStatus();
    renderOfficialTotal();
    const rows = filteredCompanyRows();
    const root = buildTree(rows);
    state.lastTree = root;
    if (expandForFilter) expandFilteredBranches(root);
    const visible = flattenVisibleTree(root);
    const tbody = byId("annualRows");
    const uniqueCompanies = new Set(rows.map((row) => row.companyId)).size;

    if (!rows.length) {
      tbody.innerHTML = '<tr class="annual-empty-row"><td colspan="11">선택한 조건에 해당하는 업체 자료가 없습니다.</td></tr>';
      byId("annualResultCount").textContent = `${state.year}년 · 업체 0개`;
      updateExpandButton(root);
      return;
    }

    tbody.innerHTML = visible.map(rowMarkup).join("");
    byId("annualResultCount").textContent = `${state.year}년 · 업체 ${numberFormat.format(uniqueCompanies)}개 · ${numberFormat.format(visible.length)}행 표시`;
    updateExpandButton(root);
  }

  function handleFilterChange({ hierarchyChanged = false } = {}) {
    if (hierarchyChanged) state.expanded = new Set(["root"]);
    renderTable({ expandForFilter: true });
  }

  function bindEvents() {
    byId("annualYear").addEventListener("change", (event) => {
      state.year = Number.parseInt(event.target.value, 10) || DEFAULT_YEAR;
      state.sector = "";
      state.industry = "";
      updateSectorOptions();
      updateIndustryOptions();
      handleFilterChange({ hierarchyChanged: true });
    });
    byId("annualSector").addEventListener("change", (event) => {
      state.sector = event.target.value;
      state.industry = "";
      updateIndustryOptions();
      handleFilterChange({ hierarchyChanged: true });
    });
    byId("annualIndustry").addEventListener("change", (event) => {
      state.industry = event.target.value;
      handleFilterChange({ hierarchyChanged: true });
    });
    byId("annualBalanceStatus").addEventListener("change", (event) => {
      state.balanceStatus = event.target.value;
      handleFilterChange({ hierarchyChanged: true });
    });

    let searchTimer = null;
    byId("annualCompanySearch").addEventListener("input", (event) => {
      window.clearTimeout(searchTimer);
      const value = cleanText(event.target.value).toLocaleLowerCase("ko-KR");
      searchTimer = window.setTimeout(() => {
        state.query = value;
        handleFilterChange({ hierarchyChanged: true });
      }, 300);
    });

    byId("annualResetFilters").addEventListener("click", () => {
      window.clearTimeout(searchTimer);
      searchTimer = null;
      state.year = DEFAULT_YEAR;
      state.sector = "";
      state.industry = "";
      state.query = "";
      state.balanceStatus = "";
      state.expanded = new Set(["root"]);
      byId("annualYear").value = String(DEFAULT_YEAR);
      byId("annualCompanySearch").value = "";
      byId("annualBalanceStatus").value = "";
      updateSectorOptions();
      updateIndustryOptions();
      renderTable();
    });

    byId("annualRows").addEventListener("click", (event) => {
      const button = event.target.closest("[data-node-toggle]");
      if (!button) return;
      const key = button.dataset.nodeToggle;
      if (state.expanded.has(key)) state.expanded.delete(key);
      else state.expanded.add(key);
      renderTable();
      const nextButton = [...byId("annualRows").querySelectorAll("[data-node-toggle]")]
        .find((candidate) => candidate.dataset.nodeToggle === key);
      nextButton?.focus();
    });

    byId("annualExpandAll").addEventListener("click", () => {
      if (!state.lastTree) return;
      const nodes = expandableNodes(state.lastTree);
      const allExpanded = nodes.length > 0 && nodes.every((node) => state.expanded.has(node.key));
      state.expanded = allExpanded ? new Set(["root"]) : new Set(nodes.map((node) => node.key));
      renderTable();
    });
  }

  async function loadAnnualData() {
    const response = await fetch(DATA_URL, { cache: "no-cache" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    if (!payload || typeof payload !== "object") throw new Error("JSON 형식 오류");
    state.payload = payload;
    state.sectorOrder = Array.isArray(payload?.classifications?.sectorOrder)
      ? payload.classifications.sectorOrder.map((value) => cleanText(value)).filter(Boolean)
      : [];
    state.rows = normalizePayload(payload);
    if (!state.rows.length) throw new Error("표시할 2015~2025년 업체 자료가 없습니다.");
  }

  async function init() {
    renderYearOptions();
    setControlsDisabled(true);
    bindEvents();
    try {
      await loadAnnualData();
      updateSectorOptions();
      updateIndustryOptions();
      renderTable();
      setControlsDisabled(false);
      if (state.invalidRowCount > 0) {
        setStatus("warning", "일부 자료 제외", `형식이 맞지 않거나 조회기간 밖인 ${numberFormat.format(state.invalidRowCount)}개 행을 제외했습니다.`);
      }
    } catch (error) {
      console.error("KRX 연간 업체현황 로딩 실패", error);
      setStatus("error", "연간 자료를 불러오지 못했습니다.", `data/krx-annual.json을 확인해 주세요. (${error.message})`);
      byId("annualOfficialTotal").textContent = "—";
      byId("annualOfficialTotalYear").textContent = "자료 연결 오류";
      byId("annualRows").innerHTML = '<tr class="annual-empty-row"><td colspan="11">연간 업체현황 자료를 표시할 수 없습니다.</td></tr>';
      byId("annualResultCount").textContent = "자료 확인 필요";
    } finally {
      byId("annualDashboard").setAttribute("aria-busy", "false");
    }
  }

  init();
})();
