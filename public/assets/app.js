"use strict";
// POLICY_PAGINATION_VERSION = "2026-08-20-v1.1-volume"
// BRIEFING_ARCHIVE_VERSION = "2026-08-20-v1"

const state = { prices: [], policies: [], policyInsight: null, briefings: [], briefingDate: "", period: "3M", category: "기후부 보도자료", policyPage: 1, symbol: "" };
const POLICIES_PER_PAGE = 5;
const fallbackPrice = [{ date: "2026-08-19", symbol: "KAU25", close: 29500, change: 1150, changeRate: 4.06, open: 29000, high: 29500, low: 29000, volume: 396644, tradeValue: 11624058550 }];
const number = new Intl.NumberFormat("ko-KR");
const byId = (id) => document.getElementById(id);

function parseCsvLine(line, delimiter) {
  const cells = []; let cell = ""; let quoted = false;
  for (let i = 0; i < line.length; i += 1) {
    const char = line[i];
    if (char === '"' && quoted && line[i + 1] === '"') { cell += '"'; i += 1; }
    else if (char === '"') quoted = !quoted;
    else if (char === delimiter && !quoted) { cells.push(cell.trim()); cell = ""; }
    else cell += char;
  }
  cells.push(cell.trim());
  return cells;
}

function parsePrices(csv) {
  const lines = csv.replace(/^\uFEFF/, "").split(/\r?\n/).filter((line) => line.trim());
  if (lines.length < 2) return [];
  const delimiter = lines[0].includes("\t") ? "\t" : ",";
  const headers = parseCsvLine(lines[0], delimiter).map((value) => value.trim().toLowerCase());
  const field = (row, names) => {
    const index = headers.findIndex((header) => names.includes(header));
    return index >= 0 ? row[index] : "";
  };
  const num = (value) => Number(String(value || 0).replace(/[,%원톤\s]/g, "")) || 0;
  return lines.slice(1).map((line) => {
    const row = parseCsvLine(line, delimiter);
    return {
      date: field(row, ["date", "trade_date", "거래일", "기준일", "기준일자"]),
      symbol: field(row, ["symbol", "종목", "종목명"]) || "KAU25",
      close: num(field(row, ["close", "현재가", "종가"])),
      change: num(field(row, ["change", "대비"])),
      changeRate: num(field(row, ["change_rate", "등락률", "등락률(%)"])),
      open: num(field(row, ["open", "시가"])), high: num(field(row, ["high", "고가"])), low: num(field(row, ["low", "저가"])),
      volume: num(field(row, ["volume", "거래량", "거래량(톤)"])), tradeValue: num(field(row, ["trade_value", "거래대금", "거래대금(원)"]))
    };
  }).filter((row) => /^\d{4}-\d{2}-\d{2}$/.test(row.date) && row.close > 0).sort((a, b) => a.date.localeCompare(b.date));
}

async function loadText(path) {
  const response = await fetch(`${path}?v=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response.text();
}

async function loadJson(path) {
  const response = await fetch(`${path}?v=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response.json();
}

function filterPrices(period) {
  const selected = state.prices.filter((row) => !state.symbol || row.symbol === state.symbol);
  if (period === "ALL" || selected.length < 2) return selected;
  const days = { "1M": 31, "3M": 93, "6M": 186, "1Y": 366 }[period];
  const end = new Date(`${selected.at(-1).date}T00:00:00`).getTime();
  return selected.filter((row) => end - new Date(`${row.date}T00:00:00`).getTime() <= days * 86400000);
}

function money(value) { return number.format(Math.round(value || 0)); }
function amount(value) { return value >= 1e8 ? `${(value / 1e8).toFixed(1)}억원` : `${money(value)}원`; }
function shortDate(value) { return value ? value.slice(2).replaceAll("-", ".") : "-"; }
function periodName(value) { return ({ "1M": "1개월", "3M": "3개월", "6M": "6개월", "1Y": "1년", "ALL": "전체" })[value]; }
function esc(value) { return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]); }
function chartDateTime(value) { return new Date(`${value}T00:00:00`).getTime(); }
function semiMonthlyTicks(start, end) {
  if (!start || !end) return [];
  const first = new Date(`${start}T00:00:00`), last = new Date(`${end}T00:00:00`);
  const ticks = [];
  for (let month = new Date(first.getFullYear(), first.getMonth(), 1); month <= last; month = new Date(month.getFullYear(), month.getMonth() + 1, 1)) {
    [1, 15].forEach((day) => {
      const tick = new Date(month.getFullYear(), month.getMonth(), day);
      if (tick >= first && tick <= last) ticks.push({ date: `${tick.getFullYear()}-${String(tick.getMonth() + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`, label: `${tick.getMonth() + 1}.${day}` });
    });
  }
  return ticks;
}

