"use strict";

(() => {
  const DATA_URL = "data/krx-industry-monthly.json";
  const numberFormat = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });
  const compactFormat = new Intl.NumberFormat("ko-KR", { notation: "compact", maximumFractionDigits: 1 });
  const SERIES_STYLE = {
    "할당대상업체 전체": { color: "#172a22", dash: "" },
    "전환": { color: "#0c7c59", dash: "" },
    "산업": { color: "#2d64a0", dash: "7 4" },
    "건물": { color: "#a16b2d", dash: "2 4" },
    "수송": { color: "#87518d", dash: "10 4 2 4" },
    "폐기물": { color: "#bd594e", dash: "4 3" },
    "공공·기타": { color: "#687781", dash: "12 4" },
  };
  const PRESET_MONTHS = { "3M": 3, "6M": 6, "1Y": 12, "2Y": 24 };
  const state = {
    payload: null,
    months: [],
    startMonth: "",
    endMonth: "",
    period: "1Y",
  };

  const byId = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

  function monthLabel(month) {
    const [year, monthNumber] = String(month).split("-");
    return `${year}년 ${Number(monthNumber)}월`;
  }

  function shortMonthLabel(month) {
    return String(month).slice(2).replace("-", ".");
  }

  function signedNumber(value, compact = false) {
    const rounded = Math.round(Number(value) || 0);
    const formatter = compact ? compactFormat : numberFormat;
    if (rounded > 0) return `+${formatter.format(rounded)}`;
    if (rounded < 0) return `−${formatter.format(Math.abs(rounded))}`;
    return "0";
  }

  function niceStep(value) {
    if (!Number.isFinite(value) || value <= 0) return 1000;
    const power = 10 ** Math.floor(Math.log10(value));
    const fraction = value / power;
    return (fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10) * power;
  }

  function selectedMonths() {
    return state.months.filter((month) => month >= state.startMonth && month <= state.endMonth);
  }

  function monthIndex(month) {
    return state.payload.months.indexOf(month);
  }

  function buildSeries(months) {
    const sectors = state.payload.coverage?.sectors || [];
    const sectorMonthly = new Map(sectors.map((sector) => [sector, Array(months.length).fill(0)]));

    state.payload.industries.forEach((industry) => {
      const sector = sectors[industry[1]] || "미분류";
      if (!sectorMonthly.has(sector)) return;
      const values = industry[3] || [];
      months.forEach((month, selectedIndex) => {
        const sourceIndex = monthIndex(month);
        const pair = Array.isArray(values[sourceIndex]) ? values[sourceIndex] : [0, 0];
        sectorMonthly.get(sector)[selectedIndex] += Number(pair[1] || 0) - Number(pair[0] || 0);
      });
    });

    const orderedLabels = ["할당대상업체 전체", ...sectors];
    const running = new Map(orderedLabels.map((label) => [label, 0]));
    return orderedLabels.map((label) => {
      const monthlyValues = label === "할당대상업체 전체"
        ? months.map((_, index) => [...sectorMonthly.values()].reduce((sum, values) => sum + values[index], 0))
        : sectorMonthly.get(label) || Array(months.length).fill(0);
      const points = monthlyValues.map((monthly, index) => {
        const cumulative = running.get(label) + monthly;
        running.set(label, cumulative);
        return { month: months[index], monthly, cumulative };
      });
      return { label, points };
    });
  }

  function setPeriod(preset) {
    if (!state.months.length) return;
    const lastIndex = state.months.length - 1;
    const count = PRESET_MONTHS[preset];
    const firstIndex = preset === "ALL" ? 0 : Math.max(0, lastIndex - count + 1);
    state.startMonth = state.months[firstIndex];
    state.endMonth = state.months[lastIndex];
    state.period = preset;
    syncControls();
    renderChart();
  }

  function syncControls() {
    const start = byId("industryCumulativeStart");
    const end = byId("industryCumulativeEnd");
    if (start && end && state.months.length) {
      start.min = state.months[0];
      start.max = state.months.at(-1);
      end.min = state.months[0];
      end.max = state.months.at(-1);
      start.value = state.startMonth;
      end.value = state.endMonth;
    }
    document.querySelectorAll("[data-industry-cumulative-period]").forEach((button) => {
      const active = button.dataset.industryCumulativePeriod === state.period;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
  }

  function renderEmpty(message) {
    const svg = byId("industryCumulativeChart");
    const legend = byId("industryCumulativeLegend");
    const label = byId("industryCumulativePeriodLabel");
    if (legend) legend.replaceChildren();
    if (label) label.textContent = "표시할 자료 없음";
    if (!svg) return;
    svg.innerHTML = [
      '<title id="industryCumulativeChartTitle">부문별 누적 순매수량 추이</title>',
      `<desc id="industryCumulativeChartDesc">${escapeHtml(message)}</desc>`,
      `<text x="480" y="200" text-anchor="middle" class="industry-cumulative-empty">${escapeHtml(message)}</text>`,
    ].join("");
  }

  function renderLegend(series) {
    const legend = byId("industryCumulativeLegend");
    if (!legend) return;
    legend.replaceChildren();
    series.forEach((item) => {
      const finalValue = item.points.at(-1)?.cumulative || 0;
      const style = SERIES_STYLE[item.label] || { color: "#46515a", dash: "" };
      const entry = document.createElement("span");
      entry.className = "industry-cumulative-legend-item";
      const marker = document.createElement("i");
      marker.style.setProperty("--series-color", style.color);
      const name = document.createElement("b");
      name.textContent = item.label;
      const value = document.createElement("em");
      value.textContent = `${signedNumber(finalValue, true)}톤`;
      value.className = finalValue > 0 ? "positive" : finalValue < 0 ? "negative" : "neutral";
      entry.append(marker, name, value);
      legend.append(entry);
    });
  }

  function renderSummary(series, months) {
    const note = byId("industryCumulativeNote");
    if (!note) return;
    const sectors = series.filter((item) => item.label !== "할당대상업체 전체");
    const ranked = sectors
      .map((item) => ({ label: item.label, value: item.points.at(-1)?.cumulative || 0 }))
      .sort((left, right) => right.value - left.value);
    const buyer = ranked[0];
    const seller = ranked.at(-1);
    note.textContent = [
      "각 월의 업종별 순매수량을 부문으로 합산하고, 선택기간 시작점을 0으로 누적합니다.",
      buyer && buyer.value > 0 ? `누적 순매수 상위는 ${buyer.label} ${signedNumber(buyer.value)}톤입니다.` : "누적 순매수 부문은 없습니다.",
      seller && seller.value < 0 ? `누적 순매도 상위는 ${seller.label} ${signedNumber(seller.value)}톤입니다.` : "누적 순매도 부문은 없습니다.",
      `선택기간 ${months.length}개월.`,
    ].join(" ");
  }

  function renderChart() {
    const svg = byId("industryCumulativeChart");
    const hoverStatus = byId("industryCumulativeHoverStatus");
    if (!svg || !state.payload) return;
    svg.onpointermove = null;
    svg.onpointerdown = null;
    svg.onpointerleave = null;
    svg.onpointercancel = null;
    svg.onkeydown = null;
    svg.onblur = null;
    if (hoverStatus) hoverStatus.textContent = "";

    const months = selectedMonths();
    if (!months.length) {
      renderEmpty("선택 기간에 표시할 월별 자료가 없습니다.");
      return;
    }

    const series = buildSeries(months);
    const allValues = [0, ...series.flatMap((item) => item.points.map((point) => point.cumulative))];
    const rawMin = Math.min(...allValues);
    const rawMax = Math.max(...allValues);
    const span = Math.max(Math.abs(rawMin), Math.abs(rawMax), 1000);
    const step = niceStep(span / 3);
    let yMin = Math.floor(Math.min(rawMin, 0) / step) * step;
    let yMax = Math.ceil(Math.max(rawMax, 0) / step) * step;
    if (yMin === yMax) {
      yMin -= step;
      yMax += step;
    }

    const width = 960;
    const left = 88;
    const right = 30;
    const top = 28;
    const bottom = 330;
    const x = (index) => months.length === 1
      ? (left + width - right) / 2
      : left + index / (months.length - 1) * (width - left - right);
    const y = (value) => top + (yMax - value) / Math.max(yMax - yMin, 1) * (bottom - top);

    const description = `${monthLabel(months[0])}부터 ${monthLabel(months.at(-1))}까지 할당대상업체 전체와 6개 부문의 월별 순매수량을 선택기간 시작점 0에서 누적한 그래프입니다. 마우스, 터치 또는 키보드 방향키로 월별 순매수량과 누적값을 확인할 수 있습니다.`;
    let html = `<title id="industryCumulativeChartTitle">부문별 누적 순매수량 추이</title><desc id="industryCumulativeChartDesc">${escapeHtml(description)}</desc>`;

    for (let value = yMin; value <= yMax + step * 0.01; value += step) {
      const py = y(value);
      html += `<line x1="${left}" x2="${width - right}" y1="${py}" y2="${py}" class="${value === 0 ? "industry-cumulative-zero" : "industry-cumulative-grid"}"/>`;
      html += `<text x="${left - 12}" y="${py + 4}" text-anchor="end" class="industry-cumulative-axis">${escapeHtml(compactFormat.format(value))}</text>`;
    }

    const tickCount = Math.min(7, months.length);
    const tickIndexes = new Set(Array.from({ length: tickCount }, (_, index) => (
      Math.round(index * (months.length - 1) / Math.max(tickCount - 1, 1))
    )));
    [...tickIndexes].forEach((index) => {
      html += `<text x="${x(index)}" y="362" text-anchor="middle" class="industry-cumulative-axis">${shortMonthLabel(months[index])}</text>`;
    });

    series.forEach((item) => {
      const style = SERIES_STYLE[item.label] || { color: "#46515a", dash: "" };
      const path = item.points.map((point, index) => `${index ? "L" : "M"}${x(index)},${y(point.cumulative)}`).join(" ");
      const lineClass = item.label === "할당대상업체 전체"
        ? "industry-cumulative-line industry-cumulative-total-line"
        : "industry-cumulative-line";
      html += `<path d="${path}" class="${lineClass}" style="stroke:${style.color};stroke-dasharray:${style.dash || "none"}"><title>${escapeHtml(item.label)}</title></path>`;
      const last = item.points.at(-1);
      html += `<circle cx="${x(item.points.length - 1)}" cy="${y(last.cumulative)}" r="4" class="industry-cumulative-end" style="fill:${style.color}"><title>${escapeHtml(item.label)} ${escapeHtml(signedNumber(last.cumulative))}톤</title></circle>`;
    });
    html += `<text x="17" y="${top}" class="industry-cumulative-unit">톤</text>`;
    html += '<g id="industryCumulativeHoverLayer" class="industry-cumulative-hover-layer" aria-hidden="true" hidden></g>';
    svg.innerHTML = html;

    const label = byId("industryCumulativePeriodLabel");
    if (label) label.textContent = `${monthLabel(months[0])} – ${monthLabel(months.at(-1))} · ${months.length}개월`;
    renderLegend(series);
    renderSummary(series, months);

    const hoverLayer = svg.querySelector("#industryCumulativeHoverLayer");
    let activeIndex = -1;
    const showHover = (index) => {
      const bounded = Math.max(0, Math.min(months.length - 1, index));
      if (bounded === activeIndex && !hoverLayer.hasAttribute("hidden")) return;
      activeIndex = bounded;
      const px = x(bounded);
      const tooltipWidth = 430;
      const tooltipHeight = 70 + series.length * 25;
      const tooltipX = px > (left + width - right) / 2
        ? Math.max(left + 7, px - tooltipWidth - 15)
        : Math.min(width - right - tooltipWidth - 7, px + 15);
      const tooltipY = top + 7;
      let markers = "";
      let rows = "";
      series.forEach((item, rowIndex) => {
        const point = item.points[bounded];
        const style = SERIES_STYLE[item.label] || { color: "#46515a" };
        markers += `<circle cx="${px}" cy="${y(point.cumulative)}" r="5" class="industry-cumulative-hover-point" style="fill:${style.color}"/>`;
        const rowY = 79 + rowIndex * 25;
        const monthlyClass = point.monthly > 0 ? "positive" : point.monthly < 0 ? "negative" : "neutral";
        const cumulativeClass = point.cumulative > 0 ? "positive" : point.cumulative < 0 ? "negative" : "neutral";
        rows += `<circle cx="18" cy="${rowY - 4}" r="4" style="fill:${style.color}"/>`;
        rows += `<text x="30" y="${rowY}" class="industry-cumulative-tooltip-label">${escapeHtml(item.label)}</text>`;
        rows += `<text x="302" y="${rowY}" text-anchor="end" class="industry-cumulative-tooltip-value ${monthlyClass}">${escapeHtml(signedNumber(point.monthly))}</text>`;
        rows += `<text x="414" y="${rowY}" text-anchor="end" class="industry-cumulative-tooltip-value ${cumulativeClass}">${escapeHtml(signedNumber(point.cumulative))}</text>`;
      });
      hoverLayer.innerHTML = [
        `<line x1="${px}" x2="${px}" y1="${top}" y2="${bottom}" class="industry-cumulative-hover-line"/>`,
        markers,
        `<g class="industry-cumulative-tooltip" transform="translate(${tooltipX} ${tooltipY})">`,
        `<rect width="${tooltipWidth}" height="${tooltipHeight}" rx="11"/>`,
        `<text x="16" y="24" class="industry-cumulative-tooltip-date">${monthLabel(months[bounded])}</text>`,
        `<line x1="14" x2="${tooltipWidth - 14}" y1="35" y2="35" class="industry-cumulative-tooltip-rule"/>`,
        '<text x="16" y="55" class="industry-cumulative-tooltip-heading">구분</text>',
        '<text x="302" y="55" text-anchor="end" class="industry-cumulative-tooltip-heading">월 순매수</text>',
        '<text x="414" y="55" text-anchor="end" class="industry-cumulative-tooltip-heading">누적 순매수</text>',
        rows,
        "</g>",
      ].join("");
      hoverLayer.removeAttribute("hidden");
      if (hoverStatus) {
        hoverStatus.textContent = `${monthLabel(months[bounded])}. ${series.map((item) => {
          const point = item.points[bounded];
          return `${item.label} 월 ${signedNumber(point.monthly)}톤, 누적 ${signedNumber(point.cumulative)}톤`;
        }).join(". ")}`;
      }
    };
    const hideHover = () => {
      hoverLayer.setAttribute("hidden", "");
      activeIndex = -1;
      if (hoverStatus) hoverStatus.textContent = "";
    };
    const pointerIndex = (event) => {
      const rect = svg.getBoundingClientRect();
      if (!rect.width) return null;
      const localX = (event.clientX - rect.left) / rect.width * width;
      if (localX < left || localX > width - right) return null;
      const ratio = (localX - left) / Math.max(width - left - right, 1);
      return Math.round(ratio * Math.max(months.length - 1, 0));
    };
    const showFromPointer = (event) => {
      const index = pointerIndex(event);
      if (index === null) {
        if (event.pointerType !== "touch") hideHover();
        return;
      }
      showHover(index);
    };
    svg.onpointermove = showFromPointer;
    svg.onpointerdown = showFromPointer;
    svg.onpointerleave = hideHover;
    svg.onpointercancel = hideHover;
    svg.onkeydown = (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      if (event.key === "Home") showHover(0);
      else if (event.key === "End") showHover(months.length - 1);
      else if (event.key === "ArrowLeft") showHover(activeIndex < 0 ? months.length - 1 : activeIndex - 1);
      else showHover(activeIndex < 0 ? 0 : activeIndex + 1);
    };
    svg.onblur = hideHover;
  }

  function bindEvents() {
    document.querySelectorAll("[data-industry-cumulative-period]").forEach((button) => {
      button.addEventListener("click", () => setPeriod(button.dataset.industryCumulativePeriod));
    });
    byId("applyIndustryCumulativeRange")?.addEventListener("click", () => {
      const start = byId("industryCumulativeStart")?.value || "";
      const end = byId("industryCumulativeEnd")?.value || "";
      if (!state.months.includes(start) || !state.months.includes(end) || start > end) {
        renderEmpty("시작월과 종료월을 자료 범위 안에서 올바르게 선택해 주세요.");
        return;
      }
      state.startMonth = start;
      state.endMonth = end;
      state.period = "CUSTOM";
      syncControls();
      renderChart();
    });
  }

  async function init() {
    bindEvents();
    try {
      const response = await fetch(DATA_URL, { cache: "no-cache" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      if (!Array.isArray(payload?.months) || !Array.isArray(payload?.industries)) {
        throw new Error("월별 업종 데이터 형식이 올바르지 않습니다.");
      }
      state.payload = payload;
      state.months = [...payload.months].sort((left, right) => left.localeCompare(right));
      setPeriod("1Y");
    } catch (error) {
      console.error("업종 누적 순매수 그래프 로딩 실패", error);
      renderEmpty(`누적 그래프 자료를 불러오지 못했습니다. (${error.message})`);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
