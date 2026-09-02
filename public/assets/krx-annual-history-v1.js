"use strict";

(() => {
  const DATA_PATH = "data/krx-annual.json";
  const YEARS = Array.from({ length: 11 }, (_, index) => 2015 + index);
  const collator = new Intl.Collator("ko-KR", { numeric: true, sensitivity: "base" });
  const numberFormat = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });
  const METRICS = [
    ["preAllocation", "사전할당"],
    ["appliedPreAllocation", "반영 사전할당"],
    ["additionalAllocation", "추가할당"],
    ["cancellation", "할당취소"],
    ["verifiedEmissions", "인증배출량"],
    ["carryover", "이월량"],
    ["borrow", "차입량"],
    ["finalBalance", "연초 과부족량"],
  ];
  const ALIASES = {
    preAllocation: ["initialAllocation"],
    appliedPreAllocation: [],
    additionalAllocation: ["additional"],
    cancellation: ["allocationCancellation", "cancelledAllocation"],
    verifiedEmissions: ["certifiedEmissions", "emissions"],
    carryover: ["carriedOver"],
    borrow: ["borrowed"],
    finalBalance: ["complianceBalance"],
  };

  let rows = [];
  let companies = [];
  let ready = false;

  const byId = (id) => document.getElementById(id);
  const own = (object, key) => Object.prototype.hasOwnProperty.call(object || {}, key);
  const clean = (value, fallback = "") => {
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
    const parsed = Number(typeof value === "string" ? value.replaceAll(",", "").trim() : value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function searchKey(value) {
    return clean(value).normalize("NFKC").toLocaleLowerCase("ko-KR").replace(/[^0-9a-z가-힣]+/g, "");
  }

  function legalCore(value) {
    const helper = window.KrxAnnualCompanyGroups?.legalCore;
    if (typeof helper === "function") return helper(value);
    return clean(value)
      .normalize("NFKC")
      .toLocaleLowerCase("ko-KR")
      .replace(/주식회사|유한책임회사|유한회사|합자회사|합명회사/g, "")
      .replace(/\(\s*주\s*\)|㈜|\(\s*유\s*\)|\(\s*합\s*\)/g, "")
      .replace(/[^0-9a-z가-힣]+/g, "");
  }

  function uniqueTexts(values) {
    const seen = new Set();
    const result = [];
    values.flatMap((value) => Array.isArray(value) ? value : value ? [value] : []).forEach((value) => {
      const text = clean(value);
      const key = searchKey(text);
      if (!text || !key || seen.has(key)) return;
      seen.add(key);
      result.push(text);
    });
    return result;
  }

  function rowsFrom(payload) {
    const source = payload?.rows ?? payload?.items ?? payload?.data ?? [];
    if (Array.isArray(source)) return source;
    if (source && typeof source === "object") {
      return Object.values(source).flatMap((value) => Array.isArray(value) ? value : []);
    }
    return [];
  }

  const yearOf = (row) => Number.parseInt(row?.year ?? row?.complianceYear, 10);
  const companyIdOf = (row, index = 0) => clean(row?.companyId ?? row?.entityId, `${companyNameOf(row)}-${index}`);
  const companyNameOf = (row) => clean(row?.companyName ?? row?.entityName ?? row?.name);
  const sectorOf = (row) => clean(row?.sector ?? row?.division, "미분류");
  const industryOf = (row) => clean(row?.industry ?? row?.businessType, "미분류");

  function aliasesOf(row) {
    return uniqueTexts([
      row?.aliases,
      row?.companyAliases,
      row?.previousNames,
      row?.relatedCompanyNames,
      Array.isArray(row?.entityRelations)
        ? row.entityRelations.map((relation) => relation?.counterpartName)
        : [],
    ]);
  }

  function metric(row, key) {
    const metrics = row?.metrics && typeof row.metrics === "object" ? row.metrics : {};
    for (const candidate of [key, ...(ALIASES[key] || [])]) {
      if (own(metrics, candidate)) return numberOrNull(metrics[candidate]);
    }
    for (const candidate of [key, ...(ALIASES[key] || [])]) {
      if (own(row, candidate)) return numberOrNull(row[candidate]);
    }
    return null;
  }

  function normalizeRows(sourceRows) {
    const grouped = new Map();
    sourceRows.forEach((row, index) => {
      const year = yearOf(row);
      const name = companyNameOf(row);
      if (!Number.isFinite(year) || !name) return;
      const normalized = {
        year,
        companyId: companyIdOf(row, index),
        companyName: name,
        sector: sectorOf(row),
        industry: industryOf(row),
        aliases: uniqueTexts([name, aliasesOf(row)]),
        calculationGroup: row?.calculationGroup || null,
        metrics: Object.fromEntries(METRICS.map(([key]) => [key, metric(row, key)])),
      };
      const groupKey = `${normalized.year}\u0001${normalized.companyId}\u0001${normalized.sector}\u0001${normalized.industry}`;
      if (!grouped.has(groupKey)) grouped.set(groupKey, []);
      grouped.get(groupKey).push(normalized);
    });

    return [...grouped.values()].map((items) => {
      const first = items[0];
      const metrics = {};
      METRICS.forEach(([key]) => {
        const values = items.map((row) => row.metrics[key]).filter((value) => typeof value === "number");
        metrics[key] = values.length ? values.reduce((sum, value) => sum + value, 0) : null;
      });
      return {
        ...first,
        aliases: uniqueTexts(items.flatMap((row) => row.aliases)),
        metrics,
      };
    });
  }

  function buildCompanies(normalizedRows) {
    const grouped = new Map();
    normalizedRows.forEach((row) => {
      if (!grouped.has(row.companyId)) grouped.set(row.companyId, []);
      grouped.get(row.companyId).push(row);
    });
    return [...grouped.entries()].map(([companyId, companyRows]) => {
      const sorted = [...companyRows].sort((left, right) => right.year - left.year);
      const latest = sorted[0];
      const aliases = uniqueTexts(companyRows.flatMap((row) => row.aliases));
      return {
        companyId,
        companyName: latest.companyName,
        aliases,
        rows: companyRows,
        latest,
        rawKeys: new Set(aliases.map(searchKey).filter(Boolean)),
        coreKeys: new Set(aliases.map(legalCore).filter(Boolean)),
      };
    }).sort((left, right) => collator.compare(left.companyName, right.companyName));
  }

  function aggregate(sourceRows) {
    const metrics = {};
    const coverage = {};
    METRICS.forEach(([key]) => {
      const values = sourceRows.map((row) => row.metrics[key]).filter((value) => typeof value === "number");
      metrics[key] = values.length ? values.reduce((sum, value) => sum + value, 0) : null;
      coverage[key] = { complete: values.length, total: sourceRows.length };
    });
    return { metrics, coverage, count: new Set(sourceRows.map((row) => row.companyId)).size };
  }

  function classificationForYear(company, year) {
    const exact = company.rows
      .filter((row) => row.year === year)
      .sort((left, right) => (right.metrics.verifiedEmissions ?? -Infinity) - (left.metrics.verifiedEmissions ?? -Infinity))[0];
    if (exact) return exact;
    const prior = company.rows.filter((row) => row.year < year).sort((left, right) => right.year - left.year)[0];
    return prior || company.latest;
  }

  function companyAggregate(company, year) {
    return aggregate(company.rows.filter((row) => row.year === year));
  }

  function groupAggregate(company, year, mode) {
    const classification = classificationForYear(company, year);
    if (!classification) return { label: "미분류", aggregate: aggregate([]) };
    const label = mode === "industry" ? classification.industry : classification.sector;
    const source = rows.filter((row) => row.year === year && (
      mode === "industry" ? row.industry === label : row.sector === label
    ));
    return { label, aggregate: aggregate(source) };
  }

  function metricMarkup(value, key, coverage) {
    if (typeof value !== "number") return '<span class="annual-history-missing">—</span>';
    const rounded = Math.round(value);
    const sign = key === "finalBalance" && rounded > 0 ? "+" : key === "finalBalance" && rounded < 0 ? "−" : "";
    const absolute = key === "finalBalance" && rounded < 0 ? Math.abs(rounded) : rounded;
    const partial = coverage && coverage.total > 0 && coverage.complete < coverage.total;
    const title = partial ? `부분 집계 ${coverage.complete}/${coverage.total}개` : "";
    return `<span class="annual-history-number${key === "finalBalance" ? " balance" : ""}${partial ? " partial" : ""}"${title ? ` title="${escapeHtml(title)}"` : ""}>${sign}${numberFormat.format(absolute)}${partial ? '<small>부분</small>' : ""}</span>`;
  }

  function tableMarkup(title, description, company, mode) {
    const rowsMarkup = YEARS.map((year) => {
      const classification = classificationForYear(company, year);
      let label = company.companyName;
      let annual = companyAggregate(company, year);
      if (mode === "industry" || mode === "sector") {
        const result = groupAggregate(company, year, mode);
        label = result.label;
        annual = result.aggregate;
      }
      const metricCells = METRICS.map(([key]) => (
        `<td>${metricMarkup(annual.metrics[key], key, annual.coverage[key])}</td>`
      )).join("");
      const sourceLabel = mode === "company"
        ? (classification ? `${classification.sector} / ${classification.industry}` : "자료 없음")
        : `${label}${annual.count ? ` · ${numberFormat.format(annual.count)}개 업체` : ""}`;
      return `<tr><th scope="row">${year}</th><td class="annual-history-basis" title="${escapeHtml(sourceLabel)}">${escapeHtml(sourceLabel)}</td>${metricCells}</tr>`;
    }).join("");

    const headers = METRICS.map(([, label]) => `<th scope="col">${escapeHtml(label)}</th>`).join("");
    return [
      '<article class="annual-history-card">',
      `<div class="annual-history-card-head"><h3>${escapeHtml(title)}</h3><p>${escapeHtml(description)}</p></div>`,
      '<div class="annual-history-table-wrap" role="region" tabindex="0">',
      '<table class="annual-history-table">',
      `<thead><tr><th scope="col">연도</th><th scope="col">기준</th>${headers}</tr></thead>`,
      `<tbody>${rowsMarkup}</tbody>`,
      '</table></div></article>',
    ].join("");
  }

  function findCompany(query) {
    const raw = searchKey(query);
    const core = legalCore(query);
    if (!raw) return null;
    const exact = companies.filter((company) => company.rawKeys.has(raw) || (core && company.coreKeys.has(core)));
    if (exact.length === 1) return exact[0];
    if (exact.length > 1) return exact.sort((left, right) => right.latest.year - left.latest.year)[0];
    const starts = companies.filter((company) => (
      [...company.rawKeys].some((key) => key.startsWith(raw))
      || (core && [...company.coreKeys].some((key) => key.startsWith(core)))
    ));
    if (starts.length === 1) return starts[0];
    const includes = companies.filter((company) => (
      [...company.rawKeys].some((key) => key.includes(raw))
      || (core && [...company.coreKeys].some((key) => key.includes(core)))
    ));
    return includes.length === 1 ? includes[0] : null;
  }

  function render(company) {
    const summary = byId("annualHistorySummary");
    const tables = byId("annualHistoryTables");
    const empty = byId("annualHistoryEmpty");
    const results = byId("annualHistoryResults");
    if (!summary || !tables || !empty || !results) return;

    const latest = company.latest;
    const memberNames = latest.calculationGroup?.memberNames || [];
    summary.innerHTML = [
      `<strong>${escapeHtml(company.companyName)}</strong>`,
      `<span>최근 분류 ${escapeHtml(latest.sector)} / ${escapeHtml(latest.industry)}</span>`,
      memberNames.length ? `<small>합산 대상: ${escapeHtml(memberNames.join(" + "))}</small>` : "",
    ].join("");
    tables.innerHTML = [
      tableMarkup("업체 히스토리", "선택 업체의 연도별 할당·배출·연초 과부족 현황", company, "company"),
      tableMarkup("업종 히스토리", "선택 업체가 속한 업종 전체의 연도별 합계", company, "industry"),
      tableMarkup("부문 히스토리", "선택 업체가 속한 부문 전체의 연도별 합계", company, "sector"),
    ].join("");
    empty.hidden = true;
    results.hidden = false;
    summary.hidden = false;
    tables.hidden = false;
    setStatus(`${company.companyName}의 연도별 히스토리를 표시했습니다.`);
  }

  function setStatus(message, kind = "") {
    const status = byId("annualHistoryStatus");
    if (!status) return;
    status.textContent = message;
    status.className = `annual-history-status${kind ? ` ${kind}` : ""}`;
  }

  function executeSearch() {
    if (!ready) return;
    const input = byId("annualHistoryCompanySearch");
    const query = clean(input?.value);
    if (!query) {
      setStatus("업체명을 입력해 주세요.", "warning");
      input?.focus();
      return;
    }
    const company = findCompany(query);
    if (!company) {
      setStatus("일치하는 업체가 없거나 검색어가 여러 업체와 중복됩니다. 목록에서 업체명을 선택해 주세요.", "error");
      return;
    }
    input.value = company.companyName;
    render(company);
  }

  function populateOptions() {
    const datalist = byId("annualHistoryCompanyOptions");
    if (!datalist) return;
    const seen = new Set();
    const options = [];
    companies.forEach((company) => {
      uniqueTexts([company.companyName, company.aliases.slice(0, 4)]).forEach((label) => {
        const key = searchKey(label);
        if (!key || seen.has(key)) return;
        seen.add(key);
        options.push(`<option value="${escapeHtml(label)}" label="${escapeHtml(`${company.companyName} · ${company.latest.sector} / ${company.latest.industry}`)}"></option>`);
      });
    });
    datalist.innerHTML = options.join("");
  }

  function bindEvents() {
    byId("annualHistorySearchButton")?.addEventListener("click", executeSearch);
    byId("annualHistoryCompanySearch")?.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      executeSearch();
    });
  }

  async function load() {
    bindEvents();
    try {
      const response = await window.fetch(DATA_PATH, { cache: "no-cache" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      rows = normalizeRows(rowsFrom(payload));
      companies = buildCompanies(rows);
      if (!companies.length) throw new Error("표시할 업체 자료가 없습니다.");
      populateOptions();
      ready = true;
      const button = byId("annualHistorySearchButton");
      if (button) button.disabled = false;
      setStatus("업체명을 입력하면 해당 업체·업종·부문의 2015~2025년 연초 과부족 히스토리를 조회합니다.");
    } catch (error) {
      console.error("업체 연도별 히스토리 로딩 실패", error);
      setStatus(`히스토리 자료를 불러오지 못했습니다. (${error.message})`, "error");
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", load, { once: true });
  } else {
    load();
  }
})();