function policyGroup(policy) {
  const source = String(policy.source || "");
  const url = String(policy.url || "");
  if (policy.section === "news" || policy.sourceType === "news") return "뉴스";
  if (policy.section === "press") return "기후부 보도자료";
  if (policy.section === "notice") return "기후부 공지사항";
  if (source.includes("보도자료") || /(?:menuId=(?:286|10598)|boardMasterId=(?:1|939))(?:&|$)/.test(url)) return "기후부 보도자료";
  if (source.includes("공지") || source.includes("공고") || policy.sourceType === "official") return "기후부 공지사항";
  return "뉴스";
}

function derivePolicyInsight(policies) {
  const official = policies.filter((policy) => policyGroup(policy) !== "뉴스").slice(0, 10);
  if (!official.length) return { summary: "최근 기후부 공식자료가 수집되면 정책 변화가 시장 수급에 미칠 영향을 분석합니다.", basisLatestDate: "-", basisCount: 0, source: "fallback" };
  const haystack = official.map((policy) => `${policy.title} ${policy.summary || ""}`).join(" ");
  const activeStabilization = official.some((policy) => {
    const text = `${policy.title} ${policy.summary || ""}`.replace(/\s+/g, " ");
    return /(?:시장안정(?:화)?|시장안정화예비분|예비분|K-MSR).{0,100}(?:발동|추가\s*공급|공급\s*결정|방출|조정\s*물량|매각\s*공고)|(?:발동|추가\s*공급|공급\s*결정|방출|조정\s*물량|매각\s*공고).{0,100}(?:시장안정(?:화)?|시장안정화예비분|예비분|K-MSR)/i.test(text);
  });
  let summary = "최근 공식자료는 제도 운영 구체화에 초점이 맞춰져 있습니다. 단기 방향을 단정하기보다 후속 일정과 실제 공급물량 변화를 확인해야 합니다.";
  if (activeStabilization) summary = "최근 공식자료에서 시장안정화 조치의 실제 발동이 확인됐습니다. 발표된 조정물량이 현물 수급에 미칠 영향을 확인해야 합니다.";
  else if (/유상|경매|입찰/.test(haystack)) summary = "최근 공식자료의 직접적인 수급 변수는 유상경매입니다. 최근 경매가 높은 낙찰가로 소화돼 공급보다 이행수요가 강하다는 신호입니다.";
  else if (/할당|계획기간|배출허용총량/.test(haystack)) summary = "최근 공식자료는 할당체계와 차기 계획기간 운영 구체화에 집중돼 있습니다. 중기 수급 기대가 바뀔 수 있어 할당량, 유상할당 비중과 시행시점을 함께 확인해야 합니다.";
  else if (/상쇄|외부사업/.test(haystack)) summary = "최근 공식자료는 상쇄배출권과 외부사업 공급 기반 확대에 초점이 맞춰져 있습니다. 실제 인증·발행 물량이 늘어나는 시점까지는 현물 공급 효과가 제한적일 수 있습니다.";
  return { summary, basisLatestDate: official[0].publishedAt || "-", basisCount: official.length, source: "fallback" };
}

function normalizedNewsTitle(value) {
  return String(value || "").toLowerCase().replace(/\[[^\]]+\]|\([^)]*\)/g, " ").replace(/[^0-9a-z가-힣]+/g, "");
}

