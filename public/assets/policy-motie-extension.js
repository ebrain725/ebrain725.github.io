"use strict";
// MOTIE_POLICY_TABS_VERSION = "2026-09-09-v1"
// POLICY_RADAR_EXCEL_VERSION = "2026-09-09-v1-all-tabs-xlsx"

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

(() => {
  const MANIFEST_URL = "data/policy-radar-excel.json";
  const STYLE_ID = "policyExcelDownloadStyle";
  const BUTTON_ID = "policyExcelDownload";

  function addStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
      .policy-title-actions {
        display: flex;
        align-items: center;
        justify-content: flex-end;
        gap: 10px;
        flex-wrap: wrap;
      }
      .policy-excel-download {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-height: 34px;
        padding: 7px 12px;
        border: 1px solid #2f6f61;
        border-radius: 999px;
        background: #ffffff;
        color: #21594d;
        font-size: 12px;
        font-weight: 800;
        line-height: 1;
        text-decoration: none;
        white-space: nowrap;
        transition: background-color .15s ease, color .15s ease, border-color .15s ease;
      }
      .policy-excel-download::before {
        content: "XLSX";
        margin-right: 7px;
        padding: 3px 5px;
        border-radius: 4px;
        background: #e7f2ee;
        color: #1f5d50;
        font-size: 9px;
        letter-spacing: .04em;
      }
      .policy-excel-download:hover,
      .policy-excel-download:focus-visible {
        border-color: #1f5d50;
        background: #1f5d50;
        color: #ffffff;
        outline: none;
      }
      .policy-excel-download:hover::before,
      .policy-excel-download:focus-visible::before {
        background: rgba(255, 255, 255, .18);
        color: #ffffff;
      }
      @media (max-width: 760px) {
        .policy-title-actions {
          width: 100%;
          justify-content: flex-start;
        }
      }
    `;
    document.head.append(style);
  }

  function installDownloadButton() {
    const sectionTitle = document.querySelector("#policy .section-title");
    if (!sectionTitle || document.getElementById(BUTTON_ID)) return;

    addStyle();
    const actions = document.createElement("div");
    actions.className = "policy-title-actions";
    const autoBadge = sectionTitle.querySelector(".auto-badge");
    if (autoBadge) {
      autoBadge.replaceWith(actions);
      actions.append(autoBadge);
    } else {
      sectionTitle.append(actions);
    }

    const link = document.createElement("a");
    link.id = BUTTON_ID;
    link.className = "policy-excel-download";
    link.textContent = "전체 리스트 엑셀";
    link.setAttribute("aria-label", "정책 레이더 전체 탭의 이벤트 리스트를 엑셀로 다운로드");
    link.hidden = true;
    actions.append(link);

    fetch(`${MANIFEST_URL}?v=${Date.now()}`, { cache: "no-store" })
      .then((response) => {
        if (!response.ok) throw new Error(`manifest ${response.status}`);
        return response.json();
      })
      .then((manifest) => {
        const file = String(manifest?.file || "data/ets-policy-radar-events.xlsx");
        const generatedAt = String(manifest?.generatedAt || "");
        const total = Number(manifest?.totalEventCount || 0);
        const sheetCount = Number(manifest?.sheetCount || 0);
        const dateTag = generatedAt.slice(0, 10).replaceAll("-", "") || "latest";
        link.href = `${file}?v=${encodeURIComponent(generatedAt || Date.now())}`;
        link.download = `ETS_정책레이더_전체이벤트_${dateTag}.xlsx`;
        link.title = `${sheetCount || 11}개 시트 · ${total.toLocaleString("ko-KR")}건 · ${generatedAt || "최신 데이터"}`;
        link.hidden = false;
      })
      .catch(() => {
        link.remove();
        if (!actions.children.length) actions.remove();
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", installDownloadButton, { once: true });
  } else {
    installDownloadButton();
  }
})();
