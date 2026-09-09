"use strict";
// MOTIE_POLICY_TABS_VERSION = "2026-09-09-v1"

(() => {
  const originalPolicyGroup = policyGroup;

  policyGroup = function extendedPolicyGroup(policy) {
    const section = String(policy?.section || "").trim().toLowerCase();
    const source = String(policy?.source || "");
    const url = String(policy?.url || "");
    if (
      section === "motie_press"
      || source === "산업부 보도자료"
      || /motir\.go\.kr\/kor\/article\/ATCL3f49a5a8c/i.test(url)
    ) return "산업부 보도자료";
    if (
      section === "motie_notice"
      || source === "산업부 공지사항"
      || /motir\.go\.kr\/kor\/article\/ATCL6e90bb9de/i.test(url)
    ) return "산업부 공지사항";
    return originalPolicyGroup(policy);
  };

  renderPolicyFilters = function renderExtendedPolicyFilters() {
    const categories = [
      "기후부 보도자료",
      "기후부 공지사항",
      "산업부 보도자료",
      "산업부 공지사항",
      "한국거래소 공지사항",
      "뉴스",
      "기관일정",
      "발의법률안",
      "국회의원 세미나 일정",
    ];
    const box = byId("policyFilters");
    box.replaceChildren();
    categories.forEach((category) => {
      const button = create("button", category === state.category ? "active" : "", category);
      button.type = "button";
      button.addEventListener("click", () => {
        state.category = category;
        state.policyPage = 1;
        renderPolicyFilters();
        renderPolicies();
      });
      box.append(button);
    });

    const billMode = state.category === "발의법률안";
    const seminarMode = state.category === "국회의원 세미나 일정";
    const motieMode = state.category === "산업부 보도자료" || state.category === "산업부 공지사항";
    byId("policyDescription").textContent = billMode
      ? "국회의원이 발의한 배출권 관련 법률안과 처리단계 변화를 추적합니다."
      : seminarMode
        ? "국회의원·의원실이 주최한 배출권 관련 세미나·토론회 일정을 국회도서관 공식자료에서 수집합니다."
        : motieMode
          ? "산업부 공식 게시판에서 배출권·탄소시장 관련 자료를 제목과 내용 기준으로 수집합니다."
          : "기후부·산업부·한국거래소 공식자료와 시장 뉴스를 자동 수집하고, 본문에서 확인된 기관 일정을 중복 없이 정리합니다.";
    const sync = billMode ? state.billLastSync : seminarMode ? state.seminarLastSync : state.policyLastSync;
    byId("policySync").textContent = sync
      ? new Date(sync).toLocaleString("ko-KR", {
        timeZone: "Asia/Seoul",
        month: "numeric",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
      : billMode
        ? "국회 API 연결 필요"
        : seminarMode
          ? "국회 공식일정 연결 대기"
          : "수동 실행 필요";
  };

  const heading = document.querySelector("#policy .section-title h2");
  if (heading) heading.textContent = "기후·산업 정책 레이더";
  const hero = document.querySelector(".hero-copy");
  if (hero) {
    hero.textContent = "배출권 가격, 기후부·산업부 정책, 한국거래소 공지, 데일리 브리핑을 연결해 시장 변화를 한 흐름으로 보여줍니다.";
  }
})();