function newsTitleDice(first, second) {
  const a = normalizedNewsTitle(first), b = normalizedNewsTitle(second);
  if (!a || !b) return 0;
  if (a === b) return 1;
  const grams = (value) => { const result = []; for (let index = 0; index < value.length - 1; index += 1) result.push(value.slice(index, index + 2)); return result; };
  const left = grams(a), right = grams(b), counts = new Map();
  left.forEach((gram) => counts.set(gram, (counts.get(gram) || 0) + 1));
  let overlap = 0;
  right.forEach((gram) => { if ((counts.get(gram) || 0) > 0) { overlap += 1; counts.set(gram, counts.get(gram) - 1); } });
  return 2 * overlap / Math.max(left.length + right.length, 1);
}

function similarNews(first, second) {
  const a = normalizedNewsTitle(first), b = normalizedNewsTitle(second);
  const [shorter, longer] = [a, b].sort((left, right) => left.length - right.length);
  return a === b || (shorter.length >= 14 && longer.includes(shorter) && shorter.length / longer.length >= .55) || newsTitleDice(a, b) >= .58;
}

function dedupeNewsPolicies(policies) {
  const official = policies.filter((policy) => policyGroup(policy) !== "뉴스");
  const remaining = policies.filter((policy) => policyGroup(policy) === "뉴스").slice();
  const representatives = [];
  while (remaining.length) {
    const group = [remaining.shift()];
    let changed = true;
    while (changed) {
      changed = false;
      for (let index = remaining.length - 1; index >= 0; index -= 1) {
        const candidate = remaining[index];
        if (candidate.publishedAt === group[0].publishedAt && group.some((member) => similarNews(candidate.title, member.title))) {
          group.push(candidate); remaining.splice(index, 1); changed = true;
        }
      }
    }
    const representative = group.reduce((best, item) => (`${item.summary || ""}${item.title || ""}`).length > (`${best.summary || ""}${best.title || ""}`).length ? item : best);
    representatives.push({ ...representative, duplicateCount: group.length, duplicateSources: [...new Set(group.map((item) => item.source).filter(Boolean))] });
  }
  return [...official, ...representatives].sort((a, b) => b.publishedAt.localeCompare(a.publishedAt));
}

function marketInsight(selected, latest) {
  const history = selected.slice(-21, -1);
  if (!history.length) return "시세가 누적되면 가격 방향과 최근 거래량 강도를 함께 분석합니다.";
  const averageVolume = history.reduce((sum, row) => sum + row.volume, 0) / history.length;
  const volumeRatio = averageVolume ? latest.volume / averageVolume : 1;
  const volumeText = volumeRatio >= 1.3 ? "최근 평균을 크게 웃도는 거래량" : volumeRatio <= .7 ? "최근 평균보다 낮은 거래량" : "평균 수준의 거래량";
  if (latest.changeRate >= 2 && volumeRatio >= 1.3) return `가격 상승이 ${volumeText}을 동반해 매수 강도가 확대됐습니다. 후속 거래량과 매수세 지속 여부를 확인하세요.`;
  if (latest.changeRate >= 2) return `가격은 강하게 상승했지만 거래량은 ${volumeText}입니다. 추가 매수 유입이 확인돼야 상승 추세의 신뢰도가 높아집니다.`;
  if (latest.changeRate <= -2 && volumeRatio >= 1.3) return `가격 하락이 ${volumeText}을 동반해 매도 압력이 강화됐습니다. 저가 매수 유입과 공급물량 변화를 점검하세요.`;
  if (latest.changeRate <= -2) return `가격은 하락했지만 거래량은 ${volumeText}입니다. 적극적 매도보다 관망 또는 유동성 부족의 영향인지 확인이 필요합니다.`;
  if (Math.abs(latest.changeRate) < .5 && volumeRatio <= .7) return "가격과 거래량이 모두 제한돼 관망 국면 가능성이 높습니다. 정책 발표나 경매 결과에 따른 거래량 회복 여부를 확인하세요.";
  if (latest.changeRate >= 0) return `완만한 상승과 ${volumeText}이 나타났습니다. 거래량 확대가 동반되는지 확인해야 추세 지속 여부를 판단할 수 있습니다.`;
  return `완만한 약세와 ${volumeText}이 나타났습니다. 추가 하락보다 저가 매수 유입과 거래량 변화를 우선 확인하세요.`;
}

