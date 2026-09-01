"use strict";
// POLICY_PAGINATION_VERSION = "2026-08-20-v1.1-volume"
// BRIEFING_ARCHIVE_VERSION = "2026-08-20-v2"
// AUCTION_MONITOR_VERSION = "2026-08-20-v3.4-full-history-default"
// MARKET_PULSE_VERSION = "2026-08-20-v2-20d-position-supply-strength"
// KAU_ROLLOVER_VERSION = "2026-08-28-v2-dual-series-volume-compare"
// NEWS_DEDUPE_VERSION = "2026-08-28-v2-story-clustering"
// PRICE_SCALE_VERSION = "2026-08-28-v1-symbol-period-autoscale"
// POLICY_INSIGHT_META_VERSION = "2026-08-28-v1-hidden"
// INSTITUTION_SCHEDULE_UI_VERSION = "2026-08-31-v2-event-relevance-and-content-dedupe"
// NEWS_REGION_UI_VERSION = "2026-08-31-v1-overseas-topic-badge"
// LEGISLATION_RADAR_VERSION = "2026-08-31-v1-allbillv2"
// INSTITUTION_SCHEDULE_DATE_UI_VERSION = "2026-08-31-v2-published-and-planned-date"
// ASSEMBLY_SEMINAR_RADAR_VERSION = "2026-08-31-v1-official-schedule"
// BILL_STAGE_TRACKER_VERSION = "2026-08-31-v3.1-chronological-committee-alternatives"
// ASSEMBLY_SEMINAR_DATE_VERSION = "2026-08-31-v2-from-2026-verified-event-date"
// KRX_NOTICE_VERSION = "2026-08-31-v1-official-board"

const state = { prices: [], auctions: [], auctionPeriod: "ALL", auctionPage: 1, auctionLastSync: "", policies: [], policyLastSync: "", institutionSchedules: [], bills: [], billLastSync: "", billWarning: "", billView: "ALL", assemblySeminars: [], seminarLastSync: "", seminarWarning: "", policyInsight: null, briefings: [], briefingDate: "", period: "3M", category: "기후부 보도자료", policyPage: 1, symbol: "" };
const POLICIES_PER_PAGE = 5;
const AUCTIONS_PER_PAGE = 3;
const CONTINUOUS_SYMBOL = "KAU25_KAU26";
const CONTINUOUS_LABEL = "KAU25 → KAU26";
const KAU26_SWITCH_NOT_BEFORE = "2026-08-31";
const BILL_STAGES = [
  { key: "proposed", label: "발의" },
  { key: "committee", label: "소관위" },
  { key: "law", label: "법사위" },
  { key: "plenary", label: "본회의" },
  { key: "government", label: "정부이송" },
  { key: "promulgated", label: "공포" }
];
const COMMITTEE_ALTERNATIVE_STAGES = [
  { key: "alternativeDecision", sourceKey: "committee", label: "대안의결" },
  { key: "alternativeProposal", sourceKey: "proposed", label: "대안제안" },
  { key: "law", sourceKey: "law", label: "법사위" },
  { key: "plenary", sourceKey: "plenary", label: "본회의" },
  { key: "government", sourceKey: "government", label: "정부이송" },
  { key: "promulgated", sourceKey: "promulgated", label: "공포" }
];
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

function continuousPrices() {
  return state.prices
    .filter((row) => (row.symbol === "KAU25" && row.date < KAU26_SWITCH_NOT_BEFORE) || (row.symbol === "KAU26" && row.date >= KAU26_SWITCH_NOT_BEFORE))
    .sort((a, b) => a.date.localeCompare(b.date));
}

function selectedPrices() {
  if (state.symbol === CONTINUOUS_SYMBOL) return continuousPrices();
  return state.prices.filter((row) => !state.symbol || row.symbol === state.symbol).sort((a, b) => a.date.localeCompare(b.date));
}

function filterRowsByPeriod(selected, period) {
  if (period === "ALL" || selected.length < 2) return selected;
  const days = { "1M": 31, "3M": 93, "6M": 186, "1Y": 366 }[period];
  const end = new Date(`${selected.at(-1).date}T00:00:00`).getTime();
  return selected.filter((row) => end - new Date(`${row.date}T00:00:00`).getTime() <= days * 86400000);
}

function filterPrices(period) { return filterRowsByPeriod(selectedPrices(), period); }

function rolloverIndex(rows) {
  return state.symbol === CONTINUOUS_SYMBOL ? rows.findIndex((row, index) => index > 0 && row.symbol !== rows[index - 1].symbol) : -1;
}

function adjustedContinuousPrices(rows) {
  const transitionIndex = rolloverIndex(rows);
  if (transitionIndex <= 0) return rows;
  const gap = rows[transitionIndex].close - rows[transitionIndex - 1].close;
  return rows.map((row, index) => index < transitionIndex ? { ...row, close: row.close + gap } : row);
}

function normalizeAuctions(items) {
  const numericFields = ["offeredQuantity", "bidQuantity", "bidRatio", "bidderCount", "winnerCount", "highestBid", "lowestBid", "awardedQuantity", "clearingPrice", "spotClose", "premiumPct"];
  return (Array.isArray(items) ? items : []).map((item) => {
    const row = { ...item, date: String(item.date || ""), symbol: String(item.symbol || "KAU25").toUpperCase() };
    numericFields.forEach((field) => { row[field] = Number(String(item[field] ?? 0).replace(/,/g, "")) || 0; });
    if (row.spotClose > 0) row.premiumPct = (row.clearingPrice / row.spotClose - 1) * 100;
    return row;
  }).filter((row) => /^\d{4}-\d{2}-\d{2}$/.test(row.date) && row.symbol.startsWith("KAU") && row.clearingPrice > 0)
    .sort((a, b) => b.date.localeCompare(a.date) || a.symbol.localeCompare(b.symbol));
}

function filterAuctions(period) {
  const all = [...state.auctions].sort((a, b) => a.date.localeCompare(b.date));
  if (period === "ALL" || all.length < 2) return all;
  const days = { "3M": 93, "6M": 186, "1Y": 366 }[period];
  const end = chartDateTime(all.at(-1).date);
  return all.filter((row) => end - chartDateTime(row.date) <= days * 86400000);
}

function money(value) { return number.format(Math.round(value || 0)); }
function amount(value) { return value >= 1e8 ? `${(value / 1e8).toFixed(1)}억원` : `${money(value)}원`; }
function shortDate(value) { return value ? value.slice(2).replaceAll("-", ".") : "-"; }
function periodName(value) { return ({ "1M": "1개월", "3M": "3개월", "6M": "6개월", "1Y": "1년", "ALL": "전체" })[value]; }
function auctionPeriodName(value) { return ({ "3M": "최근 3개월", "6M": "최근 6개월", "1Y": "최근 1년", "ALL": "전기간" })[value]; }
function compactTons(value) { return value >= 10000 ? `${(value / 10000).toFixed(value % 10000 ? 1 : 0)}만 톤` : `${money(value)}톤`; }
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

function nicePriceStep(value) {
  if (!Number.isFinite(value) || value <= 0) return 100;
  const power = 10 ** Math.floor(Math.log10(value));
  const fraction = value / power;
  const niceFraction = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 2.5 ? 2.5 : fraction <= 5 ? 5 : 10;
  return niceFraction * power;
}

function chartPriceScale(values) {
  const prices = values.filter((value) => Number.isFinite(value) && value > 0);
  if (!prices.length) return { yMin: 0, yMax: 1000, ticks: [1000, 750, 500, 250, 0] };
  const dataMin = Math.min(...prices), dataMax = Math.max(...prices);
  const center = (dataMin + dataMax) / 2;
  const dataSpan = dataMax - dataMin;
  // 종목·조회기간별 변동폭을 기준으로 확대하되, 미세 변동의 과도한 확대는 제한합니다.
  const displaySpan = Math.max(dataSpan * 1.32, center * .08, 1000);
  const lowerTarget = Math.max(0, center - displaySpan / 2);
  const upperTarget = center + displaySpan / 2;
  const step = nicePriceStep(displaySpan / 4);
  const yMin = Math.max(0, Math.floor(lowerTarget / step) * step);
  const yMax = Math.max(yMin + step, Math.ceil(upperTarget / step) * step);
  const ticks = [];
  for (let value = yMax; value >= yMin - step * .001; value -= step) ticks.push(Math.max(0, Math.round(value)));
  return { yMin, yMax, ticks };
}

function policyGroup(policy) {
  const source = String(policy.source || "");
  const url = String(policy.url || "");
  if (policy.section === "news" || policy.sourceType === "news") return "뉴스";
  if (policy.section === "krx_notice" || /한국거래소|\bKRX\b/i.test(source) || /ets\.krx\.co\.kr\/board\/ETS01030000/i.test(url)) return "한국거래소 공지사항";
  if (policy.section === "press") return "기후부 보도자료";
  if (policy.section === "notice") return "기후부 공지사항";
  if (source.includes("보도자료") || /(?:menuId=(?:286|10598)|boardMasterId=(?:1|939))(?:&|$)/.test(url)) return "기후부 보도자료";
  if (source.includes("공지") || source.includes("공고") || policy.sourceType === "official") return "기후부 공지사항";
  return "뉴스";
}

