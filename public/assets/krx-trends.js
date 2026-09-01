"use strict";

const trendState = {
  index: null,
  quality: null,
  taxonomies: [],
  reports: [],
  period: "ALL",
  customStart: "",
  customEnd: "",
  method: "total",
  dailyPage: 1,
};

const DAILY_PAGE_SIZE = 15;
const REQUIRED_METHODS = ["competitive", "negotiated", "auction", "total"];
const CATEGORY_ORDER = [
  "liable_entities",
  "market_makers",
  "brokerage_members",
  "financial_institutions",
  "others",
  "koc_specialists",
];
const CATEGORY_LABELS = {
  liable_entities: "할당대상업체",
  market_makers: "시장조성자",
  brokerage_members: "거래중개회원",
  financial_institutions: "금융기관",
  others: "기타",
  koc_specialists: "KOC전문회원",
};
const TAXONOMY_CATEGORY_KEYS = {
  legacy_brokerage_members: [
    "liable_entities", "market_makers", "brokerage_members", "koc_specialists",
  ],
  financial_institutions_and_others: [
    "liable_entities", "market_makers", "financial_institutions", "others", "koc_specialists",
  ],
};
const TAXONOMY_LABELS = {
  legacy_brokerage_members: "거래중개회원 분류체계",
  financial_institutions_and_others: "금융기관·기타 분류체계",
};
const CATEGORY_COLORS = {
  liable_entities: "#0c7c59",
  market_makers: "#c59225",
  brokerage_members: "#6d72b8",
  financial_institutions: "#2e7bb4",
  others: "#9b6b43",
  koc_specialists: "#7b8791",
};
const CATEGORY_PATTERNS = {
  liable_entities: "solid",
  market_makers: "dashed",
  brokerage_members: "dotted",
  financial_institutions: "dashdot",
  others: "longdash",
  koc_specialists: "shortdash",
};
const CATEGORY_DASH_ARRAYS = {
  solid: "",
  dashed: "10 5",
  dotted: "2 5",
  dashdot: "10 4 2 4",
  longdash: "15 5",
  shortdash: "5 4",
};
const METHOD_LABELS = {
  total: "전체 거래",
  competitive: "경쟁매매",
  negotiated: "협의매매",
  auction: "경매",
};

const trendNumber = new Intl.NumberFormat("ko-KR");
const trendById = (id) => document.getElementById(id);

