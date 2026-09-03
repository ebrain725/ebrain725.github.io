(() => {
  "use strict";

  const DATA_ROOT = "data/energy-power/";
  const PAGE_SIZE = 50;
  const SOURCE_ORDER = ["kpx_smp", "kpx_power", "eia_energy"];
  const SOURCE_STATUS_LABELS = {
    ok: "정상",
    ready: "정상",
    partial: "일부 성공",
    error: "수집 오류",
    pending: "수집 대기",
  };
  const METRIC_LABELS = {
    wti_spot: "WTI 현물",
    brent_spot: "Brent 현물",
    henry_hub_spot: "Henry Hub 현물",
  };

  const RAW_COLUMNS = {
    kpx_smp: [
      ["date", "거래일"],
      ["hour", "시간"],
      ["area_name", "지역"],
      ["smp_krw_per_kwh", "SMP (원/kWh)", "number"],
      ["mlfd_mw", "MLFD (MW)", "number"],
      ["jlfd_mw", "JLFD (MW)", "number"],
      ["slfd_mw", "SLFD (MW)", "number"],
      ["collected_at_kst", "수집시각 (KST)", "datetime"],
    ],
    kpx_power: [
      ["date", "거래일"],
      ["base_datetime_kst", "관측시각 (KST)", "datetime"],
      ["supply_capacity_gw", "공급능력 (GW)", "number"],
      ["current_demand_gw", "현재수요 (GW)", "number"],
      ["forecast_peak_demand_gw", "최대예측수요 (GW)", "number"],
      ["supply_reserve_gw", "공급예비력 (GW)", "number"],
      ["supply_reserve_rate_pct", "공급예비율 (%)", "number"],
      ["operating_reserve_gw", "운영예비력 (GW)", "number"],
      ["operating_reserve_rate_pct", "운영예비율 (%)", "number"],
      ["collected_at_kst", "수집시각 (KST)", "datetime"],
    ],
    eia_energy: [
      ["date", "관측일"],
      ["metric", "지표", "metric"],
      ["series_id", "EIA Series ID"],
      ["value", "값", "number"],
      ["unit", "단위"],
      ["collected_at_kst", "수집시각 (KST)", "datetime"],
    ],
  };

  const DAILY_COLUMNS = [
    ["date", "기준일"],
    ["smp_average", "SMP 평균 (원/kWh)", "number"],
    ["smp_high", "SMP 최고", "number"],
    ["smp_low", "SMP 최저", "number"],
    ["smp_hours", "시간값 (개)", "integer"],
    ["smp_forecast", "수요예측 피크 (GW)", "number"],
    ["power_demand", "현재수요 (GW)", "number"],
    ["power_forecast", "최대예측수요 (GW)", "number"],
    ["supply_reserve", "공급예비율 (%)", "number"],
    ["operating_reserve", "운영예비율 (%)", "number"],
    ["wti", "WTI (USD/bbl)", "number"],
    ["brent", "Brent (USD/bbl)", "number"],
    ["henry", "Henry Hub (USD/MMBtu)", "number"],
  ];

  const state = {
    manifest: null,
    daily: null,
    view: "daily",
    dataset: "kpx_smp",
    year: "ALL",
    startDate: "",
    endDate: "",
    page: 1,
    rows: [],
    rawCache: new Map(),
  };

  const elements = {};

  function byId(id) {
    return document.getElementById(id);
  }

  function cacheElements() {
    [
      "energyDashboard", "energySyncStatus", "energySyncTime", "energyAlert",
      "energyAlertTitle", "energyAlertDetail", "latestSmp", "latestSmpMeta",
      "latestDemand", "latestDemandMeta", "latestReserve", "latestReserveMeta",
      "latestWti", "latestWtiMeta", "latestBrent", "latestBrentMeta",
      "latestHenry", "latestHenryMeta", "sourceGrid", "datasetField",
      "datasetSelect", "yearSelect", "startDate", "endDate", "applyFilter",
      "resetFilter", "recordTotal", "tableStatus", "dataHead", "dataBody",
      "coverageText", "pagination",
    ].forEach((id) => { elements[id] = byId(id); });
  }

  async function fetchJson(path) {
    const separator = path.includes("?") ? "&" : "?";
    const response = await fetch(`${path}${separator}v=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`${path} 응답 오류 (${response.status})`);
    return response.json();
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function hasNumber(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function formatNumber(value, digits = 2) {
    if (!hasNumber(value)) return "-";
    return new Intl.NumberFormat("ko-KR", {
      minimumFractionDigits: 0,
      maximumFractionDigits: digits,
    }).format(value);
  }

  function formatDateTime(value) {
    if (!value) return "-";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return new Intl.DateTimeFormat("ko-KR", {
      timeZone: "Asia/Seoul",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(parsed);
  }

  function setAlert(title, detail, level = "warning") {
    elements.energyAlert.hidden = false;
    elements.energyAlert.classList.toggle("error", level === "error");
    elements.energyAlertTitle.textContent = title;
    elements.energyAlertDetail.textContent = detail;
  }

  function clearAlert() {
    elements.energyAlert.hidden = true;
    elements.energyAlert.classList.remove("error");
  }

  function setMetric(valueId, metaId, value, digits, date, extra = "") {
    elements[valueId].textContent = formatNumber(value, digits);
    elements[metaId].textContent = date ? `${date}${extra ? ` · ${extra}` : ""}` : "최근 관측 대기";
  }

  function renderLatest() {
    const latest = state.daily?.latest || {};
    const smp = latest.kpx_smp || {};
    const power = latest.kpx_power || {};
    setMetric(
      "latestSmp", "latestSmpMeta", smp.average_krw_per_kwh, 2, smp.date,
      hasNumber(smp.forecast_peak_gw) ? `수요예측 피크 ${formatNumber(smp.forecast_peak_gw, 1)}GW` : "",
    );
    setMetric(
      "latestDemand", "latestDemandMeta", power.current_demand_gw, 2, power.date,
      power.latest_at_kst ? `관측 ${formatDateTime(power.latest_at_kst)}` : "",
    );
    setMetric(
      "latestReserve", "latestReserveMeta", power.supply_reserve_rate_pct, 2, power.date,
      hasNumber(power.operating_reserve_rate_pct)
        ? `운영예비율 ${formatNumber(power.operating_reserve_rate_pct, 2)}%`
        : "",
    );
    setMetric("latestWti", "latestWtiMeta", latest.wti_spot?.value, 2, latest.wti_spot?.date);
    setMetric("latestBrent", "latestBrentMeta", latest.brent_spot?.value, 2, latest.brent_spot?.date);
    setMetric("latestHenry", "latestHenryMeta", latest.henry_hub_spot?.value, 3, latest.henry_hub_spot?.date);
  }

  function renderSyncStatus() {
    const sources = Object.values(state.manifest?.sources || {});
    const statuses = sources.map((source) => source.status);
    let label = "수집 대기";
    if (statuses.length && statuses.every((status) => ["ok", "ready"].includes(status))) label = "전체 원천 정상";
    else if (statuses.some((status) => status === "error")) label = "일부 원천 점검 필요";
    else if (statuses.some((status) => status === "partial")) label = "일부 데이터 수집";
    elements.energySyncStatus.textContent = label;
    elements.energySyncTime.textContent = state.manifest?.updated_at_kst
      ? `최근 실행 ${formatDateTime(state.manifest.updated_at_kst)}`
      : "첫 자동수집을 기다리고 있습니다.";

    const errors = sources.filter((source) => source.status === "error");
    const partials = sources.filter((source) => source.status === "partial");
    if (errors.length) {
      setAlert(
        "일부 API 수집 오류",
        `${errors.map((source) => source.label).join(", ")} 수집에 실패했습니다. 기존 정상 데이터는 그대로 유지됩니다.`,
        "error",
      );
    } else if (partials.length) {
      setAlert(
        "일부 날짜 또는 지표만 수집됨",
        `${partials.map((source) => source.label).join(", ")}의 일부 요청이 실패했습니다. 수집 상태 카드에서 세부 내용을 확인하세요.`,
      );
    } else {
      clearAlert();
    }
  }

  function renderSources() {
    const sources = state.manifest?.sources || {};
    const cards = SOURCE_ORDER.map((sourceId) => sources[sourceId]).filter(Boolean);
    if (!cards.length) {
      elements.sourceGrid.innerHTML = '<div class="source-loading">첫 자동수집을 기다리고 있습니다.</div>';
      return;
    }
    elements.sourceGrid.innerHTML = cards.map((source) => {
      const status = source.status || "pending";
      return `
        <article class="source-card">
          <div class="source-card-head">
            <div><h3>${escapeHtml(source.label)}</h3><span class="provider">${escapeHtml(source.provider)}</span></div>
            <span class="status-badge ${escapeHtml(status)}">${escapeHtml(SOURCE_STATUS_LABELS[status] || status)}</span>
          </div>
          <p>${escapeHtml(source.description)}</p>
          <div class="source-metrics">
            <div><span>누적 원천 레코드</span><b>${formatNumber(source.record_count, 0)}건</b></div>
            <div><span>최근 관측일</span><b>${escapeHtml(source.last_observation_date || "-")}</b></div>
            <div><span>주기·단위</span><b>${escapeHtml(`${source.frequency} · ${source.unit}`)}</b></div>
            <div><span>이번 실행</span><b>${formatNumber(source.run_observation_count, 0)}건 확인</b></div>
          </div>
          ${source.last_error ? `<p class="source-error">${escapeHtml(source.last_error)}</p>` : ""}
        </article>`;
    }).join("");
  }

  function allDailyYears() {
    return [...new Set((state.daily?.rows || []).map((row) => String(row.date || "").slice(0, 4)).filter(Boolean))]
      .sort((a, b) => b.localeCompare(a));
  }

  function rawYears(dataset) {
    return (state.manifest?.sources?.[dataset]?.years || []).map(String);
  }

  function renderYearOptions() {
    const years = state.view === "daily" ? allDailyYears() : rawYears(state.dataset);
    const previous = state.year;
    elements.yearSelect.innerHTML = [
      '<option value="ALL">전체</option>',
      ...years.map((year) => `<option value="${escapeHtml(year)}">${escapeHtml(year)}년</option>`),
    ].join("");
    if (years.includes(previous) || previous === "ALL") {
      elements.yearSelect.value = previous;
    } else {
      state.year = state.view === "daily" ? "ALL" : (years[0] || "ALL");
      elements.yearSelect.value = state.year;
    }
  }

  function flattenDaily(row) {
    const smp = row.kpx_smp || {};
    const power = row.kpx_power || {};
    const eia = row.eia || {};
    return {
      date: row.date,
      smp_average: smp.average_krw_per_kwh,
      smp_high: smp.high_krw_per_kwh,
      smp_low: smp.low_krw_per_kwh,
      smp_hours: smp.hourly_count,
      smp_forecast: smp.forecast_peak_gw,
      power_demand: power.current_demand_gw,
      power_forecast: power.forecast_peak_demand_gw,
      supply_reserve: power.supply_reserve_rate_pct,
      operating_reserve: power.operating_reserve_rate_pct,
      wti: eia.wti_spot?.value,
      brent: eia.brent_spot?.value,
      henry: eia.henry_hub_spot?.value,
    };
  }

  function inDateRange(row) {
    const day = String(row.date || "").slice(0, 10);
    if (state.year && state.year !== "ALL" && !day.startsWith(state.year)) return false;
    if (state.startDate && day < state.startDate) return false;
    if (state.endDate && day > state.endDate) return false;
    return true;
  }

  async function loadRawRows() {
    if (!state.year) return [];
    const source = state.manifest?.sources?.[state.dataset];
    const years = state.year === "ALL" ? rawYears(state.dataset) : [state.year];
    const payloads = await Promise.all(years.map(async (year) => {
      const relativePath = source?.files?.[year];
      if (!relativePath) return null;
      const cacheKey = `${state.dataset}:${year}`;
      if (!state.rawCache.has(cacheKey)) {
        state.rawCache.set(cacheKey, await fetchJson(`${DATA_ROOT}${relativePath}`));
      }
      return state.rawCache.get(cacheKey);
    }));
    return payloads.flatMap((payload) => Array.isArray(payload?.rows) ? payload.rows : []);
  }

  async function refreshRows() {
    elements.tableStatus.textContent = "데이터를 불러오고 있습니다.";
    try {
      if (state.view === "daily") {
        state.rows = (state.daily?.rows || []).map(flattenDaily).filter(inDateRange);
      } else {
        state.rows = (await loadRawRows()).filter(inDateRange).sort((a, b) => {
          const left = `${a.date || ""}${a.base_datetime_kst || ""}${a.hour || ""}${a.metric || ""}`;
          const right = `${b.date || ""}${b.base_datetime_kst || ""}${b.hour || ""}${b.metric || ""}`;
          return right.localeCompare(left);
        });
      }
      state.page = 1;
      renderTable();
    } catch (error) {
      state.rows = [];
      renderTable();
      setAlert("원천파일을 불러오지 못했습니다.", error.message || String(error), "error");
    }
  }

  function cellValue(row, column) {
    const [key, , type] = column;
    const value = row[key];
    if (type === "number") return formatNumber(value, key.includes("pct") || key.includes("reserve") ? 2 : 3);
    if (type === "integer") return formatNumber(value, 0);
    if (type === "datetime") return formatDateTime(value);
    if (type === "metric") return METRIC_LABELS[value] || value || "-";
    return value ?? "-";
  }

  function renderTable() {
    const columns = state.view === "daily" ? DAILY_COLUMNS : (RAW_COLUMNS[state.dataset] || []);
    elements.dataHead.innerHTML = `<tr>${columns.map((column) => `<th scope="col">${escapeHtml(column[1])}</th>`).join("")}</tr>`;
    const pageCount = Math.max(1, Math.ceil(state.rows.length / PAGE_SIZE));
    state.page = Math.min(Math.max(1, state.page), pageCount);
    const start = (state.page - 1) * PAGE_SIZE;
    const visible = state.rows.slice(start, start + PAGE_SIZE);
    if (!visible.length) {
      elements.dataBody.innerHTML = `<tr><td class="empty-row" colspan="${Math.max(1, columns.length)}">조회 조건에 맞는 데이터가 없습니다.</td></tr>`;
    } else {
      elements.dataBody.innerHTML = visible.map((row) => `<tr>${columns.map((column) => `<td>${escapeHtml(cellValue(row, column))}</td>`).join("")}</tr>`).join("");
    }
    elements.recordTotal.textContent = `${formatNumber(state.rows.length, 0)}건`;
    elements.tableStatus.textContent = state.view === "daily"
      ? `일별 요약 · ${formatNumber(state.rows.length, 0)}개 기준일`
      : `${state.manifest?.sources?.[state.dataset]?.label || "원천데이터"} · ${state.year || "-"}년`;
    const dates = state.rows.map((row) => row.date).filter(Boolean).sort();
    elements.coverageText.textContent = dates.length
      ? `${dates[0]} ~ ${dates[dates.length - 1]} · 페이지당 ${PAGE_SIZE}건`
      : "수집된 범위가 없습니다.";
    renderPagination(pageCount);
  }

  function renderPagination(pageCount) {
    if (pageCount <= 1) {
      elements.pagination.innerHTML = "";
      return;
    }
    const candidates = new Set([1, pageCount]);
    for (let page = Math.max(1, state.page - 2); page <= Math.min(pageCount, state.page + 2); page += 1) candidates.add(page);
    const pages = [...candidates].sort((a, b) => a - b);
    const pieces = [
      `<button type="button" data-page="${state.page - 1}" ${state.page === 1 ? "disabled" : ""}>이전</button>`,
    ];
    let previous = 0;
    pages.forEach((page) => {
      if (previous && page - previous > 1) pieces.push('<span aria-hidden="true">…</span>');
      pieces.push(`<button type="button" data-page="${page}" class="${page === state.page ? "active" : ""}" ${page === state.page ? 'aria-current="page"' : ""}>${page}</button>`);
      previous = page;
    });
    pieces.push(`<button type="button" data-page="${state.page + 1}" ${state.page === pageCount ? "disabled" : ""}>다음</button>`);
    elements.pagination.innerHTML = pieces.join("");
  }

  function bindEvents() {
    document.querySelectorAll("[data-view]").forEach((button) => {
      button.addEventListener("click", async () => {
        state.view = button.dataset.view;
        document.querySelectorAll("[data-view]").forEach((item) => {
          const active = item === button;
          item.classList.toggle("active", active);
          item.setAttribute("aria-pressed", String(active));
        });
        elements.datasetField.hidden = state.view !== "raw";
        state.year = state.view === "daily" ? "ALL" : (rawYears(state.dataset)[0] || "");
        renderYearOptions();
        await refreshRows();
      });
    });

    elements.datasetSelect.addEventListener("change", async () => {
      state.dataset = elements.datasetSelect.value;
      state.year = rawYears(state.dataset)[0] || "";
      renderYearOptions();
      await refreshRows();
    });

    elements.applyFilter.addEventListener("click", async () => {
      state.year = elements.yearSelect.value;
      state.startDate = elements.startDate.value;
      state.endDate = elements.endDate.value;
      if (state.startDate && state.endDate && state.startDate > state.endDate) {
        setAlert("조회기간을 확인하세요.", "시작일은 종료일보다 늦을 수 없습니다.", "error");
        return;
      }
      await refreshRows();
    });

    elements.resetFilter.addEventListener("click", async () => {
      state.startDate = "";
      state.endDate = "";
      elements.startDate.value = "";
      elements.endDate.value = "";
      state.year = state.view === "daily" ? "ALL" : (rawYears(state.dataset)[0] || "");
      renderYearOptions();
      await refreshRows();
    });

    elements.pagination.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-page]");
      if (!button || button.disabled) return;
      state.page = Number(button.dataset.page);
      renderTable();
      document.querySelector(".data-table-wrap")?.scrollTo({ top: 0, behavior: "smooth" });
    });
  }

  async function initialize() {
    cacheElements();
    bindEvents();
    try {
      const manifest = await fetchJson(`${DATA_ROOT}manifest.json`);
      const daily = await fetchJson(`${DATA_ROOT}${manifest.summary_file || "daily.json"}`);
      state.manifest = manifest;
      state.daily = daily;
      renderSyncStatus();
      renderLatest();
      renderSources();
      renderYearOptions();
      await refreshRows();
    } catch (error) {
      setAlert("전력·에너지 데이터를 불러오지 못했습니다.", error.message || String(error), "error");
      elements.energySyncStatus.textContent = "연결 오류";
      elements.energySyncTime.textContent = "데이터 파일을 확인해 주세요.";
      elements.sourceGrid.innerHTML = '<div class="source-loading">데이터 파일을 불러오지 못했습니다.</div>';
      renderTable();
    } finally {
      elements.energyDashboard.setAttribute("aria-busy", "false");
    }
  }

  document.addEventListener("DOMContentLoaded", initialize);
})();