const ASSEMBLY_AGENDA_TITLE = /^\s*[\[【〖〈<「『(]*\s*(?:오늘(?:의)?\s*)?국회\s*(?:주요\s*)?(?:의사\s*)?일정(?=\s|$|[\]】〗〉>」』):：(\[])/i;
const ASSEMBLY_MARKET_CONTEXT = /배출권|탄소\s*(?:시장|가격|배출권|국경)|온실가스|탄소중립|기후(?:위기|대응|정책|외교)?|CBAM|에너지|전력망/i;
const ASSEMBLY_EVENT_CUE = /설명회|공청회|간담회|세미나|포럼|토론회|심포지엄|컨퍼런스|협의회|회의/i;

function isAssemblyAgendaPolicy(policy) {
  return policyGroup(policy) === "뉴스" && ASSEMBLY_AGENDA_TITLE.test(String(policy.title || "").replace(/<[^>]+>/g, " ").trim());
}

function assemblyAgendaEventTitle(value, fallback = "") {
  const text = String(value || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
  const candidates = text.split(/[\r\n•●○◇◆|;/]+|\s+-\s+/).map((part) => part.trim().replace(/^[-–—·,:：\s]+|[-–—·,:：\s]+$/g, ""))
    .filter((part) => ASSEMBLY_MARKET_CONTEXT.test(part) && ASSEMBLY_EVENT_CUE.test(part));
  let title = [...candidates].sort((left, right) => left.length - right.length || left.localeCompare(right))[0] || "";
  if (title.includes(",")) {
    const focused = title.split(/[,，]/).map((part) => part.trim()).filter((part) => ASSEMBLY_MARKET_CONTEXT.test(part) && ASSEMBLY_EVENT_CUE.test(part));
    if (focused.length) title = focused.sort((left, right) => left.length - right.length)[0];
  }
  title = title
    .replace(/^\s*\d{1,2}(?::\d{2})?\s*/, "")
    .replace(/^[가-힣A-Za-z0-9· ]{2,100}(?:의원실|위원회|국회의원)\s*(?:등|주최|공동주최)?\s*/, "")
    .trim();
  return title || fallback;
}

function assemblyAgendaSchedules(policies) {
  const groups = new Map();
  policies.filter(isAssemblyAgendaPolicy).forEach((policy) => {
    const eventTitle = assemblyAgendaEventTitle(policy.summary);
    if (!eventTitle) return;
    const group = groups.get(policy.publishedAt) || [];
    group.push({ ...policy, eventTitle });
    groups.set(policy.publishedAt, group);
  });
  return [...groups.entries()].map(([date, group]) => {
    const score = (policy) => {
      let direct = 0;
      try { direct = /(?:news\.google\.com|news\.naver\.com|n\.news\.naver\.com)$/i.test(new URL(policy.url).hostname) ? 0 : 100000; } catch { direct = 0; }
      return direct + (/연합뉴스/.test(String(policy.source || "")) ? 10000 : 0) + String(policy.summary || "").length;
    };
    const representative = [...group].sort((left, right) => score(right) - score(left) || String(left.url || "").localeCompare(String(right.url || "")))[0];
    const duplicateCount = group.reduce((sum, item) => sum + Math.max(1, Number(item.duplicateCount) || 1), 0);
    return {
      id: `assembly-agenda-${date}`,
      title: representative.eventTitle,
      sourceTitle: representative.title,
      eventType: "국회일정",
      startDate: date,
      endDate: date,
      dateInference: "published",
      startTime: "",
      timezone: "Asia/Seoul",
      organizer: "대한민국 국회",
      location: "",
      status: "confirmed",
      evidence: representative.summary || representative.title,
      publishedAt: date,
      source: representative.source,
      sourceType: "news",
      url: representative.url,
      sourceUrls: group.map((item) => item.url).filter(Boolean),
      sources: [...new Set(group.map((item) => item.source).filter(Boolean))],
      duplicateCount,
    };
  });
}

function mergeAssemblyAgendaSchedules(existing, derived) {
  const ordinary = (Array.isArray(existing) ? existing : []).filter((item) => item?.eventType !== "국회일정");
  const agendas = new Map();
  [...(Array.isArray(existing) ? existing : []).filter((item) => item?.eventType === "국회일정"), ...derived].forEach((item) => {
    const key = `${item.organizer || "대한민국 국회"}|${item.startDate}`;
    const previous = agendas.get(key);
    if (!previous) { agendas.set(key, item); return; }
    const representative = String(item.evidence || "").length > String(previous.evidence || "").length ? item : previous;
    agendas.set(key, { ...representative, duplicateCount: Math.max(Number(previous.duplicateCount || 1), Number(item.duplicateCount || 1)) });
  });
  return [...ordinary, ...agendas.values()];
}

function derivePolicyInsight(policies) {
  const official = policies.filter((policy) => policyGroup(policy) !== "뉴스").slice(0, 10);
  if (!official.length) return { summary: "최근 기후부·한국거래소 공식자료가 수집되면 정책 변화가 시장 수급에 미칠 영향을 분석합니다.", basisLatestDate: "-", basisCount: 0, source: "fallback" };
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

const NEWS_GENERIC_TOKENS = new Set(["배출권", "탄소배출권", "온실가스", "탄소", "탄소시장", "시장", "배출권거래제", "거래제", "뉴스", "특집", "단독", "속보", "관련", "대응", "추진", "강화", "사업", "한국", "국내", "kau25", "kau26"]);
const NEWS_PARTICLES = ["으로", "에서", "에게", "까지", "부터", "처럼", "보다", "만큼", "은", "는", "이", "가", "을", "를", "의", "와", "과", "도", "만"];

function newsEventTokens(value) {
  const cleaned = String(value || "").toLowerCase().replace(/\[[^\]]+\]|\([^)]*\)/g, " ");
  return new Set((cleaned.match(/[a-z]+|[가-힣]{2,}/g) || []).map((token) => {
    const particle = NEWS_PARTICLES.find((item) => token.length >= 4 && token.endsWith(item));
    return particle ? token.slice(0, -particle.length) : token;
  }).filter((token) => token.length >= 2 && !NEWS_GENERIC_TOKENS.has(token)));
}

function newsDateDistance(first, second) {
  const left = chartDateTime(first), right = chartDateTime(second);
  return Number.isFinite(left) && Number.isFinite(right) ? Math.abs(left - right) / 86400000 : 999;
}

function sameNewsStory(first, second) {
  const firstTitle = normalizedNewsTitle(first.title), secondTitle = normalizedNewsTitle(second.title);
  if (!firstTitle || !secondTitle) return false;
  if (firstTitle === secondTitle) return true;
  const distance = newsDateDistance(first.publishedAt, second.publishedAt);
  const dice = newsTitleDice(first.title, second.title);
  if (distance === 0) {
    if (similarNews(first.title, second.title)) return true;
    const rightTokens = newsEventTokens(second.title);
    const shared = [...newsEventTokens(first.title)].filter((token) => rightTokens.has(token));
    return shared.length >= 2 && shared.reduce((sum, token) => sum + token.length, 0) >= 5;
  }
  return distance <= 2 && dice >= .78;
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
        if (group.some((member) => sameNewsStory(candidate, member))) {
          group.push(candidate); remaining.splice(index, 1); changed = true;
        }
      }
    }
    const representative = group.reduce((best, item) => (`${item.summary || ""}${item.title || ""}`).length > (`${best.summary || ""}${best.title || ""}`).length ? item : best);
    const duplicateCount = group.reduce((sum, item) => sum + Math.max(1, Number(item.duplicateCount) || 1), 0);
    const duplicateSources = [...new Set(group.flatMap((item) => [...(Array.isArray(item.duplicateSources) ? item.duplicateSources : []), item.source]).filter(Boolean))];
    representatives.push({ ...representative, duplicateCount, duplicateSources });
  }
  return [...official, ...representatives].sort((a, b) => b.publishedAt.localeCompare(a.publishedAt));
}

function newsDisplaySummary(policy) {
  const summary = String(policy.summary || "").trim();
  if (policyGroup(policy) !== "뉴스" || !summary) return summary || "원문에서 세부 내용을 확인하세요.";
  const titleKey = normalizedNewsTitle(policy.title), summaryKey = normalizedNewsTitle(summary), sourceKey = normalizedNewsTitle(policy.source);
  const repeatsTitle = summaryKey === titleKey || summaryKey === `${titleKey}${sourceKey}` || (summaryKey.startsWith(titleKey) && summaryKey.length <= titleKey.length + sourceKey.length + 12);
  if (!repeatsTitle) return summary;
  const count = Math.max(1, Number(policy.duplicateCount) || 1);
  return count > 1 ? `유사 기사 ${count}건을 대표기사 1건으로 통합했습니다.` : `${policy.source || "뉴스"} 기사`;
}

function policyBadge(policy) {
  const topic = String(policy.category || "기타");
  return policyGroup(policy) === "뉴스" && policy.region === "해외" ? `해외 · ${topic}` : topic;
}

function institutionEventTitle(item) {
  const evidence = String(item.evidence || "");
  const quoted = [...evidence.matchAll(/[‘“](.{4,180}?)[’”]/g)].map((match) => match[1]);
  const plainQuoted = quoted.length ? quoted : [...evidence.matchAll(/['"]([^'"]{4,180})['"]/g)].map((match) => match[1]);
  return String(plainQuoted.at(-1) || item.title || "기관 일정").trim();
}