function renderMarket() {
  const rows = filterPrices(state.period);
  const selected = state.prices.filter((row) => !state.symbol || row.symbol === state.symbol);
  const latest = selected.at(-1) || fallbackPrice[0];
  const endTime = new Date(`${latest.date}T00:00:00`).getTime();
  const yearRows = selected.filter((row) => endTime - new Date(`${row.date}T00:00:00`).getTime() <= 366 * 86400000);
  const yearHigh = Math.max(...(yearRows.length ? yearRows : [latest]).map((row) => row.high || row.close));
  const yearLow = Math.min(...(yearRows.length ? yearRows : [latest]).map((row) => row.low || row.close));
  const periodChange = rows.length > 1 ? (latest.close / rows[0].close - 1) * 100 : latest.changeRate;
  const direction = latest.changeRate >= 0 ? "up" : "down";
  const sign = latest.changeRate >= 0 ? "+" : "";
  byId("asofDate").textContent = latest.date.replaceAll("-", "."); byId("symbol").textContent = latest.symbol;
  byId("currentPrice").textContent = money(latest.close);
  byId("dailyChange").className = `change ${direction}`;
  byId("dailyChange").textContent = `${latest.changeRate >= 0 ? "▲" : "▼"} ${money(Math.abs(latest.change))}원  ${Math.abs(latest.changeRate).toFixed(2)}%`;
  byId("rangeLow").textContent = `52주 저 ${money(yearLow)}`; byId("rangeHigh").textContent = `52주 고 ${money(yearHigh)}`;
  byId("rangeMarker").style.left = `${Math.max(2, Math.min(98, (latest.close - yearLow) / Math.max(yearHigh - yearLow, 1) * 100))}%`;
  byId("volume").textContent = money(latest.volume); byId("tradeValue").textContent = `거래대금 ${amount(latest.tradeValue)}`;
  byId("periodChange").textContent = `${periodChange >= 0 ? "+" : ""}${periodChange.toFixed(2)}%`;
  byId("periodChange").className = `kpi-number ${periodChange >= 0 ? "positive" : "negative"}`;
  byId("periodLabel").textContent = `${periodName(state.period)} 기준`; byId("recordCount").textContent = `${rows.length}개 거래일`;
  byId("dayRange").textContent = `${money(latest.low)}–${money(latest.high)}`; byId("openPrice").textContent = `시가 ${money(latest.open)}원`;
  byId("pulseChange").textContent = `${sign}${latest.changeRate.toFixed(2)}%`; byId("pulseChange").className = latest.changeRate >= 0 ? "positive" : "negative";
  byId("changeTrack").style.width = `${Math.min(Math.abs(latest.changeRate) * 12, 100)}%`; byId("pulseVolume").textContent = `${money(latest.volume)}톤`;
  byId("checkPoint").textContent = marketInsight(selected, latest);
  renderChart(rows);
}

