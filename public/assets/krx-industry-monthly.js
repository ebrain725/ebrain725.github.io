"use strict";

(() => {
  const DATA_URL = "data/krx-industry-monthly.json";
  const numberFormat = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });
  const collator = new Intl.Collator("ko-KR", { numeric: true, sensitivity: "base" });
  const state = {
    payload: null,
    year: "",
    month: "",
    sector: "",
    query: "",
    netStatus: "",
    expanded: new Set(["root", "allocated"]),
    lastTree: null,
  };

  const byId = (id) => document.getElementById(id);
  const cleanText = (value) => String(value ?? "").replace(/\s+/g, " ").trim();
  const normalizeSearch = (value) => cleanText(value)
    .normalize("NFKC")
    .replace(/\s+/g, "")
    .toLocaleLowerCase("ko-KR");
  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

  function formatMonth(month) {
    const [year, monthNumber] = String(month).split("-");
    return `${year}년 ${Number(monthNumber)}월`;
  }

  function signedNumber(value) {
    const rounded = Math.round(value || 0);
    if (rounded > 0) return `+${numberFormat.format(rounded)}`;
    if (rounded < 0) return `−${numberFormat.format(Math.abs(rounded))}`;
    return "0";
  }

  function metrics(sell = 0, buy = 0) {
    const safeSell = Number.isFinite(Number(sell)) ? Number(sell) : 0;
    const safeBuy = Number.isFinite(Number(buy)) ? Number(buy) : 0;
    return { sell: safeSell, buy: safeBuy, net: safeBuy - safeSell };
  }

  function addMetrics(items) {
    return items.reduce(
      (total, item) => ({
        sell: total.sell + item.sell,
        buy: total.buy + item.buy,
        net: total.net + item.net,
      }),
      { sell: 0, buy: 0, net: 0 },
    );
  }

  function netMatches(value) {
    if (!state.netStatus) return true;
    if (state.netStatus === "buy") return value > 0;
    if (state.netStatus === "sell") return value < 0;
    if (state.netStatus === "balanced") return value === 0;
    return true;
  }

  function selectedMonthIndex() {
    return state.payload?.months?.indexOf(state.month) ?? -1;
  }

  function selectedMonthMeta() {
    const index = selectedMonthIndex();
    const values = index >= 0 ? state.payload?.totals?.[index] : null;
    if (!Array.isArray(values) || values.length < 6) return null;
    return {
      total: { sell: values[0], buy: values[1] },
      allocatedEntities: { sell: values[2], buy: values[3] },
      otherParticipants: { sell: values[4], buy: values[5] },
    };
  }

  function allIndustryRows() {
    const monthIndex = selectedMonthIndex();
    if (!state.payload || monthIndex < 0) return [];
    const sectors = state.payload.coverage?.sectors || [];
    return state.payload.industries.map((item) => {
      const [order, sectorIndex, industry, values] = item;
      const value = Array.isArray(values?.[monthIndex]) ? values[monthIndex] : [0, 0];
      return {
        order: Number(order),
        sector: cleanText(sectors[sectorIndex]) || "미분류",
        industry: cleanText(industry) || "미분류",
        searchText: normalizeSearch(industry),
        metrics: metrics(value[0], value[1]),
      };
    });
  }

  function filteredIndustryRows() {
    const query = normalizeSearch(state.query);
    return allIndustryRows().filter((row) => (
      (!state.sector || row.sector === state.sector)
      && (!query || row.searchText.includes(query))
      && netMatches(row.metrics.net)
    ));
  }

  function makeNode(kind, key, label, depth, nodeMetrics, children = [], count = 0) {
    return { kind, key, label, depth, metrics: nodeMetrics, children, count };
  }

  function buildTree() {
    const filtered = filteredIndustryRows();
    const sectorOrder = state.payload?.coverage?.sectors || [];
    const sectors = sectorOrder
      .map((sector) => {
        const rows = filtered
          .filter((row) => row.sector === sector)
          .sort((left, right) => left.order - right.order || collator.compare(left.industry, right.industry));
        if (!rows.length) return null;
        const children = rows.map((row) => makeNode(
          "industry",
          `industry:${row.order}`,
          row.industry,
          3,
          row.metrics,
        ));
        return makeNode(
          "sector",
          `sector:${sector}`,
          sector,
          2,
          addMetrics(rows.map((row) => row.metrics)),
          children,
          rows.length,
        );
      })
      .filter(Boolean);

    const allocatedMetrics = addMetrics(filtered.map((row) => row.metrics));
    const allocated = makeNode(
      "allocated",
      "allocated",
      "할당대상업체 소계",
      1,
      allocatedMetrics,
      sectors,
      filtered.length,
    );

    const filteringIndustries = Boolean(state.sector || normalizeSearch(state.query) || state.netStatus);
    const monthMeta = selectedMonthMeta();
    const otherMetrics = metrics(
      monthMeta?.otherParticipants?.sell,
      monthMeta?.otherParticipants?.buy,
    );
    const includeOther = !state.sector && !normalizeSearch(state.query) && netMatches(otherMetrics.net);
    const other = includeOther
      ? makeNode("other", "other", "할당업체외 소계", 1, otherMetrics)
      : null;

    const children = [];
    if (filtered.length || !filteringIndustries) children.push(allocated);
    if (other) children.push(other);
    const rootMetrics = addMetrics(children.map((node) => node.metrics));
    const rootLabel = filteringIndustries ? "선택 조건 합계" : "전체 거래";
    return makeNode("total", "root", rootLabel, 0, rootMetrics, children, filtered.length);
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
    state.expanded.add("allocated");
    if (state.sector || normalizeSearch(state.query) || state.netStatus) {
      root.children.forEach((node) => {
        if (node.kind !== "allocated") return;
        node.children.forEach((sector) => state.expanded.add(sector.key));
      });
    }
  }

  function treeCellMarkup(node) {
    const expanded = node.children.length ? state.expanded.has(node.key) : false;
    const toggle = node.children.length
      ? [
        `<button type="button" class="industry-tree-toggle" data-node-toggle="${escapeHtml(node.key)}"`,
        ` aria-expanded="${expanded}" aria-label="${escapeHtml(node.label)} 하위 항목 ${expanded ? "접기" : "펼치기"}"></button>`,
      ].join("")
      : '<span class="industry-tree-spacer" aria-hidden="true"></span>';
    const count = ["total", "allocated", "sector"].includes(node.kind)
      ? `<span class="industry-tree-count">${numberFormat.format(node.count)}개 업종</span>`
      : "";
    return [
      `<div class="industry-tree-content" style="--tree-depth:${node.depth}">`,
      toggle,
      `<span class="industry-tree-label" title="${escapeHtml(node.label)}">${escapeHtml(node.label)}</span>`,
      count,
      "</div>",
    ].join("");
  }

  function netMarkup(value) {
    const rounded = Math.round(value);
    const status = rounded > 0 ? "순매수" : rounded < 0 ? "순매도" : "균형";
    const className = rounded > 0
      ? "industry-net-positive"
      : rounded < 0 ? "industry-net-negative" : "industry-net-neutral";
    return [
      `<span class="industry-net-value ${className}" aria-label="${status} ${escapeHtml(signedNumber(rounded))}톤">`,
      `<small aria-hidden="true">${status}</small>`,
      `<b aria-hidden="true">${escapeHtml(signedNumber(rounded))}</b>`,
      "</span>",
    ].join("");
  }

  function rowMarkup(node) {
    return [
      `<tr class="industry-row-${node.kind}">`,
      `<td role="rowheader">${treeCellMarkup(node)}</td>`,
      `<td>${numberFormat.format(Math.round(node.metrics.sell))}</td>`,
      `<td>${numberFormat.format(Math.round(node.metrics.buy))}</td>`,
      `<td class="industry-net-column">${netMarkup(node.metrics.net)}</td>`,
      "</tr>",
    ].join("");
  }

  function updateExpandButton(root) {
    const button = byId("industryExpandAll");
    const nodes = expandableNodes(root);
    const allExpanded = nodes.length > 0 && nodes.every((node) => state.expanded.has(node.key));
    button.disabled = nodes.length === 0;
    button.setAttribute("aria-pressed", String(allExpanded));
    button.textContent = allExpanded ? "계층 전체 접기" : "계층 전체 펼치기";
  }

  function renderTable({ expandForFilter = false } = {}) {
    const root = buildTree();
    state.lastTree = root;
    if (expandForFilter) expandFilteredBranches(root);
    const visible = flattenVisibleTree(root);
    const filtered = filteredIndustryRows();
    const tbody = byId("industryRows");
    const hasVisibleDetail = root.children.length > 0;

    if (!hasVisibleDetail) {
      tbody.innerHTML = '<tr class="industry-empty-row"><td colspan="4">선택한 조건에 해당하는 업종 자료가 없습니다.</td></tr>';
      byId("industryResultCount").textContent = `${formatMonth(state.month)} · 업종 0개`;
      updateExpandButton(root);
      return;
    }

    tbody.innerHTML = visible.map(rowMarkup).join("");
    byId("industryResultCount").textContent = `${formatMonth(state.month)} · 업종 ${numberFormat.format(filtered.length)}개 · ${numberFormat.format(visible.length)}행 표시`;
    updateExpandButton(root);
  }

  function renderKpis() {
    const monthMeta = selectedMonthMeta();
    if (!monthMeta) return;
    const total = metrics(monthMeta.total.sell, monthMeta.total.buy);
    const allocated = metrics(monthMeta.allocatedEntities.sell, monthMeta.allocatedEntities.buy);
    const rows = allIndustryRows();
    const buyers = [...rows].sort((left, right) => right.metrics.net - left.metrics.net || left.order - right.order);
    const sellers = [...rows].sort((left, right) => left.metrics.net - right.metrics.net || left.order - right.order);
    const topBuyer = buyers[0];
    const topSeller = sellers[0];

    byId("industryTotalVolume").textContent = `${numberFormat.format(total.sell)}톤`;
    byId("industryTotalVolumeMonth").textContent = `${formatMonth(state.month)} · 매도·매수 동일`;
    byId("allocatedNetVolume").textContent = `${signedNumber(allocated.net)}톤`;
    const allocatedCard = byId("allocatedNetCard");
    allocatedCard.classList.toggle("net-positive", allocated.net > 0);
    allocatedCard.classList.toggle("net-negative", allocated.net < 0);
    allocatedCard.classList.toggle("net-neutral", allocated.net === 0);

    byId("topIndustryBuyer").textContent = topBuyer?.industry || "—";
    byId("topIndustryBuyerValue").textContent = topBuyer
      ? `${topBuyer.sector} · ${signedNumber(topBuyer.metrics.net)}톤`
      : "—";
    byId("topIndustrySeller").textContent = topSeller?.industry || "—";
    byId("topIndustrySellerValue").textContent = topSeller
      ? `${topSeller.sector} · ${signedNumber(topSeller.metrics.net)}톤`
      : "—";
  }

  function renderCoverage() {
    const coverage = state.payload.coverage;
    byId("industryCoverage").textContent = `${coverage.startMonth.replace("-", ".")}–${coverage.endMonth.replace("-", ".")}`;
    byId("industryCoverageDetail").textContent = `${numberFormat.format(coverage.monthCount)}개월 · ${numberFormat.format(coverage.industryCount)}개 업종`;
  }

  function replaceOptions(select, values, selectedValue, formatter, allLabel = null) {
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
      option.textContent = formatter(value);
      fragment.append(option);
    });
    select.replaceChildren(fragment);
    const available = new Set([...select.options].map((option) => option.value));
    select.value = available.has(String(selectedValue)) ? String(selectedValue) : "";
  }

  function availableYears() {
    return [...new Set(state.payload.months.map((month) => month.slice(0, 4)))]
      .sort((left, right) => Number(right) - Number(left));
  }

  function monthsForYear(year) {
    return state.payload.months
      .filter((month) => month.startsWith(`${year}-`))
      .sort((left, right) => right.localeCompare(left));
  }

  function renderYearOptions() {
    replaceOptions(byId("industryYear"), availableYears(), state.year, (year) => `${year}년`);
    state.year = byId("industryYear").value;
  }

  function renderMonthOptions() {
    const months = monthsForYear(state.year);
    if (!months.includes(state.month)) state.month = months[0] || "";
    replaceOptions(byId("industryMonth"), months, state.month, (month) => `${Number(month.slice(5))}월`);
    state.month = byId("industryMonth").value;
  }

  function renderSectorOptions() {
    replaceOptions(
      byId("industrySector"),
      state.payload.coverage.sectors,
      state.sector,
      (sector) => sector,
      "전체 부문",
    );
    state.sector = byId("industrySector").value;
  }

  function setControlsDisabled(disabled) {
    [
      "industryYear",
      "industryMonth",
      "industrySector",
      "industrySearch",
      "industryNetStatus",
      "industryResetFilters",
    ].forEach((id) => { byId(id).disabled = disabled; });
    if (disabled) byId("industryExpandAll").disabled = true;
  }

  function setStatus(kind, title, detail) {
    const alert = byId("industryStatusAlert");
    alert.setAttribute("role", kind === "error" ? "alert" : "status");
    alert.classList.toggle("error", kind === "error");
    byId("industryStatusTitle").textContent = title;
    byId("industryStatusDetail").textContent = detail;
    alert.hidden = false;
  }

  function clearStatus() {
    byId("industryStatusAlert").hidden = true;
  }

  function renderAll({ expandForFilter = false } = {}) {
    clearStatus();
    renderKpis();
    renderTable({ expandForFilter });
  }

  function bindEvents() {
    byId("industryYear").addEventListener("change", (event) => {
      state.year = event.target.value;
      state.month = "";
      renderMonthOptions();
      state.expanded = new Set(["root", "allocated"]);
      renderAll();
    });
    byId("industryMonth").addEventListener("change", (event) => {
      state.month = event.target.value;
      state.expanded = new Set(["root", "allocated"]);
      renderAll();
    });
    byId("industrySector").addEventListener("change", (event) => {
      state.sector = event.target.value;
      state.expanded = new Set(["root", "allocated"]);
      renderAll({ expandForFilter: true });
    });
    byId("industryNetStatus").addEventListener("change", (event) => {
      state.netStatus = event.target.value;
      state.expanded = new Set(["root", "allocated"]);
      renderAll({ expandForFilter: true });
    });

    let searchTimer = null;
    byId("industrySearch").addEventListener("input", (event) => {
      window.clearTimeout(searchTimer);
      const value = event.target.value;
      searchTimer = window.setTimeout(() => {
        state.query = value;
        state.expanded = new Set(["root", "allocated"]);
        renderAll({ expandForFilter: true });
      }, 250);
    });

    byId("industryResetFilters").addEventListener("click", () => {
      window.clearTimeout(searchTimer);
      searchTimer = null;
      const latest = state.payload.coverage.endMonth;
      state.year = latest.slice(0, 4);
      state.month = latest;
      state.sector = "";
      state.query = "";
      state.netStatus = "";
      state.expanded = new Set(["root", "allocated"]);
      byId("industrySearch").value = "";
      byId("industryNetStatus").value = "";
      renderYearOptions();
      renderMonthOptions();
      renderSectorOptions();
      renderAll();
    });

    byId("industryRows").addEventListener("click", (event) => {
      const button = event.target.closest("[data-node-toggle]");
      if (!button) return;
      const key = button.dataset.nodeToggle;
      if (state.expanded.has(key)) state.expanded.delete(key);
      else state.expanded.add(key);
      renderTable();
      const nextButton = [...byId("industryRows").querySelectorAll("[data-node-toggle]")]
        .find((candidate) => candidate.dataset.nodeToggle === key);
      nextButton?.focus();
    });

    byId("industryExpandAll").addEventListener("click", () => {
      if (!state.lastTree) return;
      const nodes = expandableNodes(state.lastTree);
      const allExpanded = nodes.length > 0 && nodes.every((node) => state.expanded.has(node.key));
      state.expanded = allExpanded ? new Set(["root", "allocated"]) : new Set(nodes.map((node) => node.key));
      renderTable();
    });
  }

  function validatePayload(payload) {
    if (!payload || typeof payload !== "object") throw new Error("JSON 형식 오류");
    if (!Array.isArray(payload.months) || !payload.months.length) throw new Error("월 목록이 없습니다.");
    if (!Array.isArray(payload.totals) || payload.totals.length !== payload.months.length) throw new Error("월별 합계 자료가 없습니다.");
    if (!Array.isArray(payload.industries) || !payload.industries.length) throw new Error("업종별 자료가 없습니다.");
    if (!payload.coverage?.endMonth) throw new Error("자료 범위가 없습니다.");
  }

  async function loadData() {
    const response = await fetch(DATA_URL, { cache: "no-cache" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    validatePayload(payload);
    state.payload = payload;
    state.month = payload.coverage.endMonth;
    state.year = state.month.slice(0, 4);
  }

  async function init() {
    setControlsDisabled(true);
    bindEvents();
    try {
      await loadData();
      renderCoverage();
      renderYearOptions();
      renderMonthOptions();
      renderSectorOptions();
      renderAll();
      setControlsDisabled(false);
    } catch (error) {
      console.error("월별 업종 매매현황 로딩 실패", error);
      setStatus("error", "월별 업종 매매자료를 불러오지 못했습니다.", `data/krx-industry-monthly.json을 확인해 주세요. (${error.message})`);
      byId("industryRows").innerHTML = '<tr class="industry-empty-row"><td colspan="4">월별 업종 매매현황을 표시할 수 없습니다.</td></tr>';
      byId("industryResultCount").textContent = "자료 확인 필요";
    } finally {
      byId("industryDashboard").setAttribute("aria-busy", "false");
    }
  }

  init();
})();