function normalizeInstitutionSchedules(items) {
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  const marketContext = /배출권|배출허용총량|유상\s*할당|무상\s*할당|유상\s*경매|탄소\s*(?:시장|가격|배출권|국경)|상쇄|외부사업|시장\s*안정|할당\s*계획|K\s*[-_]?\s*ETS|K(?:AU|CU|OC)\s*\d{0,2}|온실가스|탄소중립|기후(?:위기|대응|정책|외교)?|국제\s*감축|탄소국경(?:조정)?|CBAM|에너지|전력망/i;
  const nonEventContext = /청원|동의\s*(?:진행|필요|마감)|5\s*만\s*명|국민동의|서명\s*운동/i;
  const normalized = (Array.isArray(items) ? items : []).map((item) => ({
    ...item,
    id: String(item.id || ""),
    sourceTitle: String(item.sourceTitle || item.title || ""),
    title: institutionEventTitle(item),
    eventType: String(item.eventType || "기관일정"),
    publishedAt: String(item.publishedAt || ""),
    startDate: String(item.startDate || ""),
    endDate: String(item.endDate || item.startDate || ""),
    startTime: String(item.startTime || ""),
    organizer: String(item.organizer || ""),
    location: String(item.location || ""),
    evidence: String(item.evidence || ""),
    duplicateCount: Math.max(1, Number(item.duplicateCount) || 1)
  })).filter((item) => {
    const context = item.evidence || item.title;
    return Boolean(verifiedIsoDate(item.startDate) && marketContext.test(context) && !nonEventContext.test(context));
  });
  const representatives = [];
  const evidenceKeys = new Map();
  normalized.forEach((item) => {
    const normalizedEvidence = normalizedNewsTitle(item.evidence).slice(0, 1200);
    const key = normalizedEvidence.length >= 40 ? `evidence:${normalizedEvidence}` : item.id ? `id:${item.id}` : `event:${item.organizer}|${item.eventType}|${item.startDate}|${item.startTime}|${normalizedNewsTitle(item.title)}`;
    const existingIndex = evidenceKeys.get(key);
    if (existingIndex === undefined) {
      evidenceKeys.set(key, representatives.length);
      representatives.push(item);
      return;
    }
    const existing = representatives[existingIndex];
    existing.duplicateCount = Math.max(existing.duplicateCount, item.duplicateCount, 2);
  });
  return representatives.sort((left, right) => {
    const leftPast = (left.endDate || left.startDate) < today ? 1 : 0;
    const rightPast = (right.endDate || right.startDate) < today ? 1 : 0;
    if (leftPast !== rightPast) return leftPast - rightPast;
    return leftPast ? right.startDate.localeCompare(left.startDate) : left.startDate.localeCompare(right.startDate) || left.startTime.localeCompare(right.startTime);
  });
}

function monthDay(value) {
  const match = String(value || "").match(/^\d{4}-(\d{2})-(\d{2})$/);
  return match ? `${Number(match[1])}.${Number(match[2])}` : "-";
}

function schedulePlanLabel(schedule) {
  const start = monthDay(schedule.startDate);
  const end = schedule.endDate && schedule.endDate !== schedule.startDate ? `~${monthDay(schedule.endDate)}` : "";
  return `(${start}${end} 예정)`;
}

function stripScheduleTime(value) {
  return String(value || "")
    .replace(/(?<!\d)(?:(?:오전|오후|밤)\s*)?\d{1,2}\s*시(?:\s*\d{1,2}\s*분)?(?:\s*(?:부터|까지|경))?/g, "")
    .replace(/(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?:\s*(?:부터|까지))?(?!\d)/g, "")
    .replace(/\s{2,}/g, " ")
    .replace(/\s+([,.;·])/g, "$1")
    .trim();
}

function scheduleDisplaySummary(schedule) {
  const details = [schedule.organizer];
  if (schedule.location) details.push(schedule.location);
  if (schedule.status === "postponed") details.push("변경 일정");
  else if (schedule.status === "conditional") details.push("조건부 일정");
  if (schedule.duplicateCount > 1) details.push(`중복 보도 ${schedule.duplicateCount}건`);
  const evidence = stripScheduleTime(schedule.evidence);
  const repeatsTitle = evidence && normalizedNewsTitle(evidence) === normalizedNewsTitle(schedule.title);
  return `${details.filter(Boolean).join(" · ")}${evidence && !repeatsTitle ? ` — ${evidence}` : ""}` || "원문에서 일정 세부 내용을 확인하세요.";
}

function normalizeBills(items) {
  const seen = new Set();
  return (Array.isArray(items) ? items : []).map((item) => {
    const sourceLifecycle = item.lifecycle && typeof item.lifecycle === "object" ? item.lifecycle : {};
    const lifecycleDate = (key, fallback = "") => String(sourceLifecycle[key] || fallback || "").slice(0, 10);
    return {
      ...item,
      billId: String(item.billId || ""),
      billNo: String(item.billNo || ""),
      title: String(item.title || "국회 의안"),
      proposedDate: String(item.proposedDate || "").slice(0, 10),
      lastActionDate: String(item.lastActionDate || item.proposedDate || "").slice(0, 10),
      proposerKind: String(item.proposerKind || ""),
      proposer: String(item.proposer || "제안자 확인 중"),
      committee: String(item.committee || "소관위 미정"),
      status: String(item.status || "접수"),
      assemblyTerm: String(item.assemblyTerm || ""),
      terminal: item.terminal === true,
      terminationReason: String(item.terminationReason || ""),
      terminationDate: String(item.terminationDate || "").slice(0, 10),
      terminationStage: String(item.terminationStage || ""),
      timelineType: String(item.timelineType || ""),
      alternativeAdoptedDate: String(item.alternativeAdoptedDate || "").slice(0, 10),
      chronologyAdjusted: item.chronologyAdjusted === true,
      rawStage: String(item.rawStage || ""),
      rawResult: String(item.rawResult || ""),
      committeeResult: String(item.committeeResult || ""),
      lawResult: String(item.lawResult || ""),
      plenaryResult: String(item.plenaryResult || ""),
      summary: String(item.summary || ""),
      primaryCategory: String(item.primaryCategory || "제도·거버넌스"),
      relevanceLevel: String(item.relevanceLevel || "직접"),
      relevanceScore: Number(item.relevanceScore || 0),
      lifecycle: {
        proposed: lifecycleDate("proposed", item.proposedDate),
        committeeReceived: lifecycleDate("committeeReceived"),
        committeePresented: lifecycleDate("committeePresented"),
        committeeCommented: lifecycleDate("committeeCommented"),
        committeeProcessed: lifecycleDate("committeeProcessed"),
        lawPresented: lifecycleDate("lawPresented"),
        lawProcessed: lifecycleDate("lawProcessed"),
        plenaryPresented: lifecycleDate("plenaryPresented"),
        plenaryResolved: lifecycleDate("plenaryResolved"),
        governmentTransferred: lifecycleDate("governmentTransferred"),
        promulgated: lifecycleDate("promulgated")
      },
      url: String(item.url || "")
    };
  }).filter((item) => {
    const key = item.billId || item.billNo;
    if (!key || !item.title || seen.has(key)) return false;
    seen.add(key);
    return true;
  }).sort((left, right) => billLatestEventDate(right).localeCompare(billLatestEventDate(left)) || right.relevanceScore - left.relevanceScore || right.billNo.localeCompare(left.billNo));
}

function billStageIndex(bill) {
  const lifecycle = bill.lifecycle || {};
  const rawStage = `${bill.rawStage || ""} ${bill.rawResult || ""}`;
  if (billIsTerminal(bill)) {
    const explicitStage = { proposed: 0, committee: 1, law: 2, plenary: 3, government: 4, promulgated: 5 }[String(bill.terminationStage || "")];
    if (Number.isInteger(explicitStage)) return explicitStage;
    const terminalPattern = /수정안\s*반영|대안\s*반영|철회|부결|임기\s*만료|심사\s*미료|폐기/;
    if (billIsReflectedClosure(bill) && terminalPattern.test(bill.committeeResult || "")) return 1;
    if (terminalPattern.test(bill.plenaryResult || "")) return 3;
    if (terminalPattern.test(bill.lawResult || "")) return 2;
    if (terminalPattern.test(bill.committeeResult || "")) return 1;
  }
  if (lifecycle.promulgated || bill.status === "공포" || /공포/.test(rawStage)) return 5;
  if (lifecycle.governmentTransferred || bill.status === "정부이송" || /정부\s*이송/.test(rawStage)) return 4;
  if (lifecycle.plenaryPresented || lifecycle.plenaryResolved || bill.plenaryResult || /본회의/.test(`${bill.status} ${rawStage}`) || bill.status === "부결") return 3;
  if (lifecycle.lawPresented || lifecycle.lawProcessed || bill.lawResult || /법사위|법제사법/.test(`${bill.status} ${rawStage}`)) return 2;
  if (lifecycle.committeeReceived || lifecycle.committeePresented || lifecycle.committeeCommented || lifecycle.committeeProcessed || bill.committeeResult || /소관위|위원회/.test(`${bill.status} ${rawStage}`) || bill.status === "대안반영") return 1;
  return 0;
}

function billStageDate(bill, stageKey) {
  const lifecycle = bill.lifecycle || {};
  const candidates = {
    proposed: [lifecycle.proposed, bill.proposedDate],
    committee: [lifecycle.committeeReceived, lifecycle.committeePresented, lifecycle.committeeCommented, lifecycle.committeeProcessed],
    law: [lifecycle.lawPresented, lifecycle.lawProcessed],
    plenary: [lifecycle.plenaryPresented, lifecycle.plenaryResolved],
    government: [lifecycle.governmentTransferred],
    promulgated: [lifecycle.promulgated]
  };
  return (candidates[stageKey] || [])
    .filter((value) => /^\d{4}-\d{2}-\d{2}$/.test(String(value || "")))
    .sort()
    .at(-1) || "";
}

function billAlternativeDecisionDate(bill) {
  const proposedDate = billStageDate(bill, "proposed");
  const isEarlierCommitteeDate = (value) => /^\d{4}-\d{2}-\d{2}$/.test(String(value || "")) && (!proposedDate || value < proposedDate);
  if (isEarlierCommitteeDate(bill.alternativeAdoptedDate)) return bill.alternativeAdoptedDate;
  const lifecycle = bill.lifecycle || {};
  if (isEarlierCommitteeDate(lifecycle.committeeProcessed)) return lifecycle.committeeProcessed;
  return [lifecycle.committeeReceived, lifecycle.committeePresented, lifecycle.committeeCommented]
    .filter(isEarlierCommitteeDate)
    .sort()
    .at(-1) || "";
}