function renderChart(rows) {
  const svg = byId("priceChart");
  const width = 900, left = 62, right = 22, top = 24, priceBottom = 245, volumeTop = 275, bottom = 326;
  const values = rows.map((row) => row.close);
  const maxPrice = Math.max(...values, 30000), minPrice = Math.min(...values, 20000), pad = Math.max((maxPrice - minPrice) * .16, 800);
  const yMax = maxPrice + pad, yMin = Math.max(0, minPrice - pad), maxVol = Math.max(...rows.map((row) => row.volume), 1);
  const start = rows[0]?.date || "", end = rows.at(-1)?.date || "";
  const startTime = chartDateTime(start), endTime = chartDateTime(end);
  // 금융 차트처럼 실제 거래일만 같은 간격으로 배치합니다.
  // 주말·공휴일은 빈 거래량 막대로 오해되지 않도록 축에서 압축합니다.
  const x = (index) => rows.length <= 1 ? left + (width - left - right) / 2 : left + index / (rows.length - 1) * (width - left - right);
  const dateX = (date) => {
    if (rows.length <= 1) return x(0);
    const target = chartDateTime(date);
    if (target <= startTime) return x(0);
    if (target >= endTime) return x(rows.length - 1);
    const rightIndex = rows.findIndex((row) => chartDateTime(row.date) >= target);
    if (rightIndex <= 0) return x(0);
    const leftIndex = rightIndex - 1;
    const leftTime = chartDateTime(rows[leftIndex].date);
    const rightTime = chartDateTime(rows[rightIndex].date);
    const ratio = (target - leftTime) / Math.max(rightTime - leftTime, 1);
    return x(leftIndex) + (x(rightIndex) - x(leftIndex)) * ratio;
  };
  const y = (value) => top + (yMax - value) * (priceBottom - top) / Math.max(yMax - yMin, 1);
  let policyEvents = [];
  let html = `<defs><linearGradient id="areaGradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#0c7c59" stop-opacity=".2"/><stop offset="100%" stop-color="#0c7c59" stop-opacity="0"/></linearGradient><linearGradient id="lineGradient"><stop offset="0%" stop-color="#13a979"/><stop offset="100%" stop-color="#075f48"/></linearGradient></defs>`;
  for (let i = 0; i < 5; i += 1) { const gy = top + i * (priceBottom - top) / 4; const value = yMax - i * (yMax - yMin) / 4; html += `<line x1="${left}" x2="${width-right}" y1="${gy}" y2="${gy}" class="grid-line"/><text x="${left-12}" y="${gy+4}" text-anchor="end" class="axis-text">${Math.round(value/100)*100}</text>`; }
  if (rows.length > 1) semiMonthlyTicks(start, end).forEach((tick) => { const px = dateX(tick.date); html += `<line x1="${px}" x2="${px}" y1="${top}" y2="${bottom}" class="date-grid-line"/><text x="${px}" y="346" text-anchor="middle" class="axis-text chart-date-text">${tick.label}</text>`; });
  if (rows.length > 1) { const line = rows.map((row, index) => `${index ? "L" : "M"}${x(index)},${y(row.close)}`).join(" "); html += `<path d="${line} L${x(rows.length-1)},${priceBottom} L${x(0)},${priceBottom} Z" class="price-area"/><path d="${line}" class="price-line"/>`; }
  rows.forEach((row, index) => { const barWidth = Math.min(15, (width-left-right)/Math.max(rows.length,12)*.66); const barHeight = row.volume/maxVol*(bottom-volumeTop); html += `<rect x="${x(index)-barWidth/2}" y="${bottom-barHeight}" width="${barWidth}" height="${barHeight}" rx="2" class="volume-bar" style="fill:#b7d4ca"><title>${esc(row.date)} · 종가 ${money(row.close)}원 · 거래량 ${money(row.volume)}톤</title></rect><circle cx="${x(index)}" cy="${y(row.close)}" r="${rows.length===1?6:3.5}" class="price-dot"><title>${esc(row.date)} · 종가 ${money(row.close)}원 · 거래량 ${money(row.volume)}톤</title></circle>`; });
  if (rows.length > 1) {
    policyEvents = state.policies.filter((policy) => policyGroup(policy) !== "뉴스" && policy.publishedAt >= start && policy.publishedAt <= end);
    policyEvents.forEach((policy, index) => {
      const px = dateX(policy.publishedAt);
      const sameDateIndex = policyEvents.slice(0, index).filter((item) => item.publishedAt === policy.publishedAt).length;
      const markerY = top + 8 + sameDateIndex % 3 * 15;
      html += `<g class="policy-event" data-event-index="${index}" tabindex="0" role="button" aria-label="${esc(`${policy.publishedAt} 정책 발표: ${policy.title}`)}"><line x1="${px}" x2="${px}" y1="${top}" y2="${priceBottom}" class="event-line"/><circle cx="${px}" cy="${markerY}" r="12" class="event-hit"/><circle cx="${px}" cy="${markerY}" r="5" class="event-dot"/></g>`;
    });
  }
  html += `<text x="${left-12}" y="${volumeTop+5}" text-anchor="end" class="axis-unit">거래량</text>`;
  if (rows.length <= 1) html += `<text x="450" y="220" text-anchor="middle" class="empty-chart-text">전기간 시세를 등록하면 가격선이 표시됩니다</text>`;
  svg.innerHTML = html;
  const tooltip = byId("chartTooltip");
  const positionTooltip = (event, verticalOffset = 84) => {
    const wrapRect = svg.parentElement.getBoundingClientRect();
    tooltip.hidden = false;
    const maxLeft = Math.max(8, wrapRect.width - tooltip.offsetWidth - 8);
    tooltip.style.left = `${Math.min(Math.max(8, event.clientX - wrapRect.left + 12), maxLeft)}px`;
    tooltip.style.top = `${Math.max(8, event.clientY - wrapRect.top - verticalOffset)}px`;
  };
  const showPolicyTooltip = (policy, event) => {
    tooltip.classList.add("policy-tooltip");
    tooltip.replaceChildren(
      create("time", "", `${policy.publishedAt} · ${policyGroup(policy)}`),
      create("strong", "", policy.title),
      create("span", "", policy.summary || "원문에서 세부 내용을 확인하세요.")
    );
    positionTooltip(event, 108);
  };
  const handlePointer = (event) => {
    const eventNode = event.target.closest?.(".policy-event");
    if (eventNode) {
      const policy = policyEvents[Number(eventNode.dataset.eventIndex)];
      if (policy) showPolicyTooltip(policy, event);
      return;
    }
    if (!rows.length) return;
    const svgRect = svg.getBoundingClientRect();
    const pointerX = (event.clientX - svgRect.left) / Math.max(svgRect.width, 1) * width;
    const ratio = Math.max(0, Math.min(1, (pointerX - left) / (width - left - right)));
    const index = rows.length === 1 ? 0 : Math.round(ratio * (rows.length - 1));
    const row = rows[index];
    tooltip.classList.remove("policy-tooltip");
    tooltip.replaceChildren(
      create("time", "", row.date),
      create("strong", "", `${money(row.close)}원`),
      create("span", "", `거래량 ${money(row.volume)}톤`)
    );
    positionTooltip(event);
  };
  svg.onpointermove = handlePointer;
  svg.onpointerdown = handlePointer;
  svg.onfocusin = (event) => {
    const eventNode = event.target.closest?.(".policy-event");
    const policy = eventNode ? policyEvents[Number(eventNode.dataset.eventIndex)] : null;
    if (!policy) return;
    const marker = eventNode.querySelector(".event-dot").getBoundingClientRect();
    showPolicyTooltip(policy, { clientX: marker.left + marker.width / 2, clientY: marker.top + marker.height / 2 });
  };
  svg.onfocusout = () => { tooltip.hidden = true; };
  svg.onpointerleave = () => { tooltip.hidden = true; };
}

