"use strict";

(() => {
  const DATA_URL = "data/krx-industry-monthly.json";
  const numberFormat = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });
  const state = {
    payload: null,
    updateScheduled: false,
  };

  const byId = (id) => document.getElementById(id);
  const cleanText = (value) => String(value ?? "").replace(/\s+/g, " ").trim();

  function signedNumber(value) {
    const rounded = Math.round(Number(value) || 0);
    if (rounded > 0) return `+${numberFormat.format(rounded)}`;
    if (rounded < 0) return `−${numberFormat.format(Math.abs(rounded))}`;
    return "0";
  }

  function selectedMonthIndexes(startMonth, endMonth) {
    if (!state.payload || !startMonth || !endMonth || startMonth > endMonth) return [];
    return state.payload.months
      .map((month, index) => (month >= startMonth && month <= endMonth ? index : -1))
      .filter((index) => index >= 0);
  }

  function buildIndustryTotals(startMonth, endMonth) {
    const indexes = selectedMonthIndexes(startMonth, endMonth);
    const sectors = state.payload?.coverage?.sectors || [];
    if (!indexes.length) return [];

    return (state.payload?.industries || []).map((item) => {
      const [, sectorIndex, industryName, monthlyValues] = item;
      const value = indexes.reduce((sum, monthIndex) => {
        const pair = Array.isArray(monthlyValues?.[monthIndex])
          ? monthlyValues[monthIndex]
          : [0, 0];
        const sell = Number(pair[0]) || 0;
        const buy = Number(pair[1]) || 0;
        return sum + buy - sell;
      }, 0);
      return {
        sector: cleanText(sectors[sectorIndex]) || "미분류",
        industry: cleanText(industryName) || "미분류",
        value,
      };
    });
  }

  function sectorTotal(rows, sector) {
    return rows
      .filter((row) => row.sector === sector)
      .reduce((sum, row) => sum + row.value, 0);
  }

  function representativeIndustry(rows, sector, direction) {
    const candidates = rows.filter((row) => row.sector === sector);
    if (!candidates.length) return null;
    const sorted = [...candidates].sort((left, right) => (
      direction === "buy"
        ? right.value - left.value
        : left.value - right.value
    ));
    const representative = sorted[0];
    if (direction === "buy" && representative.value <= 0) return null;
    if (direction === "sell" && representative.value >= 0) return null;
    return representative;
  }

  function setKpiDetail(element, sectorValue, representative) {
    if (!element) return;
    const sectorText = `${signedNumber(sectorValue)}톤`;
    if (!representative) {
      element.textContent = sectorText;
      element.title = sectorText;
      return;
    }

    const representativeText = `${representative.industry} ${signedNumber(representative.value)}톤`;
    element.textContent = `${sectorText} (${representativeText})`;
    element.title = `부문 ${sectorText}, 대표 업종 ${representativeText}`;
  }

  function updateRepresentativeIndustries() {
    state.updateScheduled = false;
    if (!state.payload) return;

    const startMonth = byId("industryCumulativeStart")?.value || "";
    const endMonth = byId("industryCumulativeEnd")?.value || "";
    const buyerSector = cleanText(byId("industryCumulativeBuyer")?.textContent);
    const sellerSector = cleanText(byId("industryCumulativeSeller")?.textContent);
    const rows = buildIndustryTotals(startMonth, endMonth);
    if (!rows.length) return;

    if (buyerSector && !["—", "없음"].includes(buyerSector)) {
      const buyerRepresentative = representativeIndustry(rows, buyerSector, "buy");
      setKpiDetail(
        byId("industryCumulativeBuyerValue"),
        sectorTotal(rows, buyerSector),
        buyerRepresentative,
      );
    }

    if (sellerSector && !["—", "없음"].includes(sellerSector)) {
      const sellerRepresentative = representativeIndustry(rows, sellerSector, "sell");
      setKpiDetail(
        byId("industryCumulativeSellerValue"),
        sectorTotal(rows, sellerSector),
        sellerRepresentative,
      );
    }
  }

  function scheduleUpdate() {
    if (state.updateScheduled) return;
    state.updateScheduled = true;
    window.requestAnimationFrame(updateRepresentativeIndustries);
  }

  function observeCumulativeKpis() {
    const targets = [
      byId("industryCumulativeBuyer"),
      byId("industryCumulativeSeller"),
      byId("industryCumulativePeriodLabel"),
    ].filter(Boolean);
    if (!targets.length) return;

    const observer = new MutationObserver(scheduleUpdate);
    targets.forEach((target) => observer.observe(target, {
      childList: true,
      characterData: true,
      subtree: true,
    }));

    document.querySelectorAll("[data-industry-cumulative-period]").forEach((button) => {
      button.addEventListener("click", () => window.setTimeout(scheduleUpdate, 0));
    });
    byId("applyIndustryCumulativeRange")?.addEventListener(
      "click",
      () => window.setTimeout(scheduleUpdate, 0),
    );
  }

  async function init() {
    observeCumulativeKpis();
    try {
      const response = await fetch(DATA_URL, { cache: "no-cache" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      if (!Array.isArray(payload?.months) || !Array.isArray(payload?.industries)) {
        throw new Error("월별 업종 데이터 형식이 올바르지 않습니다.");
      }
      state.payload = payload;
      scheduleUpdate();
    } catch (error) {
      console.error("누적 KPI 대표 업종 계산 실패", error);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