function billUsesCommitteeAlternativeTimeline(bill) {
  const titleMarksAlternative = /[（(]\s*대안\s*[)）]\s*$/.test(String(bill.title || ""));
  const committeeApprovedAlternative = /대안\s*가결/.test(String(bill.committeeResult || ""));
  const committeeSponsor = /위원장/.test(`${bill.proposerKind || ""} ${bill.proposer || ""}`);
  const proposedDate = billStageDate(bill, "proposed");
  const alternativeDecisionDate = billAlternativeDecisionDate(bill);
  const explicitAlternative = bill.timelineType === "committeeAlternative";
  const verifiedAlternative = explicitAlternative || titleMarksAlternative && (committeeApprovedAlternative || committeeSponsor);
  return verifiedAlternative && Boolean(alternativeDecisionDate && (proposedDate ? alternativeDecisionDate < proposedDate : explicitAlternative));
}

function nonDecreasingBillDates(values) {
  const validIndices = values
    .map((value, index) => /^\d{4}-\d{2}-\d{2}$/.test(value) ? index : -1)
    .filter((index) => index >= 0);
  if (!validIndices.length) return values.map(() => "");
  const lengths = validIndices.map(() => 1);
  const previous = validIndices.map(() => -1);
  validIndices.forEach((currentIndex, currentPosition) => {
    for (let earlierPosition = 0; earlierPosition < currentPosition; earlierPosition += 1) {
      const earlierIndex = validIndices[earlierPosition];
      if (values[earlierIndex] <= values[currentIndex] && lengths[earlierPosition] + 1 > lengths[currentPosition]) {
        lengths[currentPosition] = lengths[earlierPosition] + 1;
        previous[currentPosition] = earlierPosition;
      }
    }
  });
  let bestPosition = 0;
  lengths.forEach((length, position) => {
    if (length > lengths[bestPosition]) bestPosition = position;
  });
  const retained = new Set();
  for (let position = bestPosition; position >= 0; position = previous[position]) {
    retained.add(validIndices[position]);
    if (previous[position] < 0) break;
  }
  return values.map((value, index) => retained.has(index) ? value : "");
}

function billTimeline(bill) {
  const committeeAlternative = billUsesCommitteeAlternativeTimeline(bill);
  const stages = committeeAlternative
    ? COMMITTEE_ALTERNATIVE_STAGES
    : BILL_STAGES.map((stage) => ({ key: stage.key, sourceKey: stage.key, label: stage.label }));
  const rawDates = stages.map((stage) => {
    const sourceDate = stage.key === "alternativeDecision"
      ? billAlternativeDecisionDate(bill)
      : billStageDate(bill, stage.sourceKey);
    return sourceDate;
  });
  const dates = nonDecreasingBillDates(rawDates);
  const standardIndex = billStageIndex(bill);
  const currentIndex = committeeAlternative && standardIndex < 2
    ? billStageDate(bill, "proposed") ? 1 : 0
    : standardIndex;
  return { committeeAlternative, stages, dates, currentIndex };
}

function billTerminationReason(bill) {
  const explicit = String(bill.terminationReason || "").trim();
  const disposition = `${explicit} ${bill.status || ""} ${bill.rawStage || ""} ${bill.rawResult || ""} ${bill.committeeResult || ""} ${bill.lawResult || ""} ${bill.plenaryResult || ""}`;
  if (/수정안\s*반영/.test(disposition)) return "수정안반영";
  if (/대안\s*반영/.test(disposition)) return "대안반영";
  if (/철회/.test(disposition)) return "철회";
  if (/부결/.test(disposition)) return "부결";
  if (/임기\s*만료/.test(disposition)) return "임기만료폐기";
  if (/심사\s*미료/.test(disposition)) return "심사미료폐기";
  if (/폐기/.test(disposition)) return "폐기";
  return "";
}

function billIsTerminal(bill) {
  return bill.terminal === true || Boolean(billTerminationReason(bill));
}

function billIsReflectedClosure(bill) {
  return /^(?:대안반영|수정안반영)$/.test(billTerminationReason(bill));
}

function billTerminationLabel(reason) {
  const labels = {
    "대안반영": "대안반영 · 원안 종료",
    "수정안반영": "수정안반영 · 원안 종료",
    "임기만료폐기": "임기만료 · 종료",
    "심사미료폐기": "심사미료 · 종료",
    "철회": "철회 · 종료",
    "부결": "부결 · 종료",
    "폐기": "폐기 · 종료"
  };
  return labels[reason] || `${reason} · 종료`;
}

function billTerminationReasonLabel(reason) {
  return ({ "임기만료폐기": "임기만료", "심사미료폐기": "심사미료" })[reason] || reason;
}

function billStatusGroup(bill) {
  if (billIsTerminal(bill)) return "CLOSED";
  if (bill.status === "공포") return "COMPLETED";
  return "ACTIVE";
}

function billLatestEventDate(bill) {
  if (billIsTerminal(bill) && /^\d{4}-\d{2}-\d{2}$/.test(bill.terminationDate || "")) return bill.terminationDate;
  if (billIsTerminal(bill)) {
    const terminalStage = BILL_STAGES[billStageIndex(bill)];
    const terminalStageDate = terminalStage ? billStageDate(bill, terminalStage.key) : "";
    if (terminalStageDate) return terminalStageDate;
  }
  const lifecycleDates = Object.values(bill.lifecycle || {}).filter((value) => /^\d{4}-\d{2}-\d{2}$/.test(value));
  const candidates = [bill.proposedDate, bill.lastActionDate, ...lifecycleDates].filter((value) => /^\d{4}-\d{2}-\d{2}$/.test(value));
  return candidates.sort().at(-1) || bill.lastActionDate || bill.proposedDate || "";
}

function billIsRecent(bill) {
  const latest = billLatestEventDate(bill);
  if (!latest) return false;
  const action = new Date(`${latest}T00:00:00+09:00`).getTime();
  const now = Date.now();
  const distance = (now - action) / 86400000;
  return distance >= 0 && distance <= 30;
}

function filterBills(items, view) {
  if (view === "ACTIVE") return items.filter((bill) => billStatusGroup(bill) === "ACTIVE");
  if (view === "RECENT") return items.filter(billIsRecent);
  if (view === "COMPLETED") return items.filter((bill) => billStatusGroup(bill) === "COMPLETED");
  if (view === "CLOSED") return items.filter((bill) => billStatusGroup(bill) === "CLOSED");
  return items;
}

function fullBillDate(value) {
  return /^\d{4}-\d{2}-\d{2}$/.test(String(value || "")) ? String(value).replaceAll("-", ".") : "날짜 확인 중";
}

function billStatusLabel(bill) {
  const terminalReason = billTerminationReason(bill);
  if (terminalReason) return billTerminationLabel(terminalReason);
  if (bill.terminal === true) return "법안 종료";
  const labels = {
    "접수": "발의·접수",
    "소관위": "소관위 심사",
    "법사위": "법사위 심사",
    "본회의": "본회의 심의",
    "본회의 통과": "본회의 통과",
    "정부이송": "정부 이송",
    "공포": "공포·완료"
  };
  return labels[bill.status] || bill.status || "발의·접수";
}

function billLatestActionLabel(bill) {
  const status = billStatusLabel(bill);
  const terminalReason = billTerminationReason(bill);
  const detail = terminalReason ? billTerminationLabel(terminalReason) : bill.status === "소관위" && bill.committee && bill.committee !== "소관위 미정" ? `${bill.committee} 심사` : status;
  return `${fullBillDate(billLatestEventDate(bill))} · ${detail}`;
}

function billMetadataLabel(bill) {
  return [bill.assemblyTerm || "", bill.proposer ? `대표발의 ${bill.proposer}` : "", bill.billNo ? `의안 ${bill.billNo}` : "", bill.committee].filter(Boolean).join(" · ");
}

function createBillProgress(bill) {
  const timeline = billTimeline(bill);
  const currentIndex = timeline.currentIndex;
  const terminalReason = billTerminationReason(bill);
  const terminal = billIsTerminal(bill);
  const reflectedClosure = billIsReflectedClosure(bill);
  const wrapper = create("div", "bill-progress-wrap");
  const progress = create("div", "bill-progress");
  progress.setAttribute("role", "list");
  progress.setAttribute("aria-label", `${timeline.committeeAlternative ? "위원회 대안 처리단계" : "입법 진행단계"}: ${billStatusLabel(bill)}`);
  timeline.stages.forEach((stage, index) => {
    let stageState = index < currentIndex ? "done" : index === currentIndex ? terminal ? reflectedClosure ? "terminal-reflected" : "terminal" : bill.status === "공포" ? "complete" : "current" : "upcoming";
    const stageDate = index <= currentIndex ? timeline.dates[index] : "";
    const step = create("span", `bill-stage ${stageState}`);
    step.setAttribute("role", "listitem");
    step.setAttribute("aria-label", `${stage.label}: ${stageDate ? fullBillDate(stageDate) : "날짜 없음"}`);
    const marker = create("i", "", "");
    marker.setAttribute("aria-hidden", "true");
    const caption = create("span", "bill-stage-caption");
    caption.append(create("b", "", stage.label));
    if (stageDate) {
      const date = create("time", "bill-stage-date", `(${shortDate(stageDate)})`);
      date.setAttribute("datetime", stageDate);
      date.setAttribute("title", fullBillDate(stageDate));
      caption.append(date);
    }
    step.append(marker, caption);
    progress.append(step);
  });
  wrapper.append(progress);
  if (terminal) {
    const standardCurrentIndex = billStageIndex(bill);
    const terminationDate = /^\d{4}-\d{2}-\d{2}$/.test(bill.terminationDate || "") ? bill.terminationDate : billStageDate(bill, BILL_STAGES[standardCurrentIndex].key) || billLatestEventDate(bill);
    const notice = create("div", `bill-terminal-state${reflectedClosure ? " reflected" : ""}`);
    notice.setAttribute("role", "status");
    notice.append(create("span", "", reflectedClosure ? "원안 종료" : "법안 종료"));
    if (terminalReason) notice.append(create("strong", "", billTerminationReasonLabel(terminalReason)));
    if (terminationDate) {
      const date = create("time", "", `(${shortDate(terminationDate)})`);
      date.setAttribute("datetime", terminationDate);
      notice.append(date);
    }
    wrapper.append(notice);
  }
  return wrapper;
}