function create(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }

function policyPageNumbers(current, total) {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1);
  const pages = new Set([1, total, current - 1, current, current + 1]);
  const sorted = [...pages].filter((page) => page >= 1 && page <= total).sort((a, b) => a - b);
  const result = [];
  sorted.forEach((page, index) => {
    if (index && page - sorted[index - 1] > 1) result.push("…");
    result.push(page);
  });
  return result;
}

function renderPolicyPagination(list, totalPages) {
  if (totalPages <= 1) return;
  const pagination = create("nav", "policy-pagination");
  pagination.ariaLabel = `${state.category} 페이지 이동`;
  const move = (page) => {
    state.policyPage = Math.max(1, Math.min(totalPages, page));
    renderPolicies();
  };
  const previous = create("button", "page-arrow", "‹");
  previous.type = "button"; previous.disabled = state.policyPage === 1; previous.ariaLabel = "이전 페이지";
  previous.addEventListener("click", () => move(state.policyPage - 1));
  pagination.append(previous);
  policyPageNumbers(state.policyPage, totalPages).forEach((value) => {
    if (value === "…") { pagination.append(create("span", "page-gap", value)); return; }
    const button = create("button", value === state.policyPage ? "active" : "", String(value));
    button.type = "button"; button.ariaLabel = `${value}페이지`;
    if (value === state.policyPage) button.setAttribute("aria-current", "page");
    button.addEventListener("click", () => move(value));
    pagination.append(button);
  });
  const next = create("button", "page-arrow", "›");
  next.type = "button"; next.disabled = state.policyPage === totalPages; next.ariaLabel = "다음 페이지";
  next.addEventListener("click", () => move(state.policyPage + 1));
  pagination.append(next);
  list.append(pagination);
}