async function trendLoadJson(path) {
  const response = await fetch(`${path}?v=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response.json();
}

function trendEsc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

function trendDateMs(value) {
  return new Date(`${value}T00:00:00Z`).getTime();
}

function isIsoDate(value) {
  if (!/^20\d{2}-\d{2}-\d{2}$/.test(String(value || ""))) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

function trendShiftCalendarMonths(value, offset) {
  const [year, month, day] = value.split("-").map(Number);
  const monthIndex = year * 12 + month - 1 + offset;
  const targetYear = Math.floor(monthIndex / 12);
  const targetMonthIndex = ((monthIndex % 12) + 12) % 12;
  const lastDay = new Date(Date.UTC(targetYear, targetMonthIndex + 1, 0)).getUTCDate();
  return [
    String(targetYear).padStart(4, "0"),
    String(targetMonthIndex + 1).padStart(2, "0"),
    String(Math.min(day, lastDay)).padStart(2, "0"),
  ].join("-");
}

function trendAssert(condition, message) {
  if (!condition) throw new Error(message);
}

function sameValues(left, right) {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function normalizeTaxonomies(index) {
  const rows = Array.isArray(index?.taxonomy) ? index.taxonomy : [];
  trendAssert(rows.length > 0, "index.json: 참가자 분류 정보가 없습니다.");
  const ids = new Set();
  const normalized = rows.map((row, position) => {
    const id = String(row?.id || "");
    const expectedKeys = TAXONOMY_CATEGORY_KEYS[id];
    trendAssert(expectedKeys, `index.json: 알 수 없는 참가자 분류입니다 (${id || position + 1}).`);
    trendAssert(!ids.has(id), `index.json: 참가자 분류가 중복됐습니다 (${id}).`);
    ids.add(id);
    const from = String(row?.from || "");
    const to = row?.to === null ? null : String(row?.to || "");
    trendAssert(isIsoDate(from), `index.json: 참가자 분류 시작일 오류 (${id}).`);
    trendAssert(to === null || isIsoDate(to), `index.json: 참가자 분류 종료일 오류 (${id}).`);
    trendAssert(to === null || from <= to, `index.json: 참가자 분류 기간 오류 (${id}).`);
    const expectedLabels = expectedKeys.map((key) => CATEGORY_LABELS[key]);
    const labels = Array.isArray(row?.labels) ? row.labels.map(String) : [];
    trendAssert(sameValues(labels, expectedLabels), `index.json: 참가자 분류 항목 오류 (${id}).`);
    return { id, from, to, labels, categoryKeys: expectedKeys };
  }).sort((left, right) => left.from.localeCompare(right.from));
  normalized.forEach((row, indexPosition) => {
    if (!indexPosition) return;
    const previous = normalized[indexPosition - 1];
    trendAssert(previous.to !== null && previous.to < row.from, "index.json: 참가자 분류 기간이 겹칩니다.");
  });
  return normalized;
}

function taxonomyForDate(date, taxonomies) {
  const matches = taxonomies.filter((row) => row.from <= date && (row.to === null || date <= row.to));
  trendAssert(matches.length === 1, `거래일 ${date}: 적용할 참가자 분류를 하나로 정할 수 없습니다.`);
  return matches[0];
}

function tons(value, signed = false) {
  const rounded = Math.round(Number(value) || 0);
  const prefix = signed && rounded > 0 ? "+" : "";
  return `${prefix}${trendNumber.format(rounded)}톤`;
}

function compactTons(value, signed = false) {
  const numeric = Math.round(Number(value) || 0);
  const prefix = signed && numeric > 0 ? "+" : "";
  const absolute = Math.abs(numeric);
  if (absolute >= 1000000) return `${prefix}${(numeric / 1000000).toFixed(1)}백만톤`;
  if (absolute >= 10000) return `${prefix}${(numeric / 10000).toFixed(1)}만톤`;
  return tons(numeric, signed);
}

function normalizeTrendReports(monthPayloads, index, taxonomies) {
  const seen = new Map();
  const schemaVersion = String(index?.schemaVersion || "");
  trendAssert(schemaVersion, "index.json: schemaVersion이 없습니다.");
  monthPayloads.forEach(({ month, payload }) => {
    trendAssert(payload && typeof payload === "object", `${month}: 월별 데이터 형식 오류.`);
    trendAssert(String(payload.schemaVersion || "") === schemaVersion, `${month}: schemaVersion 불일치.`);
    trendAssert(payload.month === month, `${month}: 월 식별자가 일치하지 않습니다.`);
    trendAssert(Array.isArray(payload.items) && payload.items.length > 0, `${month}: 거래일 자료가 없습니다.`);
    payload.items.forEach((item) => {
      const date = String(item?.tradeDate || "");
      trendAssert(isIsoDate(date), `${month}: 거래일 형식 오류.`);
      trendAssert(date.slice(0, 7) === month, `${date}: 월별 파일 위치가 올바르지 않습니다.`);
      trendAssert(!seen.has(date), `${date}: 거래일 자료가 중복됐습니다.`);
      trendAssert(item.participantScope === "all_instruments", `${date}: 참가자 집계범위가 전체 종목이 아닙니다.`);
      const taxonomy = taxonomyForDate(date, taxonomies);
      trendAssert(item.taxonomyVersion === taxonomy.id, `${date}: 참가자 분류 버전이 일치하지 않습니다.`);
      const totalVolume = item?.market?.totalVolume;
      trendAssert(Number.isSafeInteger(totalVolume) && totalVolume >= 0, `${date}: 전체 거래량 오류.`);
      const instrument = item?.market?.representativeInstrument;
      trendAssert(instrument && typeof instrument === "object", `${date}: 대표 종목 정보가 없습니다.`);
      trendAssert(/^(?:KAU|KCU)\d+$/.test(String(instrument.symbol || "")), `${date}: 대표 종목 코드 오류.`);
      trendAssert(Number.isSafeInteger(instrument.volume) && instrument.volume >= 0, `${date}: 대표 종목 거래량 오류.`);
      trendAssert(instrument.volume <= totalVolume, `${date}: 대표 종목 거래량이 전체 거래량보다 큽니다.`);
      ["close", "change"].forEach((key) => {
        trendAssert(instrument[key] === null || Number.isSafeInteger(instrument[key]), `${date}: 대표 종목 ${key} 오류.`);
      });
      trendAssert(item?.validation?.balanced === true && item?.validation?.netTotal === 0, `${date}: 원천 검증 상태 오류.`);
      trendAssert(typeof item.sourcePageUrl === "string" && item.sourcePageUrl.startsWith("https://ets.krx.co.kr/"), `${date}: 원문 링크 오류.`);
      trendAssert(typeof item.filename === "string" && /\.pdf$/i.test(item.filename), `${date}: 원문 파일명 오류.`);
      const flows = Array.isArray(item.participantFlows) ? item.participantFlows : [];
      trendAssert(flows.length === taxonomy.categoryKeys.length, `${date}: 참가자 수가 분류체계와 다릅니다.`);
      const flowKeys = flows.map((flow) => String(flow?.categoryKey || ""));
      trendAssert(sameValues(flowKeys, taxonomy.categoryKeys), `${date}: 참가자 순서 또는 항목이 분류체계와 다릅니다.`);
      trendAssert(new Set(flowKeys).size === flowKeys.length, `${date}: 참가자 항목이 중복됐습니다.`);
      const normalizedFlows = flows.map((flow) => {
        const categoryKey = String(flow.categoryKey);
        trendAssert(flow.label === CATEGORY_LABELS[categoryKey], `${date}: 참가자 표시명이 올바르지 않습니다 (${categoryKey}).`);
        const netByMethod = flow.netByMethod;
        trendAssert(netByMethod && typeof netByMethod === "object" && !Array.isArray(netByMethod), `${date}: 거래구분 수급 형식 오류.`);
        const methodKeys = Object.keys(netByMethod).sort();
        trendAssert(sameValues(methodKeys, [...REQUIRED_METHODS].sort()), `${date}: 거래구분 항목이 정확하지 않습니다 (${flow.label}).`);
        REQUIRED_METHODS.forEach((method) => {
          trendAssert(Number.isSafeInteger(netByMethod[method]), `${date}: ${flow.label} ${METHOD_LABELS[method]} 수치 오류.`);
        });
        trendAssert(
          netByMethod.total === netByMethod.competitive + netByMethod.negotiated + netByMethod.auction,
          `${date}: ${flow.label} 전체 순거래량이 거래방식 합과 다릅니다.`,
        );
        return { categoryKey, label: flow.label, netByMethod: { ...netByMethod } };
      });
      REQUIRED_METHODS.forEach((method) => {
        const netSum = normalizedFlows.reduce((sum, flow) => sum + flow.netByMethod[method], 0);
        trendAssert(netSum === 0, `${date}: ${METHOD_LABELS[method]} 참가자 순거래량 합계가 0이 아닙니다.`);
      });
      seen.set(date, { ...item, tradeDate: date, participantFlows: normalizedFlows });
    });
  });
  return [...seen.values()].sort((left, right) => left.tradeDate.localeCompare(right.tradeDate));
}

function selectedTrendBounds() {
  const first = trendState.reports[0]?.tradeDate || "";
  const last = trendState.reports.at(-1)?.tradeDate || "";
  if (!first || !last) return { start: "", end: "" };
  if (trendState.period === "CUSTOM") {
    const requestedStart = trendState.customStart || first;
    const requestedEnd = trendState.customEnd || last;
    return {
      start: requestedStart < first ? first : requestedStart > last ? last : requestedStart,
      end: requestedEnd < first ? first : requestedEnd > last ? last : requestedEnd,
    };
  }
  if (trendState.period === "ALL") return { start: first, end: last };
  const months = { "1M": 1, "3M": 3, "6M": 6, "1Y": 12 }[trendState.period] || 3;
  const shifted = trendShiftCalendarMonths(last, -months);
  return { start: shifted < first ? first : shifted, end: last };
}

function selectedTrendReports() {
  const { start, end } = selectedTrendBounds();
  if (!start || !end) return [];
  return trendState.reports.filter((report) => report.tradeDate >= start && report.tradeDate <= end);
}

function aggregateParticipantFlows(reports) {
  const aggregates = new Map();
  reports.forEach((report) => {
    report.participantFlows.forEach((flow) => {
      const entry = aggregates.get(flow.categoryKey) || {
        categoryKey: flow.categoryKey,
        label: flow.label,
        net: 0,
        days: 0,
        firstDate: report.tradeDate,
        lastDate: report.tradeDate,
      };
      entry.net += Number(flow.netByMethod[trendState.method] || 0);
      entry.days += 1;
      entry.firstDate = entry.firstDate < report.tradeDate ? entry.firstDate : report.tradeDate;
      entry.lastDate = entry.lastDate > report.tradeDate ? entry.lastDate : report.tradeDate;
      aggregates.set(flow.categoryKey, entry);
    });
  });
  return [...aggregates.values()].sort((left, right) => {
    const leftIndex = CATEGORY_ORDER.indexOf(left.categoryKey);
    const rightIndex = CATEGORY_ORDER.indexOf(right.categoryKey);
    return (leftIndex < 0 ? 99 : leftIndex) - (rightIndex < 0 ? 99 : rightIndex);
  });
}

function formatShortDate(value) {
  return isIsoDate(value) ? value.slice(2).replaceAll("-", ".") : "-";
}

function formatAggregatePeriod(item) {
  if (!item?.firstDate || !item?.lastDate) return "집계기간 -";
  return `집계 ${formatShortDate(item.firstDate)}–${formatShortDate(item.lastDate)}`;
}

function setTrendAlert(kind, title, messages) {
  const alert = trendById("trendStatusAlert");
  const heading = trendById("trendStatusTitle");
  const detail = trendById("trendStatusDetail");
  const rows = (Array.isArray(messages) ? messages : [messages]).filter(Boolean);
  if (!rows.length) {
    alert.hidden = true;
    alert.className = "status-alert";
    alert.setAttribute("role", "status");
    heading.textContent = "";
    detail.textContent = "";
    return;
  }
  alert.hidden = false;
  alert.className = `status-alert ${kind}`;
  alert.setAttribute("role", kind === "error" ? "alert" : "status");
  heading.textContent = title;
  detail.textContent = rows.join(" ");
}

function renderCollectionWarnings() {
  const warnings = [];
  const quality = trendState.quality || {};
  const backfill = trendState.index?.backfill || {};
  const failures = Array.isArray(quality.failures) ? quality.failures.length : 0;
  if (quality.status === "partial") {
    warnings.push(`최근 수집에서 ${failures || "일부"}건을 처리하지 못해 검증된 기존 자료만 표시합니다.`);
  } else if (quality.status === "pending") {
    warnings.push("아직 KRX 수집 품질 검증이 완료되지 않았습니다.");
  } else if (quality.status !== "valid") {
    warnings.push(`알 수 없는 수집 품질 상태입니다 (${String(quality.status || "없음")}).`);
  }
  if (backfill.status === "in_progress") {
    warnings.push(`과거자료를 ${Number(backfill.progressPercent || 0).toFixed(1)}%까지 수집했습니다. 7일을 초과한 자료 공백은 차트 선을 끊어 표시합니다.`);
  } else if (backfill.status === "not_started") {
    warnings.push("과거자료 수집이 아직 시작되지 않았습니다.");
  } else if (backfill.status === "complete_with_errors") {
    warnings.push(`전체 게시물 확인은 끝났지만 ${Number(backfill.failureCount || failures)}건을 계속 재시도하고 있습니다.`);
  } else if (backfill.status !== "complete") {
    warnings.push(`알 수 없는 과거자료 수집 상태입니다 (${String(backfill.status || "없음")}).`);
  }
  setTrendAlert(warnings.length ? "warning" : "", warnings.length ? "자료 상태 안내" : "", warnings);
}

function setTrendControlsDisabled(disabled) {
  document.querySelectorAll("[data-trend-period], #trendStartDate, #trendEndDate, #applyTrendRange, #trendMethod")
    .forEach((control) => { control.disabled = disabled; });
}

function renderSyncState() {
  const index = trendState.index || {};
  const quality = trendState.quality || {};
  const sync = quality.lastSuccessAt || index.lastSync;
  const syncBox = document.querySelector(".sync-box");
  syncBox.classList.toggle("partial", quality.status === "partial");
  syncBox.classList.toggle("pending", quality.status === "pending");
  syncBox.classList.remove("error");
  trendById("trendSync").textContent = sync
    ? new Date(sync).toLocaleString("ko-KR", {
      timeZone: "Asia/Seoul", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
    })
    : "첫 수집 대기";
  const backfill = index.backfill || {};
  if (backfill.status === "complete") {
    const monthCount = Array.isArray(index.availableMonths) ? index.availableMonths.length : 0;
    trendById("backfillStatus").textContent = `${index.itemCount || 0}거래일 · ${monthCount}개월`;
  } else if (backfill.status === "complete_with_errors") {
    trendById("backfillStatus").textContent = `전기간 확인 완료 · 실패 ${Number(backfill.failureCount || 0)}건 재시도 중`;
  } else if (backfill.status === "in_progress") {
    trendById("backfillStatus").textContent = `과거자료 ${Number(backfill.progressPercent || 0).toFixed(1)}% 수집 중`;
  } else {
    trendById("backfillStatus").textContent = "과거자료 첫 수집 대기";
  }
}

function renderCollectionStatus(reports) {
  const allReports = trendState.reports;
  const quality = trendState.quality || {};
  const failureCount = Array.isArray(quality.failures) ? quality.failures.length : 0;
  const duplicateCount = allReports.length - new Set(allReports.map((report) => report.tradeDate)).size;
  const complete = trendState.index?.backfill?.status === "complete" && quality.status === "valid";

  trendById("collectionQuality").textContent = complete ? "검증 완료" : "확인 필요";
  trendById("collectionQualityDetail").textContent = `수집 오류 ${failureCount}건 · 중복 ${duplicateCount}건`;
  trendById("periodCoverage").textContent = `전체 ${allReports.length}거래일 중 ${reports.length}거래일 조회`;
}

function renderTrendControls(reports) {
  const bounds = selectedTrendBounds();
  const startInput = trendById("trendStartDate");
  const endInput = trendById("trendEndDate");
  if (trendState.reports.length) {
    startInput.min = trendState.reports[0].tradeDate;
    startInput.max = trendState.reports.at(-1).tradeDate;
    endInput.min = trendState.reports[0].tradeDate;
    endInput.max = trendState.reports.at(-1).tradeDate;
  }
  startInput.value = bounds.start;
  endInput.value = bounds.end;
  document.querySelectorAll("[data-trend-period]").forEach((button) => {
    const active = button.dataset.trendPeriod === trendState.period;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  trendById("trendMethod").value = trendState.method;
}

function renderTrendKpis(reports, aggregates) {
  const totalVolume = reports.reduce((sum, report) => sum + Number(report.market.totalVolume || 0), 0);
  const averageVolume = reports.length ? totalVolume / reports.length : 0;
  const buyer = [...aggregates].sort((left, right) => right.net - left.net)[0];
  const seller = [...aggregates].sort((left, right) => left.net - right.net)[0];
  trendById("periodTotalVolume").textContent = reports.length ? compactTons(totalVolume) : "-";
  trendById("periodAverageVolume").textContent = reports.length ? compactTons(averageVolume) : "-";
  trendById("periodTradingDays").textContent = `선택 ${reports.length}일 · 전체 ${trendState.reports.length}일`;
  trendById("topNetBuyer").textContent = buyer?.net > 0 ? buyer.label : "순매수 없음";
  trendById("topNetBuyerValue").textContent = buyer?.net > 0 ? tons(buyer.net, true) : "-";
  trendById("topNetBuyerPeriod").textContent = buyer?.net > 0 ? formatAggregatePeriod(buyer) : "집계기간 -";
  trendById("topNetSeller").textContent = seller?.net < 0 ? seller.label : "순매도 없음";
  trendById("topNetSellerValue").textContent = seller?.net < 0 ? tons(seller.net, true) : "-";
  trendById("topNetSellerPeriod").textContent = seller?.net < 0 ? formatAggregatePeriod(seller) : "집계기간 -";
  const first = reports[0]?.tradeDate;
  const last = reports.at(-1)?.tradeDate;
  trendById("selectedPeriodLabel").textContent = first && last
    ? `${first} – ${last} · ${METHOD_LABELS[trendState.method]}`
    : "수집된 자료 없음";
}

function niceFlowStep(value) {
  if (!Number.isFinite(value) || value <= 0) return 1000;
  const power = 10 ** Math.floor(Math.log10(value));
  const fraction = value / power;
  return (fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10) * power;
}

function renderFlowChart(reports) {
  const svg = trendById("flowChart");
  const legend = trendById("flowLegend");
  legend.replaceChildren();
  if (!reports.length) {
    svg.innerHTML = [
      '<title id="flowChartTitle">참가자별 누적 순거래량 추이</title>',
      '<desc id="flowChartDesc">선택 기간에 표시할 검증된 거래동향 자료가 없습니다.</desc>',
      '<text x="480" y="200" text-anchor="middle" class="empty-chart">수집된 거래동향 자료가 없습니다.</text>',
    ].join("");
    return;
  }

  const running = new Map();
  const series = new Map();
  reports.forEach((report) => {
    report.participantFlows.forEach((flow) => {
      const previous = running.get(flow.categoryKey) || 0;
      const next = previous + Number(flow.netByMethod[trendState.method] || 0);
      running.set(flow.categoryKey, next);
      const values = series.get(flow.categoryKey) || { label: flow.label, points: [] };
      values.points.push({ date: report.tradeDate, value: next });
      series.set(flow.categoryKey, values);
    });
  });
  const ordered = [...series.entries()].sort((left, right) => {
    const a = CATEGORY_ORDER.indexOf(left[0]);
    const b = CATEGORY_ORDER.indexOf(right[0]);
    return (a < 0 ? 99 : a) - (b < 0 ? 99 : b);
  });
  const allValues = [0, ...ordered.flatMap(([, value]) => value.points.map((point) => point.value))];
  const rawMin = Math.min(...allValues);
  const rawMax = Math.max(...allValues);
  const span = Math.max(Math.abs(rawMin), Math.abs(rawMax), 1000);
  const step = niceFlowStep(span / 3);
  const yMin = Math.floor(Math.min(rawMin, 0) / step) * step;
  const yMax = Math.ceil(Math.max(rawMax, 0) / step) * step || step;
  const width = 960, left = 82, right = 28, top = 28, bottom = 338;
  const firstTime = trendDateMs(reports[0].tradeDate);
  const lastTime = trendDateMs(reports.at(-1).tradeDate);
  const x = (date) => {
    const time = trendDateMs(date);
    return firstTime === lastTime
      ? (left + width - right) / 2
      : left + (time - firstTime) / (lastTime - firstTime) * (width - left - right);
  };
  const y = (value) => top + (yMax - value) / Math.max(yMax - yMin, 1) * (bottom - top);
  const gapCount = reports.slice(1).reduce((count, report, index) => (
    trendDateMs(report.tradeDate) - trendDateMs(reports[index].tradeDate) > 7 * 86400000 ? count + 1 : count
  ), 0);
  const chartDescription = `${reports[0].tradeDate}부터 ${reports.at(-1).tradeDate}까지 ${METHOD_LABELS[trendState.method]} 기준 ${ordered.length}개 참가자 누적 순거래량입니다.${gapCount ? ` 7일 초과 자료 공백 ${gapCount}곳은 선을 끊었습니다.` : ""} 상세 수치는 이어지는 기간 순거래량 표에서 확인할 수 있습니다.`;
  let html = `<title id="flowChartTitle">참가자별 누적 순거래량 추이</title><desc id="flowChartDesc">${trendEsc(chartDescription)}</desc>`;
  for (let value = yMin; value <= yMax + step * 0.01; value += step) {
    const py = y(value);
    html += `<line x1="${left}" x2="${width - right}" y1="${py}" y2="${py}" class="${value === 0 ? "zero-line" : "grid-line"}"/>`;
    html += `<text x="${left - 12}" y="${py + 4}" text-anchor="end" class="axis-text">${trendNumber.format(value)}</text>`;
  }
  const tickCount = Math.min(6, reports.length);
  const tickIndexes = new Set(Array.from({ length: tickCount }, (_, index) => Math.round(index * (reports.length - 1) / Math.max(tickCount - 1, 1))));
  [...tickIndexes].forEach((index) => {
    const report = reports[index];
    const px = x(report.tradeDate);
    html += `<text x="${px}" y="365" text-anchor="middle" class="axis-text">${trendEsc(report.tradeDate.slice(5).replace("-", "."))}</text>`;
  });
  trendState.taxonomies.slice(1).forEach((taxonomy) => {
    const changeDate = taxonomy.from;
    if (changeDate < reports[0].tradeDate || changeDate > reports.at(-1).tradeDate) return;
    const px = x(changeDate);
    html += `<line x1="${px}" x2="${px}" y1="${top}" y2="${bottom}" class="taxonomy-line"/>`;
    html += `<text x="${px + 7}" y="${top + 14}" class="taxonomy-label">${trendEsc(changeDate.slice(2).replaceAll("-", "."))} 분류 변경</text>`;
  });
  ordered.forEach(([key, value]) => {
    const color = CATEGORY_COLORS[key] || "#46515a";
    const pattern = CATEGORY_PATTERNS[key] || "solid";
    const dashArray = CATEGORY_DASH_ARRAYS[pattern] || "";
    const path = value.points.map((point, index) => {
      const previous = value.points[index - 1];
      const hasLongGap = previous && trendDateMs(point.date) - trendDateMs(previous.date) > 7 * 86400000;
      return `${!index || hasLongGap ? "M" : "L"}${x(point.date)},${y(point.value)}`;
    }).join(" ");
    html += `<path d="${path}" class="flow-line" style="stroke:${color};stroke-dasharray:${dashArray || "none"}"><title>${trendEsc(value.label)}</title></path>`;
    const last = value.points.at(-1);
    html += `<circle cx="${x(last.date)}" cy="${y(last.value)}" r="4" style="fill:${color}" class="flow-end"><title>${trendEsc(value.label)} ${tons(last.value, true)}</title></circle>`;
    const item = document.createElement("span");
    const marker = document.createElement("i");
    marker.className = `pattern-${pattern}`;
    marker.style.borderColor = color;
    item.append(marker, document.createTextNode(value.label));
    legend.append(item);
  });
  html += `<text x="16" y="${top}" class="axis-unit">톤</text>`;
  svg.innerHTML = html;
}

function renderParticipantTable(aggregates) {
  const tbody = trendById("participantRows");
  tbody.replaceChildren();
  if (!aggregates.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.textContent = "선택 기간에 수집된 참가자 자료가 없습니다.";
    row.append(cell); tbody.append(row);
    return;
  }
  aggregates.forEach((item) => {
    const row = document.createElement("tr");
    const values = [
      item.label,
      tons(item.net, true),
      tons(item.days ? item.net / item.days : 0, true),
      `${item.firstDate.slice(2).replaceAll("-", ".")}–${item.lastDate.slice(2).replaceAll("-", ".")}`,
    ];
    values.forEach((value, index) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      if (index === 1 || index === 2) cell.className = item.net > 0 ? "positive" : item.net < 0 ? "negative" : "";
      row.append(cell);
    });
    tbody.append(row);
  });
}

function renderDailyPagination(totalPages) {
  const nav = trendById("dailyPagination");
  nav.replaceChildren();
  nav.hidden = totalPages <= 1;
  if (totalPages <= 1) return;
  const addButton = (label, page, disabled = false, active = false, ariaLabel = "") => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.disabled = disabled;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    button.setAttribute("aria-label", ariaLabel || `${page}페이지${active ? ", 현재 페이지" : ""}`);
    button.addEventListener("click", () => { trendState.dailyPage = page; renderTrendPage(); });
    nav.append(button);
  };
  addButton("‹", trendState.dailyPage - 1, trendState.dailyPage === 1, false, "이전 페이지");
  const candidates = new Set([1, totalPages, trendState.dailyPage - 1, trendState.dailyPage, trendState.dailyPage + 1]);
  let previous = 0;
  [...candidates].filter((page) => page >= 1 && page <= totalPages).sort((a, b) => a - b).forEach((page) => {
    if (previous && page - previous > 1) {
      const gap = document.createElement("span"); gap.textContent = "…"; nav.append(gap);
    }
    addButton(String(page), page, false, page === trendState.dailyPage, `${page}페이지${page === trendState.dailyPage ? ", 현재 페이지" : ""}`);
    previous = page;
  });
  addButton("›", trendState.dailyPage + 1, trendState.dailyPage === totalPages, false, "다음 페이지");
}

function renderDailyTable(reports) {
  const tbody = trendById("dailyRows");
  tbody.replaceChildren();
  trendById("dailyRecordCount").textContent = `선택기간 ${reports.length}건 / 전체 ${trendState.reports.length}건 · ${METHOD_LABELS[trendState.method]}`;
  if (!reports.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td"); cell.colSpan = 6;
    cell.textContent = "선택 기간에 수집된 일별 자료가 없습니다.";
    row.append(cell); tbody.append(row); renderDailyPagination(1); return;
  }
  const descending = [...reports].reverse();
  const totalPages = Math.max(1, Math.ceil(descending.length / DAILY_PAGE_SIZE));
  trendState.dailyPage = Math.max(1, Math.min(trendState.dailyPage, totalPages));
  const start = (trendState.dailyPage - 1) * DAILY_PAGE_SIZE;
  descending.slice(start, start + DAILY_PAGE_SIZE).forEach((report) => {
    const instrument = report.market?.representativeInstrument || {};
    const row = document.createElement("tr");
    const cells = [
      report.tradeDate,
      instrument.symbol || "-",
      Number.isFinite(Number(instrument.close)) && instrument.close !== null ? `${trendNumber.format(instrument.close)}원` : "-",
      tons(report.market.totalVolume),
    ];
    cells.forEach((value) => {
      const cell = document.createElement("td"); cell.textContent = value;
      row.append(cell);
    });

    const playerCell = document.createElement("td");
    playerCell.className = "daily-player-cell";
    const playerFlows = document.createElement("div");
    playerFlows.className = "daily-player-flows";
    report.participantFlows.forEach((flow) => {
      const net = Number(flow.netByMethod[trendState.method] || 0);
      const item = document.createElement("span");
      item.className = `daily-player-flow ${net > 0 ? "positive-flow" : net < 0 ? "negative-flow" : "neutral-flow"}`;
      item.setAttribute("aria-label", `${flow.label} 순매매 ${tons(net, true)}`);
      const label = document.createElement("span");
      label.textContent = flow.label;
      const value = document.createElement("strong");
      value.textContent = tons(net, true);
      item.append(label, value);
      playerFlows.append(item);
    });
    playerCell.append(playerFlows);
    row.append(playerCell);

    const sourceCell = document.createElement("td");
    const source = document.createElement("a");
    source.className = "daily-source-link";
    source.href = report.sourcePageUrl;
    source.target = "_blank";
    source.rel = "noopener noreferrer";
    source.title = `${report.filename || report.title || "KRX 원문"} · 공식 게시글 열기`;
    source.setAttribute("aria-label", `${report.tradeDate} 한국거래소 공식 원문 게시글 열기, 새 창`);
    source.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M6.75 3.75h7l3.5 3.5v13H6.75z"/><path d="M13.75 3.75v3.5h3.5M9.5 11.25h5M9.5 14.25h5M9.5 17.25h3"/><path class="source-arrow" d="M15.5 10.5h4v4M19.5 10.5l-5 5"/></svg>';
    sourceCell.append(source); row.append(sourceCell); tbody.append(row);
  });
  renderDailyPagination(totalPages);
}

function renderTaxonomyNote(reports) {
  const note = trendById("taxonomyNote");
  if (!reports.length) {
    note.textContent = "자료 수집이 완료되면 참가자 분류 기준을 함께 표시합니다.";
    return;
  }
  const usedIds = [...new Set(reports.map((report) => report.taxonomyVersion))];
  if (usedIds.length > 1) {
    const transitions = trendState.taxonomies
      .filter((taxonomy) => taxonomy.from > reports[0].tradeDate && taxonomy.from <= reports.at(-1).tradeDate)
      .map((taxonomy) => taxonomy.from.replaceAll("-", "."));
    note.textContent = `${transitions.join(", ")} 참가자 분류 변경: 서로 다른 분류의 항목은 합산하지 않고 별도 선과 집계기간으로 표시합니다.`;
    return;
  }
  note.textContent = `해당 기간은 ${TAXONOMY_LABELS[usedIds[0]] || usedIds[0]}를 사용합니다.`;
}

function renderTrendPage() {
  const reports = selectedTrendReports();
  const aggregates = aggregateParticipantFlows(reports);
  renderTrendControls(reports);
  renderCollectionStatus(reports);
  renderTrendKpis(reports, aggregates);
  renderFlowChart(reports);
  renderParticipantTable(aggregates);
  renderDailyTable(reports);
  renderTaxonomyNote(reports);
}

function bindTrendControls() {
  document.querySelectorAll("[data-trend-period]").forEach((button) => {
    button.addEventListener("click", () => {
      trendState.period = button.dataset.trendPeriod;
      trendState.dailyPage = 1;
      renderTrendPage();
      renderCollectionWarnings();
    });
  });
  trendById("applyTrendRange").addEventListener("click", () => {
    const startInput = trendById("trendStartDate");
    const endInput = trendById("trendEndDate");
    const start = startInput.value;
    const end = endInput.value;
    const first = trendState.reports[0]?.tradeDate || "";
    const last = trendState.reports.at(-1)?.tradeDate || "";
    if (!start || !end || !startInput.checkValidity() || !endInput.checkValidity() || start > end || start < first || end > last) {
      setTrendAlert("error", "기간 입력 오류", `수집된 자료기간 ${first || "-"}부터 ${last || "-"} 사이에서 시작일과 종료일을 올바르게 선택해 주세요.`);
      startInput.focus();
      return;
    }
    renderCollectionWarnings();
    trendState.customStart = start;
    trendState.customEnd = end;
    trendState.period = "CUSTOM";
    trendState.dailyPage = 1;
    renderTrendPage();
  });
  trendById("trendMethod").addEventListener("change", (event) => {
    trendState.method = event.target.value;
    trendState.dailyPage = 1;
    renderTrendPage();
    renderCollectionWarnings();
  });
}

async function initKrxTrends() {
  bindTrendControls();
  const [index, quality] = await Promise.all([
    trendLoadJson("data/krx-daily/index.json"),
    trendLoadJson("data/krx-daily/quality.json"),
  ]);
  trendAssert(index && typeof index === "object", "index.json 형식이 올바르지 않습니다.");
  trendAssert(quality && typeof quality === "object", "quality.json 형식이 올바르지 않습니다.");
  trendAssert(String(quality.schemaVersion || "") === String(index.schemaVersion || ""), "quality.json schemaVersion이 일치하지 않습니다.");
  trendAssert(["pending", "partial", "valid"].includes(quality.status), "quality.json 상태값이 올바르지 않습니다.");
  trendAssert(Array.isArray(quality.failures), "quality.json failures 형식이 올바르지 않습니다.");
  trendAssert(quality.status !== "partial" || quality.failures.length > 0, "quality.json partial 상태에 실패 내역이 없습니다.");
  trendAssert(quality.status !== "valid" || quality.failures.length === 0, "quality.json valid 상태에 실패 내역이 남아 있습니다.");
  trendState.index = index;
  trendState.quality = quality;
  trendState.taxonomies = normalizeTaxonomies(index);
  const months = Array.isArray(index.availableMonths) ? index.availableMonths : [];
  trendAssert(new Set(months).size === months.length && months.every((month) => /^20\d{2}-(?:0[1-9]|1[0-2])$/.test(month)), "index.json availableMonths 형식 또는 중복 오류.");
  trendAssert(sameValues(months, [...months].sort()), "index.json availableMonths가 날짜순이 아닙니다.");
  trendAssert(Number.isSafeInteger(index.itemCount) && index.itemCount >= 0, "index.json itemCount 오류.");
  trendAssert((index.itemCount === 0) === (months.length === 0), "index.json의 itemCount와 availableMonths가 일치하지 않습니다.");
  const backfill = index.backfill;
  trendAssert(backfill && typeof backfill === "object", "index.json backfill 정보가 없습니다.");
  trendAssert(["not_started", "in_progress", "complete", "complete_with_errors"].includes(backfill.status), "index.json backfill 상태값 오류.");
  trendAssert(Number.isFinite(backfill.progressPercent) && backfill.progressPercent >= 0 && backfill.progressPercent <= 100, "index.json backfill 진행률 오류.");
  const results = await Promise.allSettled(
    months.map((month) => trendLoadJson(`data/krx-daily/by-month/${month}.json`))
  );
  const failedMonths = results
    .map((result, indexPosition) => ({ result, month: months[indexPosition] }))
    .filter(({ result }) => result.status === "rejected")
    .map(({ month }) => month);
  trendAssert(!failedMonths.length, `월별 거래동향 파일을 불러오지 못했습니다: ${failedMonths.join(", ")}`);
  const loadedMonths = results.map((result, indexPosition) => ({ month: months[indexPosition], payload: result.value }));
  trendState.reports = normalizeTrendReports(loadedMonths, index, trendState.taxonomies);
  trendAssert(trendState.reports.length === index.itemCount, `거래일 건수가 index.json과 다릅니다 (${trendState.reports.length}/${index.itemCount}).`);
  if (trendState.reports.length) {
    trendAssert(index.firstTradeDate === trendState.reports[0].tradeDate, "index.json 최초 거래일이 월별 데이터와 다릅니다.");
    trendAssert(index.lastTradeDate === trendState.reports.at(-1).tradeDate, "index.json 최신 거래일이 월별 데이터와 다릅니다.");
  }
  setTrendControlsDisabled(false);
  renderSyncState();
  renderTrendPage();
  renderCollectionWarnings();
  trendById("trendDashboard").setAttribute("aria-busy", "false");
}

function renderFatalError(error) {
  trendState.reports = [];
  trendState.period = "ALL";
  renderTrendPage();
  setTrendControlsDisabled(true);
  document.querySelector(".sync-box").className = "sync-box error";
  trendById("trendSync").textContent = "자료 연결 오류";
  trendById("backfillStatus").textContent = "확인 실패";
  trendById("collectionQuality").textContent = "검증 실패";
  trendById("collectionQualityDetail").textContent = "공개 데이터 연결 상태를 확인해 주세요.";
  trendById("periodCoverage").textContent = "전체자료를 표시할 수 없습니다.";
  trendById("dailyRecordCount").textContent = "자료 오류";
  trendById("participantRows").innerHTML = '<tr><td colspan="4">데이터 검증 오류로 기간 수급을 표시할 수 없습니다.</td></tr>';
  trendById("dailyRows").innerHTML = '<tr><td colspan="6">데이터 검증 오류로 일별 자료를 표시할 수 없습니다.</td></tr>';
  trendById("flowChart").innerHTML = [
    '<title id="flowChartTitle">KRX 거래동향 자료 오류</title>',
    '<desc id="flowChartDesc">데이터 연결 또는 검증 오류로 차트를 표시할 수 없습니다.</desc>',
    '<text x="480" y="200" text-anchor="middle" class="empty-chart">KRX 거래동향 데이터를 검증하지 못했습니다.</text>',
  ].join("");
  setTrendAlert("error", "KRX 거래동향 자료 오류", error instanceof Error ? error.message : String(error));
  trendById("trendDashboard").setAttribute("aria-busy", "false");
}

initKrxTrends().catch(renderFatalError);