function renderBillOverview(list, bills) {
  const overview = create("section", "bill-overview");
  overview.setAttribute("aria-label", "발의법률안 처리 현황");
  const filters = create("div", "bill-summary-filters");
  const counts = {
    ALL: bills.length,
    ACTIVE: bills.filter((bill) => billStatusGroup(bill) === "ACTIVE").length,
    RECENT: bills.filter(billIsRecent).length,
    COMPLETED: bills.filter((bill) => billStatusGroup(bill) === "COMPLETED").length,
    CLOSED: bills.filter((bill) => billStatusGroup(bill) === "CLOSED").length
  };
  [
    ["ALL", "전체"], ["ACTIVE", "진행 중"], ["RECENT", "최근 변경"], ["COMPLETED", "공포·완료"], ["CLOSED", "법안 종료"]
  ].forEach(([value, label]) => {
    const button = create("button", state.billView === value ? "active" : "");
    button.type = "button";
    button.setAttribute("aria-pressed", state.billView === value ? "true" : "false");
    button.append(create("span", "", label), create("strong", "", `${counts[value]}건`));
    button.addEventListener("click", () => { state.billView = value; state.policyPage = 1; renderPolicies(); });
    filters.append(button);
  });
  const stageBills = bills.filter((bill) => billStatusGroup(bill) !== "CLOSED");
  const stageCounts = BILL_STAGES.map((_, index) => stageBills.filter((bill) => billStageIndex(bill) === index).length);
  const distribution = create("div", "bill-stage-distribution");
  distribution.append(create("span", "distribution-label", "현재 단계"));
  BILL_STAGES.forEach((stage, index) => {
    const item = create("span", "distribution-item");
    item.append(create("b", "", stage.label), create("strong", "", `${stageCounts[index]}건`));
    distribution.append(item);
  });
  overview.append(filters, distribution);
  list.append(overview);
}

function verifiedIsoDate(value) {
  const match = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return "";
  const year = Number(match[1]), month = Number(match[2]), day = Number(match[3]);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return parsed.getUTCFullYear() === year && parsed.getUTCMonth() === month - 1 && parsed.getUTCDate() === day ? match[0] : "";
}

function seminarDateRange() {
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  const maximum = new Date(`${today}T00:00:00Z`); maximum.setUTCDate(maximum.getUTCDate() + 365);
  return { today, maximum: maximum.toISOString().slice(0, 10) };
}