function renderPolicies() {
  const items = state.policies.filter((item) => policyGroup(item) === state.category);
  const list = byId("policyList"); list.replaceChildren();
  const insight = state.policyInsight?.summary ? state.policyInsight : derivePolicyInsight(state.policies);
  byId("latestPolicy").textContent = insight.summary;
  byId("latestPolicyDate").textContent = insight.basisCount ? `AI 공식자료 종합 · ${insight.basisLatestDate} · ${insight.basisCount}건` : "공식자료 수집 대기";
  if (!items.length) { state.policyPage = 1; list.append(create("div", "loading-state", `${state.category}에 해당하는 자료가 없습니다.`)); renderChart(filterPrices(state.period)); return; }
  const totalPages = Math.ceil(items.length / POLICIES_PER_PAGE);
  state.policyPage = Math.max(1, Math.min(state.policyPage, totalPages));
  const start = (state.policyPage - 1) * POLICIES_PER_PAGE;
  items.slice(start, start + POLICIES_PER_PAGE).forEach((policy) => {
    const row = create("article", "policy-row");
    row.append(create("time", "", shortDate(policy.publishedAt)), create("span", "category-badge", policy.category || "기타"));
    const body = create("div", "policy-body");
    const title = create("h3", "", policy.title); title.title = policy.title;
    const summaryText = policy.summary || "원문에서 세부 내용을 확인하세요.";
    const summary = create("p", "", summaryText); summary.title = summaryText;
    body.append(title, summary);
    const link = create("a", "source-link", "∞"); link.href = policy.url || "#"; link.target = "_blank"; link.rel = "noreferrer"; link.ariaLabel = `${policy.title} 원문 링크`; link.title = "원문 링크";
    row.append(body, link); list.append(row);
  });
  renderPolicyPagination(list, totalPages);
  renderChart(filterPrices(state.period));
}

function renderPolicyFilters() {
  const categories = ["기후부 보도자료", "기후부 공지사항", "뉴스"];
  const box = byId("policyFilters"); box.replaceChildren();
  categories.forEach((category) => { const button = create("button", category === state.category ? "active" : "", category); button.addEventListener("click", () => { state.category = category; state.policyPage = 1; renderPolicyFilters(); renderPolicies(); }); box.append(button); });
  byId("policyDescription").textContent = "기후부 공식 보도자료·공지사항과 시장 뉴스의 제목·본문을 기준으로 자동 수집합니다.";
}

function renderSymbolPicker() {
  const symbols = [...new Set(state.prices.map((row) => row.symbol).filter(Boolean))].sort();
  const picker = byId("symbolPicker"), select = byId("symbolSelect");
  select.replaceChildren();
  symbols.forEach((symbol) => { const option = create("option", "", symbol); option.value = symbol; option.selected = symbol === state.symbol; select.append(option); });
  picker.hidden = symbols.length <= 1;
  select.addEventListener("change", () => { state.symbol = select.value; renderMarket(); });
}

function readableBriefingText(value) {
  const raw = String(value || "");
  if (!raw.includes("<")) return raw;
  const documentValue = new DOMParser().parseFromString(raw.replace(/<br\s*\/?\s*>/gi, "\n").replace(/<\/(p|div|blockquote|li)>/gi, "\n"), "text/html");
  return (documentValue.body.textContent || "").replace(/\n{3,}/g, "\n\n").trim();
}