function normalizeAssemblySeminars(items) {
  const { today, maximum } = seminarDateRange();
  const seen = new Set();
  return (Array.isArray(items) ? items : []).map((item) => {
    const hostValue = Array.isArray(item.organizers) ? item.organizers.join(", ") : (item.host || item.organizer || item.organizers || "");
    return {
      ...item,
      id: String(item.sourceEventId || item.eventId || item.id || item.sourceId || ""),
      category: "세미나일정",
      title: String(item.title || "국회의원 정책세미나"),
      publishedAt: String(item.publishedAt || item.firstSeenAt || item.firstSeen || item.collectedAt || "").slice(0, 10),
      startDate: String(item.startDate || item.eventDate || item.date || "").slice(0, 10),
      endDate: String(item.endDate || item.startDate || item.eventDate || item.date || "").slice(0, 10),
      eventType: String(item.eventType || item.type || "세미나"),
      host: String(hostValue),
      venue: String(item.venue || item.location || ""),
      status: String(item.status || "예정"),
      relevance: String(item.relevance || item.relevanceLevel || ""),
      summary: String(item.summary || item.description || ""),
      url: String(item.url || item.officialUrl || item.sourceUrl || "https://ampos.nanet.go.kr/seminarList.do")
    };
  }).filter((item) => {
    const eventDate = verifiedIsoDate(item.startDate);
    if (!item.title || !eventDate || eventDate < "2026-01-01" || eventDate > maximum) return false;
    const key = item.id || `${normalizedNewsTitle(item.title)}|${normalizedNewsTitle(item.host)}|${item.startDate}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).sort((left, right) => {
    const leftPast = (left.endDate || left.startDate) < today ? 1 : 0;
    const rightPast = (right.endDate || right.startDate) < today ? 1 : 0;
    if (leftPast !== rightPast) return leftPast - rightPast;
    return leftPast ? right.startDate.localeCompare(left.startDate) : left.startDate.localeCompare(right.startDate);
  });
}

function seminarPlanLabel(seminar) {
  const eventDate = verifiedIsoDate(seminar.startDate);
  if (!eventDate) return "(행사일 확인 필요)";
  const display = eventDate.replaceAll("-", ".");
  const { today } = seminarDateRange();
  if (/취소|cancel/i.test(seminar.status)) return `(${display} 취소)`;
  if (/변경|연기|postpon/i.test(seminar.status)) return `(${display} 연기)`;
  if (eventDate > today) return `(${display} 예정)`;
  if (eventDate === today) return `(${display} 오늘)`;
  return `(${display} 개최)`;
}

function seminarDisplaySummary(seminar) {
  const details = [seminar.host, seminar.venue, seminar.relevance ? `관련성 ${seminar.relevance}` : ""];
  if (/취소|cancel/i.test(seminar.status)) details.push("취소");
  else if (/변경|연기|postpon/i.test(seminar.status)) details.push("일정 변경");
  const summary = stripScheduleTime(seminar.summary);
  const repeatsTitle = summary && normalizedNewsTitle(summary) === normalizedNewsTitle(seminar.title);
  return `${details.filter(Boolean).join(" · ")}${summary && !repeatsTitle ? ` — ${summary}` : ""}` || "국회도서관 공식 일정에서 세부 내용을 확인하세요.";
}

function marketPulseMetrics(selected, latest) {
  const ordered = [...selected].sort((a, b) => a.date.localeCompare(b.date));
  const windowRows = ordered.slice(-20);
  const baseline = ordered.slice(-21, -1);
  const priceAverage = windowRows.reduce((sum, row) => sum + row.close, 0) / Math.max(windowRows.length, 1);
  const priceHigh = Math.max(...windowRows.map((row) => row.close), latest.close);
  const priceLow = Math.min(...windowRows.map((row) => row.close), latest.close);
  const pricePosition = priceHigh === priceLow ? 50 : (latest.close - priceLow) / (priceHigh - priceLow) * 100;
  const priceBand = pricePosition >= 80 ? "상단" : pricePosition <= 20 ? "하단" : "중단";
  const priceAverageDiff = priceAverage ? (latest.close / priceAverage - 1) * 100 : 0;
  const volumeBase = baseline.length ? baseline : windowRows.filter((row) => row.date !== latest.date);
  const averageVolume = volumeBase.reduce((sum, row) => sum + row.volume, 0) / Math.max(volumeBase.length, 1);
  const volumeRatio = averageVolume ? latest.volume / averageVolume : 1;
  const volumeRank = 1 + windowRows.filter((row) => row.volume > latest.volume).length;
  const volumeTopPercent = Math.max(5, Math.ceil(volumeRank / Math.max(windowRows.length, 1) * 20) * 5);
  const isUp = latest.changeRate > .2, isDown = latest.changeRate < -.2;
  let supplyStatus = volumeRatio >= 1.5 ? "매수·매도 공방 · 강함" : volumeRatio >= 1 ? "시장 참여 · 보통" : "관망 우세";
  let supplyClass = "";
  let supplyAnalysis = volumeRatio >= 1 ? "가격은 보합권이지만 거래가 늘어 매수·매도 공방이 확대됐습니다." : "가격과 거래량이 모두 제한돼 관망세가 우세합니다.";
  if (isUp) {
    supplyStatus = volumeRatio >= 1.5 ? "매수 우위 · 강함" : volumeRatio >= 1 ? "매수 우위 · 보통" : "상승 지속성 확인";
    supplyClass = "positive";
    supplyAnalysis = volumeRatio >= 1 ? "가격과 거래량이 함께 증가해 단기 매수세가 강화됐습니다." : "가격은 상승했지만 거래 참여가 줄어 후속 매수세 확인이 필요합니다.";
  } else if (isDown) {
    supplyStatus = volumeRatio >= 1.5 ? "매도 압력 · 강함" : volumeRatio >= 1 ? "매도 압력 · 보통" : "약세 · 거래 부진";
    supplyClass = "negative";
    supplyAnalysis = volumeRatio >= 1 ? "가격 하락과 거래량 증가가 동행해 매도 압력이 확대됐습니다." : "가격은 하락했지만 거래가 줄어 관망 속 약세로 해석됩니다.";
  }
  return { pricePosition, priceBand, priceAverageDiff, volumeRatio, volumeTopPercent, supplyStatus, supplyClass, supplyAnalysis, sampleSize: windowRows.length };
}

function marketInsight(selected, latest) {
  const pulse = marketPulseMetrics(selected, latest);
  if (pulse.sampleSize < 2) return "시세가 누적되면 최근 가격 구간과 거래 강도를 함께 점검합니다.";
  if (pulse.pricePosition >= 90 && pulse.volumeRatio >= 1) return "가격이 최근 20거래일 상단까지 상승했습니다. 평균 이상의 거래가 이어지는지와 최근 고점 안착 여부를 확인해야 합니다.";
  if (pulse.pricePosition >= 90) return "가격은 최근 20거래일 상단이지만 거래 강도는 평균보다 낮습니다. 고점 안착보다 단기 차익매물 출회 여부를 먼저 확인하세요.";
  if (pulse.pricePosition <= 10 && pulse.volumeRatio >= 1) return "가격이 최근 20거래일 하단에 위치하고 거래도 증가했습니다. 추가 매도 압력과 저가 매수 유입 여부를 함께 확인하세요.";
  if (latest.changeRate > 0) return "가격은 상승 흐름을 유지하고 있습니다. 20일 평균 위에서 거래 강도가 확대되는지 확인하면 추세의 지속성을 판단할 수 있습니다.";
  if (latest.changeRate < 0) return "가격은 조정 흐름을 보이고 있습니다. 20일 평균 지지 여부와 거래량을 동반한 추가 하락 가능성을 확인해야 합니다.";
  return "가격은 최근 범위 안에서 움직이고 있습니다. 거래 강도 확대와 20일 고점·저점 돌파 여부를 확인하세요.";
}

function spotAtAuction(auction) {
  if (Number(auction.spotClose) > 0) return { close: Number(auction.spotClose) };
  return state.prices.filter((row) => row.symbol === auction.symbol && row.date <= auction.date).at(-1) || null;
}

function auctionPremiumText(clearingPrice, spotClose) {
  if (!spotClose) return "-";
  const difference = clearingPrice - spotClose;
  const premium = difference / spotClose * 100;
  const percentSign = premium >= 0 ? "+" : "-";
  const amountSign = difference >= 0 ? "+" : "-";
  return `${percentSign}${Math.abs(premium).toFixed(1)}% (${amountSign}${money(Math.abs(difference))}원/톤)`;
}

function auctionInsight(latest, spot) {
  const demand = latest.bidRatio >= 150 ? "응찰수요가 공급물량을 크게 웃돌았습니다" : latest.bidRatio >= 100 ? "응찰수요가 공급물량을 상회했습니다" : "응찰수요가 공급물량에 미치지 못했습니다";
  if (!spot?.close) return `최근 경매 응찰률은 ${latest.bidRatio.toFixed(0)}%, 낙찰가는 ${money(latest.clearingPrice)}원으로 ${demand}. 경매일 현물 종가가 추가되면 가격 차이를 함께 비교합니다.`;
  const premium = (latest.clearingPrice / spot.close - 1) * 100;
  const relation = premium >= 0 ? `낙찰가가 경매일 현물 종가보다 ${Math.abs(premium).toFixed(1)}% 높았습니다` : `낙찰가가 경매일 현물 종가보다 ${Math.abs(premium).toFixed(1)}% 낮았습니다`;
  return `최근 경매 응찰률은 ${latest.bidRatio.toFixed(0)}%로 ${demand}. 낙찰가는 ${money(latest.clearingPrice)}원이며, 경매일 ${relation}.`;
}

function renderAuctionPagination(totalPages) {
  const pagination = byId("auctionPagination");
  pagination.replaceChildren();
  pagination.hidden = totalPages <= 1;
  if (totalPages <= 1) return;
  const move = (page) => {
    state.auctionPage = Math.max(1, Math.min(totalPages, page));
    renderAuctions();
  };
  const previous = create("button", "page-arrow", "‹");
  previous.type = "button"; previous.disabled = state.auctionPage === 1; previous.ariaLabel = "이전 경매 페이지";
  previous.addEventListener("click", () => move(state.auctionPage - 1));
  pagination.append(previous);
  policyPageNumbers(state.auctionPage, totalPages).forEach((value) => {
    if (value === "…") { pagination.append(create("span", "page-gap", value)); return; }
    const button = create("button", value === state.auctionPage ? "active" : "", String(value));
    button.type = "button"; button.ariaLabel = `경매 ${value}페이지`;
    if (value === state.auctionPage) button.setAttribute("aria-current", "page");
    button.addEventListener("click", () => move(value));
    pagination.append(button);
  });
  const next = create("button", "page-arrow", "›");
  next.type = "button"; next.disabled = state.auctionPage === totalPages; next.ariaLabel = "다음 경매 페이지";
  next.addEventListener("click", () => move(state.auctionPage + 1));
  pagination.append(next);
}

function renderAuctions() {
  const rows = filterAuctions(state.auctionPeriod);
  const totalPages = Math.max(1, Math.ceil(rows.length / AUCTIONS_PER_PAGE));
  state.auctionPage = Math.max(1, Math.min(state.auctionPage, totalPages));
  const latest = rows.at(-1) || [...state.auctions].sort((a, b) => a.date.localeCompare(b.date)).at(-1);
  const tbody = byId("auctionRows");
  tbody.replaceChildren();
  byId("auctionPeriodLabel").textContent = auctionPeriodName(state.auctionPeriod);
  byId("auctionRecordCount").textContent = `${rows.length}건 · ${state.auctionPage}/${totalPages}페이지`;
  byId("auctionSync").textContent = state.auctionLastSync ? new Date(state.auctionLastSync).toLocaleString("ko-KR", { timeZone: "Asia/Seoul", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "수동 실행 필요";
  if (!latest) {
    state.auctionPage = 1;
    byId("auctionInsight").textContent = "경매 수집을 처음 실행하면 KRX 전기간 결과가 자동으로 채워집니다.";
    const row = document.createElement("tr"); const cell = document.createElement("td"); cell.colSpan = 8; cell.textContent = "경매 결과가 아직 수집되지 않았습니다."; row.append(cell); tbody.append(row);
    renderAuctionPagination(1);
    return;
  }
  const spot = spotAtAuction(latest);
  const premium = spot?.close ? (latest.clearingPrice / spot.close - 1) * 100 : null;
  byId("auctionPrice").textContent = `${money(latest.clearingPrice)}원`;
  byId("auctionDate").textContent = `${latest.date} · ${latest.symbol}`;
  byId("auctionRatio").textContent = `${latest.bidRatio.toFixed(0)}%`;
  byId("auctionParticipants").textContent = `${latest.bidderCount}개사 응찰 · ${latest.winnerCount}개사 낙찰`;
  byId("auctionSupply").textContent = compactTons(latest.offeredQuantity);
  byId("auctionDemand").textContent = `응찰 ${compactTons(latest.bidQuantity)}`;
  byId("auctionSpread").textContent = premium === null ? "-" : auctionPremiumText(latest.clearingPrice, spot.close);
  byId("auctionSpread").className = premium === null ? "" : premium >= 0 ? "positive" : "negative";
  byId("auctionInsight").textContent = auctionInsight(latest, spot);
  const start = (state.auctionPage - 1) * AUCTIONS_PER_PAGE;
  [...rows].reverse().slice(start, start + AUCTIONS_PER_PAGE).forEach((item) => {
    const itemSpot = spotAtAuction(item);
    const itemPremium = itemSpot?.close ? (item.clearingPrice / itemSpot.close - 1) * 100 : null;
    const tr = document.createElement("tr");
    [item.date, item.symbol, `${money(item.offeredQuantity)}톤`, `${money(item.bidQuantity)}톤`, `${item.bidRatio.toFixed(0)}%`, `${money(item.clearingPrice)}원`, itemSpot?.close ? `${money(itemSpot.close)}원` : "-", itemPremium === null ? "-" : auctionPremiumText(item.clearingPrice, itemSpot.close)].forEach((value) => { const td = document.createElement("td"); td.textContent = value; tr.append(td); });
    tbody.append(tr);
  });
  renderAuctionPagination(totalPages);
}

function renderMarket() {
  const selected = selectedPrices();
  const rows = filterRowsByPeriod(selected, state.period);
  const latest = selected.at(-1) || fallbackPrice[0];
  const metricSelected = state.symbol === CONTINUOUS_SYMBOL ? adjustedContinuousPrices(selected) : selected;
  const metricRows = filterRowsByPeriod(metricSelected, state.period);
  const metricLatest = metricSelected.at(-1) || latest;
  const hasRollover = rolloverIndex(selected) > 0;
  const endTime = new Date(`${latest.date}T00:00:00`).getTime();
  const yearRows = selected.filter((row) => endTime - new Date(`${row.date}T00:00:00`).getTime() <= 366 * 86400000);
  const yearHigh = Math.max(...(yearRows.length ? yearRows : [latest]).map((row) => row.high || row.close));
  const yearLow = Math.min(...(yearRows.length ? yearRows : [latest]).map((row) => row.low || row.close));
  const periodChange = metricRows.length > 1 ? (metricLatest.close / metricRows[0].close - 1) * 100 : latest.changeRate;
  const direction = latest.changeRate >= 0 ? "up" : "down";
  const marketPulse = marketPulseMetrics(metricSelected, metricLatest);
  byId("asofDate").textContent = latest.date.replaceAll("-", "."); byId("symbol").textContent = latest.symbol;
  byId("currentPrice").textContent = money(latest.close);
  byId("dailyChange").className = `change ${direction}`;
  byId("dailyChange").textContent = `${latest.changeRate >= 0 ? "▲" : "▼"} ${money(Math.abs(latest.change))}원  ${Math.abs(latest.changeRate).toFixed(2)}%`;
  byId("rangeLow").textContent = `52주 저 ${money(yearLow)}`; byId("rangeHigh").textContent = `52주 고 ${money(yearHigh)}`;
  byId("rangeMarker").style.left = `${Math.max(2, Math.min(98, (latest.close - yearLow) / Math.max(yearHigh - yearLow, 1) * 100))}%`;
  byId("volume").textContent = money(latest.volume); byId("tradeValue").textContent = `거래대금 ${amount(latest.tradeValue)}`;
  byId("periodChange").textContent = `${periodChange >= 0 ? "+" : ""}${periodChange.toFixed(2)}%`;
  byId("periodChange").className = `kpi-number ${periodChange >= 0 ? "positive" : "negative"}`;
  byId("periodLabel").textContent = `${periodName(state.period)} 기준${hasRollover ? " · 종목전환 보정" : ""}`; byId("recordCount").textContent = `${rows.length}개 거래일`;
  byId("dayRange").textContent = `${money(latest.low)}–${money(latest.high)}`; byId("openPrice").textContent = `시가 ${money(latest.open)}원`;
  byId("pulsePosition").textContent = `${marketPulse.priceBand} ${Math.round(marketPulse.pricePosition)}%`;
  byId("positionTrack").style.width = `${marketPulse.pricePosition}%`;
  byId("pulsePositionDetail").textContent = `20일 평균 대비 ${marketPulse.priceAverageDiff >= 0 ? "+" : ""}${marketPulse.priceAverageDiff.toFixed(1)}%${hasRollover ? " · 전환 보정" : ""}`;
  byId("supplyStatus").textContent = marketPulse.supplyStatus; byId("supplyStatus").className = marketPulse.supplyClass;
  byId("supplyDetail").textContent = `20일 평균의 ${marketPulse.volumeRatio.toFixed(1)}배 · 최근 20일 상위 ${marketPulse.volumeTopPercent}%`;
  byId("supplyAnalysis").textContent = marketPulse.supplyAnalysis;
  byId("checkPoint").textContent = marketInsight(metricSelected, metricLatest);
  renderChart(rows);
}

function renderChart(rows) {
  const svg = byId("priceChart");
  const width = 900, left = 62, right = 22, top = 24, priceBottom = 245, volumeTop = 275, bottom = 326;
  const continuousMode = state.symbol === CONTINUOUS_SYMBOL;
  const start = rows[0]?.date || "", end = rows.at(-1)?.date || "";
  const previewKau26 = continuousMode && start && end ? state.prices
    .filter((row) => row.symbol === "KAU26" && row.date < KAU26_SWITCH_NOT_BEFORE && row.date >= start && row.date <= end)
    .sort((a, b) => a.date.localeCompare(b.date)) : [];
  const previewByDate = new Map(previewKau26.map((row) => [row.date, row]));
  const values = [...rows.map((row) => row.close), ...previewKau26.map((row) => row.close)];
  const { yMax, yMin, ticks: priceTicks } = chartPriceScale(values);
  const maxVol = Math.max(...rows.map((row) => row.volume), 1);
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
  let html = `<defs><linearGradient id="areaGradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#0c7c59" stop-opacity=".2"/><stop offset="100%" stop-color="#0c7c59" stop-opacity="0"/></linearGradient><linearGradient id="continuousAreaGradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#87938e" stop-opacity=".14"/><stop offset="100%" stop-color="#87938e" stop-opacity="0"/></linearGradient><linearGradient id="lineGradient"><stop offset="0%" stop-color="#13a979"/><stop offset="100%" stop-color="#075f48"/></linearGradient></defs>`;
  priceTicks.forEach((value) => { const gy = y(value); html += `<line x1="${left}" x2="${width-right}" y1="${gy}" y2="${gy}" class="grid-line"/><text x="${left-12}" y="${gy+4}" text-anchor="end" class="axis-text">${money(value)}</text>`; });
  if (rows.length > 1) semiMonthlyTicks(start, end).forEach((tick) => { const px = dateX(tick.date); html += `<line x1="${px}" x2="${px}" y1="${top}" y2="${bottom}" class="date-grid-line"/><text x="${px}" y="346" text-anchor="middle" class="axis-text chart-date-text">${tick.label}</text>`; });
  const transitionIndex = rolloverIndex(rows);
  const firstKau26Index = continuousMode ? rows.findIndex((row) => row.symbol === "KAU26") : -1;
  if (rows.length > 1) {
    const primaryLine = rows.map((row, index) => `${index ? "L" : "M"}${x(index)},${y(row.close)}`).join(" ");
    html += `<path d="${primaryLine} L${x(rows.length-1)},${priceBottom} L${x(0)},${priceBottom} Z" class="price-area${continuousMode ? " continuous-price-area" : ""}"/>`;
    if (continuousMode) {
      const grayRows = rows[0]?.symbol === "KAU25" ? rows.slice(0, firstKau26Index > 0 ? firstKau26Index + 1 : rows.length) : [];
      const greenRows = firstKau26Index >= 0 ? rows.slice(firstKau26Index) : [];
      if (grayRows.length > 1) html += `<path d="${grayRows.map((row, index) => `${index ? "L" : "M"}${x(index)},${y(row.close)}`).join(" ")}" class="price-line-kau25"/>`;
      if (greenRows.length > 1) html += `<path d="${greenRows.map((row, index) => `${index ? "L" : "M"}${x(firstKau26Index + index)},${y(row.close)}`).join(" ")}" class="price-line-kau26"/>`;
      const previewSeries = firstKau26Index > 0 ? [...previewKau26, rows[firstKau26Index]] : previewKau26;
      if (previewSeries.length > 1) html += `<path d="${previewSeries.map((row, index) => `${index ? "L" : "M"}${dateX(row.date)},${y(row.close)}`).join(" ")}" class="price-line-kau26-preview"/>`;
    } else html += `<path d="${primaryLine}" class="price-line"/>`;
  }
  if (transitionIndex > 0) {
    const transitionX = x(transitionIndex);
    html += `<line x1="${transitionX}" x2="${transitionX}" y1="${top}" y2="${bottom}" class="rollover-line"/><text x="${transitionX + 7}" y="${top + 13}" class="rollover-label">KAU25 → KAU26</text>`;
  }
  const singlePriceLegend = byId("singlePriceLegend");
  if (singlePriceLegend) singlePriceLegend.hidden = continuousMode;
  ["kau25Legend", "kau26Legend", "kau26PreviewLegend"].forEach((id) => { const legend = byId(id); if (legend) legend.hidden = !continuousMode; });
  const rolloverLegend = byId("rolloverLegend");
  if (rolloverLegend) rolloverLegend.hidden = transitionIndex <= 0;
  previewKau26.forEach((row) => { html += `<circle cx="${dateX(row.date)}" cy="${y(row.close)}" r="3" class="preview-dot"><title>${esc(row.date)} · KAU26 사전가격 ${money(row.close)}원</title></circle>`; });
  rows.forEach((row, index) => {
    const comparison = continuousMode && row.date < KAU26_SWITCH_NOT_BEFORE ? previewByDate.get(row.date) : null;
    const volumeText = `거래량 ${money(row.volume)}톤${comparison ? ` (KAU26 거래량 ${money(comparison.volume)}톤)` : ""}`;
    const barWidth = Math.min(15, (width-left-right)/Math.max(rows.length,12)*.66); const barHeight = row.volume/maxVol*(bottom-volumeTop);
    const dotClass = continuousMode ? `price-dot ${row.symbol === "KAU25" ? "kau25-dot" : "kau26-dot"}` : "price-dot";
    html += `<rect x="${x(index)-barWidth/2}" y="${bottom-barHeight}" width="${barWidth}" height="${barHeight}" rx="2" class="volume-bar" style="fill:#b7d4ca"><title>${esc(row.date)} · ${esc(row.symbol)} · 종가 ${money(row.close)}원 · ${volumeText}</title></rect><circle cx="${x(index)}" cy="${y(row.close)}" r="${rows.length===1?6:3.5}" class="${dotClass}"><title>${esc(row.date)} · ${esc(row.symbol)} · 종가 ${money(row.close)}원 · ${volumeText}</title></circle>`;
  });
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
    const comparison = continuousMode && row.date < KAU26_SWITCH_NOT_BEFORE ? previewByDate.get(row.date) : null;
    const tooltipNodes = [
      create("time", "", `${row.date} · ${row.symbol}`),
      create("strong", "", `${row.symbol} ${money(row.close)}원`)
    ];
    if (comparison) tooltipNodes.push(create("span", "", `KAU26 종가 ${money(comparison.close)}원`));
    tooltipNodes.push(create("span", "", `거래량 ${money(row.volume)}톤${comparison ? ` (KAU26 거래량 ${money(comparison.volume)}톤)` : ""}`));
    tooltip.classList.remove("policy-tooltip");
    tooltip.replaceChildren(...tooltipNodes);
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
  const scheduleMode = state.category === "기관일정";
  const billMode = state.category === "발의법률안";
  const seminarMode = state.category === "국회의원 세미나 일정";
  const items = scheduleMode ? state.institutionSchedules : billMode ? filterBills(state.bills, state.billView) : seminarMode ? state.assemblySeminars : state.policies.filter((item) => policyGroup(item) === state.category);
  const list = byId("policyList"); list.replaceChildren();
  const insight = state.policyInsight?.summary ? state.policyInsight : derivePolicyInsight(state.policies);
  byId("latestPolicy").textContent = insight.summary;
  if (billMode && state.bills.length) renderBillOverview(list, state.bills);
  if (!items.length) {
    state.policyPage = 1;
    const emptyMessage = billMode && state.bills.length ? "선택한 조건에 해당하는 발의법률안이 없습니다." : scheduleMode ? "현재 확인된 기관 일정이 없습니다." : billMode ? (state.billWarning || "현재 확인된 배출권 관련 국회법안이 없습니다.") : seminarMode ? (state.seminarWarning || "현재 확인된 배출권 관련 국회의원 세미나 일정이 없습니다.") : `${state.category}에 해당하는 자료가 없습니다.`;
    list.append(create("div", "loading-state", emptyMessage)); renderChart(filterPrices(state.period)); return;
  }
  const totalPages = Math.ceil(items.length / POLICIES_PER_PAGE);
  state.policyPage = Math.max(1, Math.min(state.policyPage, totalPages));
  const start = (state.policyPage - 1) * POLICIES_PER_PAGE;
  items.slice(start, start + POLICIES_PER_PAGE).forEach((policy) => {
    const row = create("article", `policy-row${scheduleMode ? " schedule-row" : ""}${billMode ? " bill-row" : ""}${seminarMode ? " assembly-seminar-row" : ""}`);
    const primaryDate = scheduleMode ? (policy.publishedAt || policy.startDate) : billMode ? (policy.lastActionDate || policy.proposedDate) : seminarMode ? policy.startDate : policy.publishedAt;
    const date = create("time", scheduleMode || seminarMode ? "schedule-date-stack" : "", shortDate(primaryDate));
    if (scheduleMode) date.append(create("small", "", schedulePlanLabel(policy)));
    if (seminarMode) date.append(create("small", "", seminarPlanLabel(policy)));
    const badge = create("span", "category-badge", scheduleMode || seminarMode ? policy.eventType : billMode ? billStatusLabel(policy) : policyBadge(policy));
    if (billMode) {
      if (billIsReflectedClosure(policy)) badge.classList.add("terminal-reflected");
      else if (billIsTerminal(policy)) badge.classList.add("terminal");
      else if (policy.status === "공포") badge.classList.add("complete");
      else badge.classList.add("in-progress");
    }
    row.append(date, badge);
    const body = create("div", "policy-body");
    const displayTitle = scheduleMode || seminarMode ? (stripScheduleTime(policy.title) || policy.title) : policy.title;
    const title = create("h3", "", displayTitle); title.title = displayTitle;
    if (billMode) {
      const latestAction = create("p", "bill-latest-action");
      latestAction.append(create("span", "", "최근 처리"), document.createTextNode(billLatestActionLabel(policy)));
      const metadataText = billMetadataLabel(policy);
      const metadata = create("p", "bill-metadata", metadataText); metadata.title = policy.summary || metadataText;
      body.append(title, createBillProgress(policy), latestAction, metadata);
    } else {
      const summaryText = scheduleMode ? scheduleDisplaySummary(policy) : seminarMode ? seminarDisplaySummary(policy) : newsDisplaySummary(policy);
      const summary = create("p", "", summaryText); summary.title = summaryText;
      body.append(title, summary);
    }
    const link = create("a", "source-link", "∞"); link.href = policy.url || "#"; link.target = "_blank"; link.rel = "noreferrer"; link.ariaLabel = `${displayTitle} ${billMode ? "국회 의안" : seminarMode ? "국회 세미나" : ""} 원문 링크`; link.title = billMode ? "국회 의안 원문" : seminarMode ? "국회 세미나 공식 일정" : "원문 링크";
    row.append(body, link); list.append(row);
  });
  renderPolicyPagination(list, totalPages);
  renderChart(filterPrices(state.period));
}

function renderPolicyFilters() {
  const categories = ["기후부 보도자료", "기후부 공지사항", "한국거래소 공지사항", "뉴스", "기관일정", "발의법률안", "국회의원 세미나 일정"];
  const box = byId("policyFilters"); box.replaceChildren();
  categories.forEach((category) => { const button = create("button", category === state.category ? "active" : "", category); button.addEventListener("click", () => { state.category = category; state.policyPage = 1; renderPolicyFilters(); renderPolicies(); }); box.append(button); });
  const billMode = state.category === "발의법률안";
  const seminarMode = state.category === "국회의원 세미나 일정";
  byId("policyDescription").textContent = billMode
    ? "국회의원이 발의한 배출권 관련 법률안과 처리단계 변화를 추적합니다."
    : seminarMode
      ? "국회의원·의원실이 주최한 배출권 관련 세미나·토론회 일정을 국회도서관 공식자료에서 수집합니다."
      : "기후부·한국거래소 공식자료와 시장 뉴스를 자동 수집하고, 본문에서 확인된 기관 일정을 중복 없이 정리합니다.";
  const sync = billMode ? state.billLastSync : seminarMode ? state.seminarLastSync : state.policyLastSync;
  byId("policySync").textContent = sync ? new Date(sync).toLocaleString("ko-KR", { timeZone: "Asia/Seoul", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : billMode ? "국회 API 연결 필요" : seminarMode ? "국회 공식일정 연결 대기" : "수동 실행 필요";
}

function renderSymbolPicker() {
  const picker = byId("symbolPicker"), select = byId("symbolSelect");
  const symbols = new Set(state.prices.map((row) => row.symbol).filter(Boolean));
  const choices = [];
  if (symbols.has("KAU25") && symbols.has("KAU26")) choices.push({ value: CONTINUOUS_SYMBOL, label: CONTINUOUS_LABEL });
  [...symbols].filter((symbol) => /^KAU\d+$/.test(symbol)).sort((left, right) => Number(left.slice(3)) - Number(right.slice(3))).forEach((symbol) => choices.push({ value: symbol, label: symbol }));
  select.replaceChildren();
  choices.forEach(({ value, label }) => { const option = create("option", "", label); option.value = value; option.selected = value === state.symbol; select.append(option); });
  picker.hidden = choices.length <= 1;
  select.onchange = () => { state.symbol = select.value; renderMarket(); renderAuctions(); };
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
    summary.append(create("span", "", "상세 브리핑"), create("span", "detail-click-button", "클릭"));
    details.append(summary, create("div", "briefing-content", parts.details));
    feature.append(details);
  }
  layout.append(feature);
  area.replaceChildren(layout);
}

async function init() {
  const [priceResult, auctionResult, policyResult, briefingResult, billResult, seminarResult] = await Promise.allSettled([loadText("data/prices.csv"), loadJson("data/auctions.json"), loadJson("data/policies.json"), loadJson("data/briefing.json"), loadJson("data/bills.json"), loadJson("data/assembly_seminars.json")]);
  state.prices = priceResult.status === "fulfilled" ? parsePrices(priceResult.value) : fallbackPrice;
  if (!state.prices.length) state.prices = fallbackPrice;
  const symbols = new Set(state.prices.map((row) => row.symbol));
  if (symbols.has("KAU25") && symbols.has("KAU26")) state.symbol = CONTINUOUS_SYMBOL;
  else {
    const latestDate = state.prices.at(-1).date;
    const latestRows = state.prices.filter((row) => row.date === latestDate);
    state.symbol = latestRows.reduce((best, row) => !best || row.volume > best.volume ? row : best, null)?.symbol || state.prices.at(-1).symbol;
  }
  const auctionData = auctionResult.status === "fulfilled" ? auctionResult.value : { items: [] };
  state.auctions = normalizeAuctions(auctionData.items || []);
  state.auctionLastSync = auctionData.lastSync || "";
  const policyData = policyResult.status === "fulfilled" ? policyResult.value : { items: [], keywords: [] };
  const rawPolicies = (policyData.items || []).map((item) => ({ ...item, publishedAt: item.publishedAt || item.date || "" }));
  const agendaSchedules = assemblyAgendaSchedules(rawPolicies);
  state.policies = dedupeNewsPolicies(rawPolicies.filter((item) => !(isAssemblyAgendaPolicy(item) && assemblyAgendaEventTitle(item.summary))));
  state.policyLastSync = policyData.lastSync || "";
  state.institutionSchedules = normalizeInstitutionSchedules(mergeAssemblyAgendaSchedules(policyData.institutionSchedules || [], agendaSchedules));
  state.policyInsight = policyData.aiInsight || null;
  const billData = billResult.status === "fulfilled" ? billResult.value : { items: [], warning: "발의법률안 데이터를 불러오지 못했습니다." };
  state.bills = normalizeBills(billData.items || []);
  state.billLastSync = billData.lastSync || "";
  state.billWarning = billData.warning || "";
  const seminarData = seminarResult.status === "fulfilled" ? seminarResult.value : { items: [], warning: "국회의원 세미나 일정 데이터를 불러오지 못했습니다." };
  state.assemblySeminars = normalizeAssemblySeminars(seminarData.items || []);
  state.seminarLastSync = seminarData.lastSync || "";
  state.seminarWarning = seminarData.warning || "";
  const briefingData = briefingResult.status === "fulfilled" ? briefingResult.value : { items: [] };
  state.briefings = (briefingData.items || []).map((item) => ({ ...item, date: item.date || item.briefingDate || "" })).sort((a, b) => b.date.localeCompare(a.date));
  state.briefingDate = state.briefings[0]?.date || "";
  document.querySelectorAll("[data-period]").forEach((button) => button.addEventListener("click", () => { state.period = button.dataset.period; document.querySelectorAll("[data-period]").forEach((item) => item.classList.toggle("active", item === button)); renderMarket(); }));
  document.querySelectorAll("[data-auction-period]").forEach((button) => button.addEventListener("click", () => { state.auctionPeriod = button.dataset.auctionPeriod; state.auctionPage = 1; document.querySelectorAll("[data-auction-period]").forEach((item) => item.classList.toggle("active", item === button)); renderAuctions(); }));
  renderSymbolPicker(); renderPolicyFilters(); renderPolicies(); renderMarket(); renderAuctions(); renderBriefings();
}

init().catch(() => { state.prices = fallbackPrice; renderMarket(); renderAuctions(); });