function splitBriefingContent(value, outlook) {
  let lines = readableBriefingText(value).replace(/\r\n/g, "\n").split("\n");
  if (/^(?:KAU\s*)?배출권시장 일일 브리핑$/i.test((lines[0] || "").trim())) lines = lines.slice(1);
  lines = lines.filter((line) => line.trim() !== "상세 브리핑");
  while (lines.length && !lines[0].trim()) lines.shift();
  while (lines.length && !lines.at(-1).trim()) lines.pop();
  const detailHeadings = ["기간별 가격", "수급·가격 심층 분석", "정책 분석", "전력·연료 연계", "상방 요인 심층 분석", "하방 요인 심층 분석", "체크 이슈 포인트"];
  const detailIndex = lines.findIndex((line) => detailHeadings.some((heading) => line.trim().startsWith(heading)));
  const overviewLines = detailIndex >= 0 ? lines.slice(0, detailIndex) : lines;
  const detailLines = detailIndex >= 0 ? lines.slice(detailIndex) : [];
  let overview = overviewLines.join("\n").trim();
  if (outlook && !/향후\s*1주\s*판단/.test(overview)) overview = `${overview}${overview ? "\n\n" : ""}향후 1주 판단\n${readableBriefingText(outlook)}`;
  return { overview, details: detailLines.join("\n").trim() };
}

function renderBriefings() {
  const area = byId("briefingArea");
  const selected = state.briefings.find((item) => item.date === state.briefingDate) || state.briefings[0];
  if (!selected) return;

  const picker = byId("briefingDatePicker"), select = byId("briefingDateSelect");
  picker.hidden = false;
  select.replaceChildren();
  state.briefings.forEach((item) => {
    const option = create("option", "", item.date);
    option.value = item.date;
    option.selected = item.date === selected.date;
    select.append(option);
  });
  select.onchange = () => { state.briefingDate = select.value; renderBriefings(); };

  const parts = splitBriefingContent(selected.content, selected.outlook);
  const layout = create("div", "briefing-layout");
  const feature = create("article", "briefing-feature");
  const meta = create("div", "briefing-meta");
  meta.append(create("span", `tone tone-${selected.marketTone || "중립"}`, selected.marketTone || "중립"), create("time", "", selected.date), create("span", "", selected.source || "Telegram"));
  feature.append(meta, create("h3", "", selected.title || "배출권 데일리 브리핑"), create("div", "briefing-content briefing-overview", parts.overview));
  if (parts.details) {
    const details = create("details", "briefing-details");
    const summary = create("summary");
    summary.append(create("span", "", "상세 브리핑"), create("i", "", "＋"));
    details.append(summary, create("div", "briefing-content", parts.details));
    feature.append(details);
  }
  layout.append(feature);
  area.replaceChildren(layout);
}

async function init() {
  const [priceResult, policyResult, briefingResult] = await Promise.allSettled([loadText("data/prices.csv"), loadJson("data/policies.json"), loadJson("data/briefing.json")]);
  state.prices = priceResult.status === "fulfilled" ? parsePrices(priceResult.value) : fallbackPrice;
  if (!state.prices.length) state.prices = fallbackPrice;
  const latestDate = state.prices.at(-1).date;
  const latestRows = state.prices.filter((row) => row.date === latestDate);
  state.symbol = latestRows.reduce((best, row) => !best || row.volume > best.volume ? row : best, null)?.symbol || state.prices.at(-1).symbol;
  const policyData = policyResult.status === "fulfilled" ? policyResult.value : { items: [], keywords: [] };
  state.policies = dedupeNewsPolicies((policyData.items || []).map((item) => ({ ...item, publishedAt: item.publishedAt || item.date || "" })));
  state.policyInsight = policyData.aiInsight || null;
  const briefingData = briefingResult.status === "fulfilled" ? briefingResult.value : { items: [] };
  state.briefings = (briefingData.items || []).map((item) => ({ ...item, date: item.date || item.briefingDate || "" })).sort((a, b) => b.date.localeCompare(a.date));
  state.briefingDate = state.briefings[0]?.date || "";
  byId("policySync").textContent = policyData.lastSync ? new Date(policyData.lastSync).toLocaleString("ko-KR", { timeZone: "Asia/Seoul", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "수동 실행 필요";
  document.querySelectorAll("[data-period]").forEach((button) => button.addEventListener("click", () => { state.period = button.dataset.period; document.querySelectorAll("[data-period]").forEach((item) => item.classList.toggle("active", item === button)); renderMarket(); }));
  renderSymbolPicker(); renderPolicyFilters(); renderPolicies(); renderMarket(); renderBriefings();
}

init().catch(() => { state.prices = fallbackPrice; renderMarket(); });
