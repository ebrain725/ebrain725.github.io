#!/usr/bin/env python3
# PRESS_TAB_FIX_VERSION = "2026-08-20-v3.1-fast-current-board"
# NEWS_DEDUPE_VERSION = "2026-08-31-v3.1-google-naver-cross-source"
# NEWS_RELEVANCE_VERSION = "2026-08-31-v3.1-exact-ets-limited-body-check"
# INSTITUTION_SCHEDULE_VERSION = "2026-08-31-v2-event-sentence-relevance-and-content-dedupe"
# NEWS_REGION_VERSION = "2026-08-31-v1-domestic-overseas"
# INSTITUTION_SCHEDULE_YEAR_FIX_VERSION = "2026-08-31-v2-strict-year-inference"
# KRX_NOTICE_VERSION = "2026-08-31-v1-official-board"
# ASSEMBLY_AGENDA_ROUTING_VERSION = "2026-09-01-v1-move-and-dedupe"
"""기후부·한국거래소 공식자료와 시장 뉴스를 수집·정리한다."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "config" / "settings.json"
OUTPUT_PATH = ROOT / "public" / "data" / "policies.json"
KST = ZoneInfo("Asia/Seoul")
RSS_TIMEOUT_SECONDS = 12
ARTICLE_TIMEOUT_SECONDS = 6
OPENAI_TIMEOUT_SECONDS = 30
NAVER_TIMEOUT_SECONDS = 12
KRX_NOTICE_TIMEOUT_SECONDS = 18
NAVER_API_HUB_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"
NAVER_CLIENT_ID_ENV = "NAVER_API_HUB_CLIENT_ID"
NAVER_CLIENT_SECRET_ENV = "NAVER_API_HUB_CLIENT_SECRET"
NAVER_MAX_DISPLAY = 100
NAVER_BODY_VERIFY_LIMIT = 12
SCHEDULE_BODY_VERIFY_LIMIT = 18
SCHEDULE_RETENTION_DAYS = 30
SCHEDULE_HORIZON_DAYS = 400
MAX_SCHEDULE_ITEMS = 80


def load_keyword_file(relative_path: str) -> list[str]:
    """뉴스 전용 키워드를 한 줄에 하나씩 적은 UTF-8 파일에서 읽는다."""
    path = (ROOT / relative_path).resolve()
    root = ROOT.resolve()
    if path != root and root not in path.parents:
        raise RuntimeError("뉴스 키워드 파일은 저장소 내부에 있어야 합니다.")
    if not path.is_file():
        raise RuntimeError(f"뉴스 키워드 파일을 찾지 못했습니다: {relative_path}")
    values: list[str] = []
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        values.extend(value.strip() for value in re.split(r"[,;\t]", line) if value.strip())
    return list(dict.fromkeys(values))


def policy_section(item: dict) -> str:
    """화면 탭에 사용할 보도자료·공지사항·뉴스 구분을 안정적으로 판정한다."""
    if item.get("sourceType") == "news" or item.get("section") == "news":
        return "news"
    source = str(item.get("source", ""))
    url = str(item.get("url", ""))
    if "한국거래소" in source or "ets.krx.co.kr" in url:
        return "krx_notice"
    explicit = str(item.get("section", "")).strip().lower()
    if explicit in {"press", "notice", "krx_notice"}:
        return explicit
    if "보도자료" in source or re.search(r"(?:menuId=(?:286|10598)|boardMasterId=(?:1|939))(?:&|$)", url):
        return "press"
    return "notice"


def source_section(source: dict) -> str:
    explicit = str(source.get("type", "")).strip().lower()
    if explicit in {"press", "notice", "krx_notice"}:
        return explicit
    return policy_section({"source": source.get("name", ""), "url": source.get("url", ""), "sourceType": "official"})


def policy_material_text(item: dict) -> str:
    return f"{item.get('title', '')} {item.get('summary', '')}"


def has_auction_material(items: list[dict]) -> bool:
    return any(re.search(r"유상할당|유상경매|경매|입찰", policy_material_text(item)) for item in items)


def has_active_market_stabilization(items: list[dict]) -> bool:
    """제도 언급이 아니라 실제 발동·물량조정 발표가 있는 경우만 참으로 본다."""
    mechanism = r"시장안정(?:화)?|시장안정화예비분|예비분|K-MSR"
    activation = r"발동|추가\s*공급|공급\s*결정|방출|조정\s*물량|매각\s*공고"
    for item in items:
        text = re.sub(r"\s+", " ", policy_material_text(item))
        if re.search(rf"(?:{mechanism}).{{0,100}}(?:{activation})|(?:{activation}).{{0,100}}(?:{mechanism})", text, re.IGNORECASE):
            return True
    return False


def fallback_policy_insight(items: list[dict]) -> str:
    official = [item for item in items if item.get("sourceType") != "news"][:10]
    if not official:
        return "최근 기후부·한국거래소 공식자료가 수집되면 정책 변화가 시장 수급에 미칠 영향을 분석합니다."
    haystack = " ".join(f"{item.get('title', '')} {item.get('summary', '')}" for item in official)
    auction_material = has_auction_material(official)
    active_stabilization = has_active_market_stabilization(official)
    if active_stabilization:
        return "최근 공식자료에서 시장안정화 조치의 실제 발동 또는 공급물량 조정이 확인됩니다. 현물 수급에 직접 영향을 줄 수 있으므로 발동 시점, 조정물량과 시장 흡수 여부를 우선 점검해야 합니다."
    if auction_material:
        return "최근 공식자료의 직접적인 수급 변수는 유상경매입니다. 최근 경매가 높은 낙찰가로 소화돼 공급보다 이행수요가 강하다는 신호입니다."
    if re.search(r"할당|계획기간|배출허용총량", haystack):
        return "최근 공식자료는 할당체계와 차기 계획기간 운영 구체화에 집중돼 있습니다. 중기 수급 기대가 바뀔 수 있어 할당량, 유상할당 비중과 시행시점을 함께 확인해야 합니다."
    if re.search(r"상쇄|외부사업", haystack):
        return "최근 공식자료는 상쇄배출권과 외부사업 공급 기반 확대에 초점이 맞춰져 있습니다. 실제 인증·발행 물량이 늘어나는 시점까지는 현물 공급 효과가 제한적일 수 있습니다."
    return "최근 공식자료는 제도 운영 구체화에 초점이 맞춰져 있습니다. 단기 방향을 단정하기보다 후속 일정과 실제 공급물량 변화를 확인해야 합니다."


def extract_response_text(payload: dict) -> str:
    parts: list[str] = []
    for output in payload.get("output", []):
        for content in output.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                parts.append(str(content["text"]))
    return " ".join(parts).strip()


def normalized_news_title(value: str) -> str:
    value = re.sub(r"\s+(?:-|\||::)\s+[^-|:]{2,30}$", " ", (value or "").lower())
    value = re.sub(r"\[[^\]]+\]|\([^)]*\)", " ", value)
    return re.sub(r"[^0-9a-z가-힣]+", "", value)


def canonical_news_url(value: str) -> str:
    """기사 URL을 중복 판정 전용 키로 정규화한다.

    실제 출력 URL은 바꾸지 않고, 스킴·www·추적 파라미터 차이만 제거한다.
    기사 식별에 쓰일 수 있는 일반 쿼리 파라미터는 그대로 보존한다.
    """
    value = html.unescape(str(value or "")).strip()
    if not re.match(r"^https?://", value, re.IGNORECASE):
        return ""
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return ""
    hostname = (parsed.hostname or "").lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    if not hostname:
        return ""
    try:
        parsed_port = parsed.port
    except ValueError:
        return ""
    port = f":{parsed_port}" if parsed_port and parsed_port not in {80, 443} else ""
    path = re.sub(r"/{2,}", "/", parsed.path or "/").rstrip("/") or "/"
    tracking_names = {
        "fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "ref", "source",
        "campaign", "cmpid", "ncid", "n_media", "n_query", "n_rank", "n_ad_group",
        "n_ad", "n_keyword", "n_keyword_id",
    }
    query = [
        (key, val)
        for key, val in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in tracking_names
    ]
    query_text = urllib.parse.urlencode(sorted(query))
    return f"{hostname}{port}{path}{'?' + query_text if query_text else ''}"


def publisher_name_from_url(value: str, fallback: str = "NAVER 뉴스") -> str:
    """NAVER 응답의 원문 URL에서 노출 가능한 출처명을 만든다."""
    try:
        hostname = (urllib.parse.urlsplit(value).hostname or "").lower()
    except ValueError:
        return fallback
    hostname = re.sub(r"^(?:www|m|news)\.", "", hostname)
    return hostname or fallback


def preferred_news_item_score(item: dict) -> tuple[int, int, int]:
    """Google 리다이렉트보다 언론사 원문 링크를 대표기사로 우선한다."""
    try:
        hostname = (urllib.parse.urlsplit(str(item.get("url", ""))).hostname or "").lower()
    except ValueError:
        hostname = ""
    aggregator = hostname in {
        "news.google.com", "news.naver.com", "n.news.naver.com", "m.news.naver.com",
    }
    return (
        0 if aggregator else 1,
        len(str(item.get("summary", ""))),
        len(str(item.get("title", ""))),
    )


def title_bigrams(value: str) -> set[str]:
    return {value[index : index + 2] for index in range(max(0, len(value) - 1))}


def normalized_news_summary(value: str) -> str:
    """검색 공급자마다 다른 HTML·문장부호를 제거한 중복판정용 요약문."""
    value = re.sub(r"<[^>]+>", " ", html.unescape(value or "")).lower()
    return re.sub(r"[^0-9a-z가-힣]+", "", value)


def text_ngram_dice(first: str, second: str, size: int = 3) -> float:
    if len(first) < size or len(second) < size:
        return 0.0
    grams_a = {first[index : index + size] for index in range(len(first) - size + 1)}
    grams_b = {second[index : index + size] for index in range(len(second) - size + 1)}
    return 2 * len(grams_a & grams_b) / max(len(grams_a) + len(grams_b), 1)


def similar_news_summary(first: str, second: str) -> bool:
    """같은 보도자료를 옮긴 기사처럼 요약문이 사실상 같은 경우를 찾는다."""
    a, b = normalized_news_summary(first), normalized_news_summary(second)
    if min(len(a), len(b)) < 55:
        return False
    ratio = SequenceMatcher(None, a, b).ratio()
    dice = text_ngram_dice(a, b)
    return ratio >= 0.78 or dice >= 0.72


def similar_news_title(first: str, second: str) -> bool:
    a, b = normalized_news_title(first), normalized_news_title(second)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = sorted((a, b), key=len)
    if len(shorter) >= 14 and shorter in longer and len(shorter) / len(longer) >= 0.55:
        return True
    ratio = SequenceMatcher(None, a, b).ratio()
    grams_a, grams_b = title_bigrams(a), title_bigrams(b)
    dice = 2 * len(grams_a & grams_b) / max(len(grams_a) + len(grams_b), 1)
    return ratio >= 0.78 and dice >= 0.65


NEWS_GENERIC_TOKENS = {
    "배출권", "탄소배출권", "온실가스", "탄소", "탄소시장", "시장", "배출권거래제", "거래제",
    "뉴스", "특집", "단독", "속보", "관련", "대응", "추진", "강화", "사업", "한국", "국내",
    "kau25", "kau26",
}
NEWS_PARTICLES = ("으로", "에서", "에게", "까지", "부터", "처럼", "보다", "만큼", "은", "는", "이", "가", "을", "를", "의", "와", "과", "도", "만")


def news_event_tokens(value: str) -> set[str]:
    cleaned = re.sub(r"\[[^\]]+\]|\([^)]*\)", " ", (value or "").lower())
    result: set[str] = set()
    for token in re.findall(r"[a-z]+|[가-힣]{2,}", cleaned):
        particle = next((item for item in NEWS_PARTICLES if len(token) >= 4 and token.endswith(item)), "")
        if particle:
            token = token[: -len(particle)]
        if len(token) >= 2 and token not in NEWS_GENERIC_TOKENS:
            result.add(token)
    return result


def news_date_distance(first: str, second: str) -> int:
    try:
        return abs((datetime.strptime(first, "%Y-%m-%d") - datetime.strptime(second, "%Y-%m-%d")).days)
    except (TypeError, ValueError):
        return 999


NEWS_DIRECTION_CONFLICTS = (
    ({"상승", "급등", "반등", "강세", "확대", "증가", "상향"}, {"하락", "급락", "약세", "축소", "감소", "하향"}),
    ({"매수", "순매수"}, {"매도", "순매도"}),
    ({"인상"}, {"인하"}),
    ({"도입"}, {"폐지"}),
    ({"개시", "시작", "재개"}, {"종료", "중단", "연기"}),
    ({"통과", "가결", "확정"}, {"부결", "철회", "무산"}),
)


def conflicting_news_claim(first: str, second: str) -> bool:
    """문구가 비슷해도 방향·핵심 수치가 반대인 기사는 합치지 않는다."""
    a = clean_html(first, 2_000).lower()
    b = clean_html(second, 2_000).lower()
    for positive, negative in NEWS_DIRECTION_CONFLICTS:
        a_positive, a_negative = any(word in a for word in positive), any(word in a for word in negative)
        b_positive, b_negative = any(word in b for word in positive), any(word in b for word in negative)
        if (a_positive and not a_negative and b_negative and not b_positive) or (
            b_positive and not b_negative and a_negative and not a_positive
        ):
            return True

    kau_a = set(re.findall(r"\bkau\s*[-_]?\s*\d{2}\b", a, re.IGNORECASE))
    kau_b = set(re.findall(r"\bkau\s*[-_]?\s*\d{2}\b", b, re.IGNORECASE))
    if kau_a and kau_b and kau_a.isdisjoint(kau_b):
        return True

    def claims(value: str) -> dict[str, set[str]]:
        result: dict[str, set[str]] = {}
        pattern = r"(\d[\d,.]*(?:만\d[\d,]*)?(?:천)?)\s*(만톤|억원|조원|%|원|톤)"
        for number, unit in re.findall(pattern, value):
            result.setdefault(unit, set()).add(number.replace(",", ""))
        return result

    claims_a, claims_b = claims(a), claims(b)
    return any(
        claims_a[unit] and claims_b[unit] and claims_a[unit].isdisjoint(claims_b[unit])
        for unit in claims_a.keys() & claims_b.keys()
    )


def news_claim_text(item: dict) -> str:
    return f"{item.get('title', '')} {item.get('summary', '')}"


def same_news_story(first: dict, second: dict) -> bool:
    first_url = canonical_news_url(first.get("url", ""))
    second_url = canonical_news_url(second.get("url", ""))
    if first_url and first_url == second_url:
        return True
    distance = news_date_distance(first.get("publishedAt", ""), second.get("publishedAt", ""))
    if distance != 0 or conflicting_news_claim(news_claim_text(first), news_claim_text(second)):
        return False
    first_title = normalized_news_title(first.get("title", ""))
    second_title = normalized_news_title(second.get("title", ""))
    if not first_title or not second_title:
        return False
    if first_title == second_title:
        return True
    if distance <= 1 and similar_news_summary(first.get("summary", ""), second.get("summary", "")):
        return True
    grams_a, grams_b = title_bigrams(first_title), title_bigrams(second_title)
    dice = 2 * len(grams_a & grams_b) / max(len(grams_a) + len(grams_b), 1)
    if distance == 0:
        if similar_news_title(first.get("title", ""), second.get("title", "")):
            return True
        tokens_a = news_event_tokens(first.get("title", ""))
        tokens_b = news_event_tokens(second.get("title", ""))
        shared = tokens_a & tokens_b
        coverage = len(shared) / max(min(len(tokens_a), len(tokens_b)), 1)
        has_specific_token = any(len(token) >= 4 for token in shared)
        return (
            len(shared) >= 2
            and sum(len(token) for token in shared) >= 7
            and coverage >= 0.55
            and has_specific_token
        ) or (
            len(shared) >= 3
            and sum(len(token) for token in shared) >= 10
            and coverage >= 0.40
            and has_specific_token
        )
    return False


def metadata_values(value: object) -> list[str]:
    """이전 JSON의 문자열/배열 메타데이터를 모두 안전하게 읽는다."""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def merge_news_metadata(first: dict, second: dict) -> dict:
    """같은 URL이 재수집돼도 기존 중복 메타데이터와 검증 상태를 잃지 않는다."""
    representative = dict(max((first, second), key=preferred_news_item_score))
    representative["matchedKeywords"] = list(dict.fromkeys([
        *metadata_values(first.get("matchedKeywords")),
        *metadata_values(second.get("matchedKeywords")),
    ]))
    representative["duplicateSources"] = list(dict.fromkeys([
        *metadata_values(first.get("duplicateSources")),
        str(first.get("source", "")).strip(),
        *metadata_values(second.get("duplicateSources")),
        str(second.get("source", "")).strip(),
    ]))
    representative["searchProviders"] = list(dict.fromkeys([
        *metadata_values(first.get("searchProviders")),
        str(first.get("searchProvider", "")).strip(),
        *metadata_values(second.get("searchProviders")),
        str(second.get("searchProvider", "")).strip(),
    ]))
    representative["duplicateSources"] = [value for value in representative["duplicateSources"] if value]
    representative["searchProviders"] = [value for value in representative["searchProviders"] if value]
    representative["duplicateCount"] = max(
        1,
        int(first.get("duplicateCount") or 1),
        int(second.get("duplicateCount") or 1),
    )
    if first.get("_trustedSearchMatch") or second.get("_trustedSearchMatch"):
        representative["_trustedSearchMatch"] = True
    pending_queries = list(dict.fromkeys([
        *metadata_values(first.get("_bodyVerificationQueries")),
        *metadata_values(second.get("_bodyVerificationQueries")),
    ]))
    if pending_queries:
        representative["_bodyVerificationQueries"] = pending_queries
    body_score = max(
        int(first.get("_bodyCandidateScore") or 0),
        int(second.get("_bodyCandidateScore") or 0),
    )
    if body_score:
        representative["_bodyCandidateScore"] = body_score
    return representative


def compatible_news_group(candidate: dict, group: list[dict]) -> bool:
    """단일 연결식 군집에서 반대 내용이 다른 기사를 타고 합쳐지는 것을 막는다."""
    candidate_url = canonical_news_url(candidate.get("url", ""))
    for member in group:
        member_url = canonical_news_url(member.get("url", ""))
        if candidate_url and candidate_url == member_url:
            continue
        if news_date_distance(candidate.get("publishedAt", ""), member.get("publishedAt", "")) != 0:
            return False
        if conflicting_news_claim(news_claim_text(candidate), news_claim_text(member)):
            return False
    return True


def dedupe_news(items: list[dict]) -> list[dict]:
    remaining = list(items)
    groups: list[list[dict]] = []
    while remaining:
        group = [remaining.pop(0)]
        changed = True
        while changed:
            changed = False
            for candidate in list(remaining):
                if (
                    any(same_news_story(candidate, member) for member in group)
                    and compatible_news_group(candidate, group)
                ):
                    group.append(candidate)
                    remaining.remove(candidate)
                    changed = True
        groups.append(group)

    result: list[dict] = []
    for group in groups:
        representative = max(group, key=preferred_news_item_score)
        representative = dict(representative)
        sources = list(dict.fromkeys(
            source
            for item in group
            for source in [*metadata_values(item.get("duplicateSources")), item.get("source", "")]
            if source
        ))
        providers = list(dict.fromkeys(
            provider
            for item in group
            for provider in [*metadata_values(item.get("searchProviders")), item.get("searchProvider", "")]
            if provider
        ))
        observed_evidence = {
            canonical_news_url(item.get("url", ""))
            or f"{item.get('source', '')}|{item.get('publishedAt', '')}|{normalized_news_title(item.get('title', ''))}"
            for item in group
        }
        previous_count = max(max(1, int(item.get("duplicateCount") or 1)) for item in group)
        # 기존 JSON과 신규 Google/NAVER 결과를 다시 합칠 때 duplicateCount가
        # 실행할 때마다 누적 증가하지 않도록 관측된 고유 근거 수와 기존 최댓값만 비교한다.
        representative["duplicateCount"] = max(previous_count, len(observed_evidence), len(sources), 1)
        representative["duplicateSources"] = sources
        representative["searchProviders"] = providers
        result.append(representative)
    return sorted(result, key=lambda item: (item.get("publishedAt", ""), item.get("title", "")), reverse=True)


def generate_policy_insight(items: list[dict]) -> dict:
    official = [item for item in items if item.get("sourceType") != "news"][:10]
    now = datetime.now(KST).isoformat(timespec="seconds")
    fallback = fallback_policy_insight(official)
    result = {
        "summary": fallback,
        "generatedAt": now,
        "basisLatestDate": official[0].get("publishedAt", "-") if official else "-",
        "basisCount": len(official),
        "source": "fallback",
        "model": None,
    }
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key or not official:
        return result

    model = os.getenv("OPENAI_MODEL", "gpt-5-mini").strip() or "gpt-5-mini"
    materials = "\n".join(
        f"- {item.get('publishedAt', '')} | {item.get('source', '기후부 공식자료')} | {item.get('title', '')} | {clean_html(item.get('summary', ''), 320)}"
        for item in official
    )
    prompt = (
        "당신은 한국 배출권거래제(K-ETS) 정책 분석가입니다. 아래 기후부 및 한국거래소 배출권시장 공식자료만 근거로 최근 정책 인사이트를 작성하세요. "
        "기사나 외부 사실은 사용하지 마세요. 상방·하방·혼합·중립 같은 방향등급을 만들거나 가격 방향을 단정하지 말고, 제목을 나열하지도 마세요. "
        "자료의 최근성과 실제 시행 여부를 가장 중요하게 보세요. 유상경매 입찰계획·공고·결과가 있으면 실제 공급물량, 응찰률과 낙찰가를 우선 수급 변수로 다루세요. "
        "최근 경매 결과가 있으면 공급물량 자체보다 낙찰가·응찰강도가 보여주는 실제 이행수요를 해석하세요. "
        "'유상할당 및 시장안정화 조치를 위한 배출권 추가할당에 관한 규정'이라는 법령명이나 K-MSR 도입·논의만으로 시장안정화 조치가 작동 중이라고 판단하면 안 됩니다. "
        "실제 발동 공고가 없으면 시장안정화를 아예 언급하지 마세요. "
        "첫 문장은 핵심 수급 변수를 짚고, 둘째 문장은 확인된 경매 결과의 의미를 분석하세요. 자연스러운 한국어 2문장, 70~120자로 작성하고 문장만 출력하세요.\n\n" + materials
    )
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps({"model": model, "input": prompt, "max_output_tokens": 300}, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=OPENAI_TIMEOUT_SECONDS) as response:
            text = extract_response_text(json.loads(response.read().decode("utf-8")))
        text = re.sub(r"\s+", " ", text).strip().strip('"')
        if len(text) >= 40:
            # 실제 발동 근거가 없는데 AI가 시장안정화를 현재 변수로 끌어올리면
            # 검증된 유상경매 중심 문구를 유지한다.
            ungrounded_stabilization = (
                has_auction_material(official)
                and not has_active_market_stabilization(official)
                and bool(re.search(r"시장안정|K-MSR|예비분", text, re.IGNORECASE))
            )
            if not ungrounded_stabilization:
                result.update({"summary": text[:260], "source": "openai", "model": model})
    except Exception as exc:
        print(f"AI 정책 인사이트 경고: {exc}", file=sys.stderr)
    return result


def clean_html(value: str, limit: int = 260) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else f"{text[: limit - 1].rstrip()}…"


NEWS_PHRASE_PARTS = {
    "배출권거래제": ("배출권", "거래제"),
    "탄소배출권": ("탄소", "배출권"),
    "온실가스배출권": ("온실가스", "배출권"),
    "유상할당": ("유상", "할당"),
    "유상경매": ("유상", "경매"),
    "탄소시장": ("탄소", "시장"),
    "상쇄배출권": ("상쇄", "배출권"),
}


def normalized_news_visible_text(value: str) -> str:
    """기사의 보이는 문자를 보존해 정확 문구 검사에 사용할 형태로 만든다."""
    text = unicodedata.normalize("NFKC", html.unescape(str(value or "")))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[\u200b-\u200d\u2060\ufeff]", "", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def news_phrase_pattern(keyword: str) -> re.Pattern[str] | None:
    """NAVER가 문구를 낱말로 분리해 검색해도 우리는 인접한 ETS 문구만 허용한다."""
    compact = re.sub(r"[\s_]+", "", normalized_news_visible_text(keyword))
    if not compact:
        return None
    if re.fullmatch(r"kau\d{2}", compact):
        year = compact[3:]
        return re.compile(
            rf"(?<![a-z0-9])kau\s*[-‐‑‒–—−_]?\s*{re.escape(year)}(?![a-z0-9])",
            re.IGNORECASE,
        )
    hyphenless = re.sub(r"[-‐‑‒–—−]", "", compact)
    if hyphenless == "kets":
        return re.compile(r"(?<![a-z0-9])k\s*[-‐‑‒–—−_]?\s*ets(?![a-z0-9])", re.IGNORECASE)
    compact = hyphenless
    parts = NEWS_PHRASE_PARTS.get(compact)
    if parts:
        return re.compile(r"\s*".join(re.escape(part) for part in parts), re.IGNORECASE)
    return re.compile(re.escape(compact), re.IGNORECASE)


def news_phrase_matches(value: str, keywords: list[str]) -> list[str]:
    """제목·요약 또는 검증한 본문에 실제로 나타난 설정 문구만 반환한다."""
    visible = normalized_news_visible_text(value)[:20_000]
    matched: list[str] = []
    for keyword in keywords:
        pattern = news_phrase_pattern(keyword)
        if pattern and pattern.search(visible):
            matched.append(keyword)
    return list(dict.fromkeys(matched))


def news_field_phrase_matches(title: str, summary: str, keywords: list[str]) -> list[str]:
    """제목 끝과 요약 시작의 낱말이 합쳐져 오탐이 되지 않도록 따로 검사한다."""
    return list(dict.fromkeys([
        *news_phrase_matches(title, keywords),
        *news_phrase_matches(summary, keywords),
    ]))


def naver_body_candidate_score(value: str) -> int:
    """본문을 열어볼 가치가 있는 ETS 문맥만 제한적으로 선별한다."""
    text = clean_html(value, 4_000).lower()
    score = 0
    if re.search(r"배출권|k\s*[-–—_]?\s*ets|kau\s*[-_]?\s*\d{2}", text, re.IGNORECASE):
        score += 3
    if re.search(r"탄소|온실가스|배출", text):
        score += 1
    if re.search(r"거래|시장|할당|경매|상쇄|감축|규제|이행", text):
        score += 1
    return score


def html_attribute(tag_text: str, name: str) -> str:
    match = re.search(rf"\b{name}\s*=\s*(['\"])(.*?)\1", tag_text, re.IGNORECASE | re.DOTALL)
    return html.unescape(match.group(2)).strip() if match else ""


def extract_article_text(page_html: str) -> str:
    cleaned_html = re.sub(
        r"<(?:script|style|nav|aside|footer)\b[^>]*>[\s\S]*?</(?:script|style|nav|aside|footer)>",
        " ",
        page_html,
        flags=re.IGNORECASE,
    )
    structured: list[str] = []
    for match in re.finditer(r'"articleBody"\s*:\s*("(?:\\.|[^"\\])*")', page_html, re.IGNORECASE):
        try:
            structured.append(clean_html(json.loads(match.group(1)), 20_000))
        except (json.JSONDecodeError, TypeError):
            pass
    useful = [text for text in structured if len(text) >= 80]
    if useful:
        return max(useful, key=len)[:20_000]

    dedicated: list[str] = []
    dedicated_pattern = re.compile(
        r"<(?:div|section)\b[^>]*(?:class|id)\s*=\s*(['\"])[^'\"]*"
        r"(?:article[-_ ]?body|article[-_ ]?content|news[-_ ]?body|news[-_ ]?content|story[-_ ]?body|"
        r"entry[-_ ]?content|post[-_ ]?content|board[-_ ]?view|board[-_ ]?content|bbs[-_ ]?view|"
        r"view[-_ ]?content|view[-_ ]?cont|view[-_ ]?txt)[^'\"]*\1[^>]*>([\s\S]*?)</(?:div|section)>",
        re.IGNORECASE,
    )
    for match in dedicated_pattern.finditer(cleaned_html):
        dedicated.append(clean_html(match.group(2) or "", 20_000))
    useful = [text for text in dedicated if len(text) >= 80]
    if useful:
        return max(useful, key=len)[:20_000]

    article_blocks = [
        clean_html(match.group(1), 20_000)
        for match in re.finditer(r"<article\b[^>]*>([\s\S]*?)</article>", cleaned_html, re.IGNORECASE)
    ]
    useful = [text for text in article_blocks if len(text) >= 80]
    if useful:
        return max(useful, key=len)[:20_000]

    meta_candidates: list[str] = []
    for meta in re.findall(r"<meta\b[^>]*>", page_html, re.IGNORECASE):
        key = f"{html_attribute(meta, 'property')} {html_attribute(meta, 'name')}".lower()
        if re.search(r"og:description|twitter:description|description", key):
            meta_candidates.append(clean_html(html_attribute(meta, "content"), 20_000))
    useful = [text for text in meta_candidates if len(text) >= 80]
    if useful:
        return max(useful, key=len)[:20_000]

    main_blocks = [
        clean_html(match.group(1), 20_000)
        for match in re.finditer(r"<main\b[^>]*>([\s\S]*?)</main>", cleaned_html, re.IGNORECASE)
    ]
    useful = [text for text in main_blocks if len(text) >= 80]
    return max(useful, key=len)[:20_000] if useful else ""


def fetch_article_text(url: str) -> str:
    if not re.match(r"^https?://", url or "", re.IGNORECASE):
        return ""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 ETS-SIGNAL/1.0",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=ARTICLE_TIMEOUT_SECONDS) as response:
            content_type = response.headers.get("Content-Type", "")
            if not re.search(r"text/html|application/xhtml\+xml", content_type, re.IGNORECASE):
                return ""
            charset = response.headers.get_content_charset() or "utf-8"
            page_html = response.read(2_000_000).decode(charset, errors="replace")
        return extract_article_text(page_html)
    except Exception:
        return ""


def keyword_excerpt(text: str, matched_keywords: list[str], limit: int = 260) -> str:
    lower = text.lower()
    positions: list[int] = []
    for keyword in matched_keywords:
        pattern = news_phrase_pattern(keyword)
        phrase_match = pattern.search(text) if pattern else None
        position = phrase_match.start() if phrase_match else lower.find(keyword.lower())
        if position >= 0:
            positions.append(position)
    start = max(0, min(positions) - 80) if positions else 0
    excerpt = text[start : start + limit].strip()
    return f"{'…' if start else ''}{excerpt}{'…' if start + limit < len(text) else ''}"


SCHEDULE_EVENT_PATTERNS = (
    ("유상경매", r"유상\s*(?:할당\s*)?경매|배출권.{0,18}입찰|입찰.{0,18}배출권"),
    ("공청회", r"공청회"),
    ("설명회", r"설명회"),
    ("간담회", r"간담회"),
    ("세미나", r"세미나|포럼|토론회|심포지엄|컨퍼런스"),
    ("협의회", r"협의회|위원회|관계기관\s*회의|회의를?\s*(?:개최|연다|진행)"),
    ("접수마감", r"접수|신청|모집|공모|의견\s*제출|제출\s*마감|마감"),
    ("시행", r"시행|적용|발효|개시|거래\s*(?:시작|종료)"),
    ("발표", r"발표|공개|공고"),
)
SCHEDULE_EVENT_CUE = re.compile(
    "|".join(f"(?:{pattern})" for _, pattern in SCHEDULE_EVENT_PATTERNS),
    re.IGNORECASE,
)
SCHEDULE_FUTURE_CUE = re.compile(
    r"오는|다가오는|예정|개최(?:한다|할|될|된다)|열(?:린다|릴|기로)|실시(?:한다|할)|진행(?:한다|할)|"
    r"접수(?:한다|할|받)|신청|모집|마감(?:한다|할)|시행(?:한다|할|된다)|적용(?:한다|할|된다)|"
    r"발효(?:한다|할|된다)|발표(?:한다|할|된다)|공개(?:한다|할)|공고(?:한다|할)|입찰|경매|"
    r"부터|까지|개시|시작",
    re.IGNORECASE,
)
SCHEDULE_CROSS_YEAR_CUE = re.compile(
    r"오는|다가오는|예정|개최(?:한다|할|될|된다)|열(?:린다|릴|기로)|실시(?:한다|할)|진행(?:한다|할)|"
    r"접수(?:한다|할|받)|신청(?:한다|할|받)|모집(?:한다|할)|마감(?:한다|할)|시행(?:한다|할|된다)|"
    r"적용(?:한다|할|된다)|발효(?:한다|할|된다)|발표(?:한다|할|된다)|공개(?:한다|할)|"
    r"공고(?:한다|할)|개시|시작",
    re.IGNORECASE,
)
SCHEDULE_PAST_CUE = re.compile(
    r"지난|앞서|당시|개최했다|열렸다|마쳤다|진행됐다|진행되었다|참석했다|발표했다|"
    r"시행됐다|시행되었다|종료됐다|종료되었다",
    re.IGNORECASE,
)
SCHEDULE_MARKET_CONTEXT = re.compile(
    r"배출권|배출허용총량|유상\s*할당|무상\s*할당|유상\s*경매|"
    r"탄소\s*(?:시장|가격|배출권|국경)|상쇄|외부사업|시장\s*안정|할당\s*계획|"
    r"(?<![A-Za-z0-9])K\s*[-_]?\s*ETS(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])K(?:AU|CU|OC)\s*\d{0,2}(?![A-Za-z0-9])|"
    r"온실가스|탄소중립|기후(?:위기|대응|정책|외교)?|국제\s*감축|"
    r"탄소국경(?:조정)?|(?<![A-Za-z0-9])CBAM(?![A-Za-z0-9])|에너지|전력망",
    re.IGNORECASE,
)
SCHEDULE_NON_EVENT_CONTEXT = re.compile(
    r"청원|동의\s*(?:진행|필요|마감)|5\s*만\s*명|국민동의|서명\s*운동",
    re.IGNORECASE,
)
SCHEDULE_INSTITUTIONS = (
    ("기후에너지환경부", r"기후에너지환경부|기후부"),
    ("한국거래소", r"한국거래소|(?<![A-Za-z0-9])KRX(?![A-Za-z0-9])"),
    ("온실가스종합정보센터", r"온실가스종합정보센터"),
    ("한국환경공단", r"한국환경공단"),
    ("대한상공회의소", r"대한상공회의소|대한상의"),
    ("산업통상부", r"산업통상자원부|산업통상부|산업부"),
    ("기획재정부", r"기획재정부|기재부"),
)
ASSEMBLY_AGENDA_TITLE = re.compile(
    r"^\s*[\[【〖〈<「『(]*\s*(?:오늘(?:의)?\s*)?국회\s*(?:주요\s*)?(?:의사\s*)?일정"
    r"(?=\s|$|[\]】〗〉>」』):：(\[])",
    re.IGNORECASE,
)
SCHEDULE_GENERIC_TOKENS = {
    "배출권", "탄소", "탄소시장", "배출권거래제", "국내", "관련", "기관", "정책", "시장",
    "개최", "예정", "진행", "실시", "발표", "공개", "공고", "접수", "신청", "모집",
    "설명회", "공청회", "간담회", "세미나", "포럼", "회의", "협의회", "유상경매", "입찰",
}
SCHEDULE_PARTICLES = (
    "으로부터", "에게서", "에서는", "으로", "에서", "에게", "부터", "까지", "처럼", "보다",
    "만큼", "이라", "라고", "이며", "에는", "으로는", "은", "는", "이", "가", "을", "를",
    "의", "와", "과", "도", "만", "에", "로",
)
SCHEDULE_BOILERPLATE_STEMS = (
    "개최", "예정", "진행", "실시", "발표", "공개", "공고", "접수", "신청", "모집",
    "설명회", "공청회", "간담회", "세미나", "포럼", "토론회", "회의", "협의회",
    "유상경매", "경매", "입찰",
)
SCHEDULE_ORGANIZER_TOKENS = {
    "기후에너지환경부", "기후부", "한국거래소", "krx", "온실가스종합정보센터", "한국환경공단",
    "대한상공회의소", "대한상의", "산업통상부", "산업통상자원부", "산업부", "기획재정부", "기재부",
    "오전", "오후", "오는", "일정", "대상",
}


def schedule_iso_date(value: object) -> datetime | None:
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d").replace(tzinfo=KST)
    except (TypeError, ValueError):
        return None


def is_assembly_agenda_article(item: dict) -> bool:
    """`오늘의 국회일정` 종합기사를 일반 뉴스가 아닌 기관일정으로 보낸다."""
    if item.get("sourceType") != "news" and item.get("section") != "news":
        return False
    title = normalized_news_visible_text(str(item.get("title", "")))
    return bool(ASSEMBLY_AGENDA_TITLE.search(title))


def schedule_date_value(year: int, month: int, day: int) -> datetime | None:
    try:
        return datetime(year, month, day, tzinfo=KST)
    except ValueError:
        return None


def schedule_month_shift(base: datetime, months: int) -> tuple[int, int]:
    total = base.year * 12 + base.month - 1 + months
    return total // 12, total % 12 + 1


def resolve_schedule_month_day(
    published: datetime,
    month: int,
    day: int,
    year: int | None = None,
    *,
    force_next_year: bool = False,
    allow_cross_year: bool = False,
) -> datetime | None:
    if year is not None:
        return schedule_date_value(year, month, day)

    target_year = published.year + 1 if force_next_year else published.year
    candidate = schedule_date_value(target_year, month, day)
    if (
        candidate
        and allow_cross_year
        and not force_next_year
        and published.month >= 11
        and month <= 2
        and candidate.date() < published.date()
    ):
        candidate = schedule_date_value(published.year + 1, month, day)
    return candidate


def schedule_time_value(text: str) -> str:
    match = re.search(r"(?:(오전|오후|낮|밤)\s*)?(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?", text)
    if match:
        period, hour_text, minute_text = match.groups()
        hour, minute = int(hour_text), int(minute_text or 0)
        if period in {"오후", "밤"} and hour < 12:
            hour += 12
        elif period in {"오전", "낮"} and hour == 12:
            hour = 0 if period == "오전" else 12
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    match = re.search(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)", text)
    return f"{int(match.group(1)):02d}:{match.group(2)}" if match else ""


def schedule_date_mentions(text: str, published_at: str) -> list[dict]:
    published = schedule_iso_date(published_at)
    if not published:
        return []
    mentions: list[dict] = []
    occupied: list[tuple[int, int]] = []

    def available(start: int, end: int) -> bool:
        return not any(start < right and end > left for left, right in occupied)

    def add(start: int, end: int, value: datetime | None, raw: str, inference: str = "explicit") -> None:
        if value and available(start, end):
            mentions.append({"start": start, "end": end, "date": value, "raw": raw, "inference": inference})
            occupied.append((start, end))

    full_pattern = re.compile(
        r"(?:(?:(20\d{2})\s*년|(내년))\s*)?(\d{1,2})\s*월\s*(\d{1,2})\s*일"
    )
    for match in full_pattern.finditer(text):
        year_text, next_year_text, month_text, day_text = match.groups()
        local_context = text[max(0, match.start() - 24) : min(len(text), match.end() + 80)]
        allow_cross_year = bool(
            SCHEDULE_CROSS_YEAR_CUE.search(local_context)
            and not SCHEDULE_PAST_CUE.search(local_context)
        )
        value = resolve_schedule_month_day(
            published,
            int(month_text),
            int(day_text),
            int(year_text) if year_text else None,
            force_next_year=bool(next_year_text),
            allow_cross_year=allow_cross_year,
        )
        inference = "explicit" if year_text else "next_year" if next_year_text else "cross_year" if value and value.year > published.year else "yearless"
        add(match.start(), match.end(), value, match.group(0), inference)

    numeric_pattern = re.compile(r"(?<!\d)(20\d{2})[./-](\d{1,2})[./-](\d{1,2})(?!\d)")
    for match in numeric_pattern.finditer(text):
        add(
            match.start(),
            match.end(),
            schedule_date_value(*(int(value) for value in match.groups())),
            match.group(0),
            "explicit",
        )

    for match in re.finditer(r"(내달|다음\s*달)\s*(\d{1,2})\s*일", text):
        year, month = schedule_month_shift(published, 1)
        add(match.start(), match.end(), schedule_date_value(year, month, int(match.group(2))), match.group(0), "next_month")

    for match in re.finditer(r"오는\s*(\d{1,2})\s*일", text):
        day = int(match.group(1))
        year, month = published.year, published.month
        candidate = schedule_date_value(year, month, day)
        if candidate and candidate.date() < published.date():
            year, month = schedule_month_shift(published, 1)
            candidate = schedule_date_value(year, month, day)
        add(match.start(), match.end(), candidate, match.group(0), "relative")

    relative_days = {"오늘": 0, "내일": 1, "모레": 2}
    for word, offset in relative_days.items():
        for match in re.finditer(word, text):
            add(match.start(), match.end(), published + timedelta(days=offset), match.group(0), "relative")

    weekday_names = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
    for match in re.finditer(r"다음\s*주\s*([월화수목금토일])요일", text):
        next_monday = published + timedelta(days=(7 - published.weekday()))
        value = next_monday + timedelta(days=weekday_names[match.group(1)])
        add(match.start(), match.end(), value, match.group(0), "relative")

    mentions.sort(key=lambda item: item["start"])
    for index, mention in enumerate(mentions):
        tail = text[mention["end"] : mention["end"] + 42]
        end_match = re.search(
            r"(?:부터\s*|[~∼〜\-–—]\s*)"
            r"(?:(?:(20\d{2})\s*년\s*)?(?:(\d{1,2})\s*월\s*)?(\d{1,2})\s*일)?\s*(?:까지)?",
            tail,
        )
        if not end_match:
            continue
        year_text, month_text, day_text = end_match.groups()
        if not day_text:
            continue
        start_date = mention["date"]
        end_year = int(year_text) if year_text else start_date.year
        end_month = int(month_text) if month_text else start_date.month
        end_value = schedule_date_value(end_year, end_month, int(day_text))
        if end_value and end_value.date() < start_date.date() and not year_text:
            if month_text:
                end_year += 1
            else:
                end_year, end_month = schedule_month_shift(start_date, 1)
            end_value = schedule_date_value(end_year, end_month, int(day_text))
        # 게시일보다 앞서 시작했지만 아직 접수·행사가 끝나지 않은 기간은
        # 다음 해 일정으로 오인하지 않고 현재 진행 중인 기간으로 되돌린다.
        if (
            start_date.year > published.year
            and not re.search(r"20\d{2}\s*년|내년", str(mention.get("raw", "")))
            and not year_text
        ):
            current_start = schedule_date_value(published.year, start_date.month, start_date.day)
            current_end_year = current_start.year if current_start else published.year
            current_end_month = int(month_text) if month_text else (current_start.month if current_start else start_date.month)
            current_end = schedule_date_value(current_end_year, current_end_month, int(day_text))
            if current_start and current_end and current_end.date() < current_start.date():
                current_end_year, current_end_month = schedule_month_shift(current_start, 1)
                current_end = schedule_date_value(current_end_year, current_end_month, int(day_text))
            if current_start and current_end and current_end.date() >= published.date():
                mention["date"] = current_start
                start_date = current_start
                end_value = current_end
        mention["endDate"] = end_value
        mention["rangeEnd"] = mention["end"] + end_match.end()
        # 이미 별도 날짜로 잡힌 범위 끝은 두 번째 일정으로 만들지 않는다.
        for later in mentions[index + 1 :]:
            if later["start"] < mention["rangeEnd"]:
                later["consumedByRange"] = True
    return [item for item in mentions if not item.get("consumedByRange")]


def schedule_sentences(value: str) -> list[str]:
    text = clean_html(value, 20_000)
    parts = re.split(r"(?<=[.!?。])\s+|[\r\n]+", text)
    return [part.strip() for part in parts if 18 <= len(part.strip()) <= 900]


def schedule_event_type(text: str) -> str:
    for label, pattern in SCHEDULE_EVENT_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return label
    return "기관일정"


def schedule_event_title(sentence: str, fallback: str) -> str:
    quoted = re.findall(r"[‘“](.{4,180}?)[’”]", sentence)
    if not quoted:
        quoted = re.findall(r"['\"]([^'\"]{4,180})['\"]", sentence)
    return clean_html(quoted[-1] if quoted else fallback, 180)


def schedule_organizer(text: str, item: dict) -> str:
    for name, pattern in SCHEDULE_INSTITUTIONS:
        if re.search(pattern, text, re.IGNORECASE):
            return name
    host_match = re.search(r"([가-힣A-Za-z0-9·, ]{2,120}(?:의원실|의원모임))\s*(?:공동\s*)?주최", text)
    if host_match:
        host = re.split(r"에서는|에서|에는", host_match.group(1))[-1]
        return re.sub(r"\s+", " ", host).strip(" ,·")
    match = re.search(
        r"([가-힣A-Za-z0-9· ]{2,24}(?:부|청|위원회|거래소|공단|공사|협회|센터|연구원|연구소|진흥원))"
        r"(?:은|는|이|가|에서|와|과)",
        text,
    )
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()
    source = str(item.get("source", "")).strip()
    if item.get("sourceType") != "news" and source:
        if "기후부" in source or "기후에너지환경부" in source:
            return "기후에너지환경부"
        return re.sub(r"\s*(?:보도자료|공지사항|공지·공고)$", "", source).strip()
    return ""


def schedule_location(text: str) -> str:
    known = re.search(
        r"((?:정부)?세종청사|대한상공회의소|대한상의|한국거래소|온라인|"
        r"[가-힣A-Za-z0-9· ]{2,24}(?:회의실|컨벤션센터|센터|호텔|청사))에서",
        text,
    )
    return re.sub(r"\s+", " ", known.group(1)).strip() if known else ""


def schedule_status(text: str) -> str:
    if re.search(r"취소|철회", text):
        return "cancelled"
    if re.search(r"연기|변경", text):
        return "postponed"
    if re.search(r"필요시|조건부|잠정", text):
        return "conditional"
    return "confirmed"


def normalized_schedule_content(value: str) -> str:
    text = normalized_news_visible_text(value)
    text = re.sub(r"(?:무단\s*전재|재배포\s*금지|기자\s*[가-힣]{2,5})", " ", text)
    return re.sub(r"[^0-9a-z가-힣]+", "", text)[:1_200]


def schedule_event_tokens(value: str) -> set[str]:
    cleaned = re.sub(r"20\d{2}|\d{1,2}\s*(?:월|일|시|분)|[^0-9a-z가-힣]+", " ", str(value or "").lower())
    tokens: set[str] = set()
    for raw_token in cleaned.split():
        token = raw_token
        for particle in SCHEDULE_PARTICLES:
            if len(token) >= len(particle) + 2 and token.endswith(particle):
                token = token[: -len(particle)]
                break
        if (
            len(token) < 2
            or token in SCHEDULE_GENERIC_TOKENS
            or token in SCHEDULE_ORGANIZER_TOKENS
            or any(token.startswith(stem) for stem in SCHEDULE_BOILERPLATE_STEMS)
        ):
            continue
        tokens.add(token)
    return tokens


def schedule_product_codes(value: str) -> set[str]:
    return {
        code.upper()
        for code in re.findall(
            r"(?<![A-Za-z0-9])(?:KAU|KCU|KOC)\d{2}(?![A-Za-z0-9])",
            str(value or ""),
            re.IGNORECASE,
        )
    }


def schedule_story_score(item: dict) -> tuple[int, int, int, str, int, int]:
    direct_source = preferred_news_item_score(item)[0] if item.get("sourceType") == "news" else 1
    return (
        1 if item.get("sourceType") != "news" else 0,
        direct_source,
        1 if item.get("sourceTitle") else 0,
        str(item.get("publishedAt", "")),
        1 if item.get("startTime") else 0,
        len(str(item.get("evidence", ""))),
    )


def schedule_time_minutes(value: str) -> int | None:
    match = re.fullmatch(r"(\d{2}):(\d{2})", str(value or ""))
    return int(match.group(1)) * 60 + int(match.group(2)) if match else None


def same_institution_schedule(first: dict, second: dict) -> bool:
    first_type = str(first.get("eventType", ""))
    second_type = str(second.get("eventType", ""))
    if "국회일정" in {first_type, second_type}:
        return (
            first_type == second_type == "국회일정"
            and first.get("startDate") == second.get("startDate")
            and normalized_news_title(first.get("organizer", ""))
            == normalized_news_title(second.get("organizer", ""))
        )
    first_evidence_key = normalized_schedule_content(first.get("evidence", ""))
    second_evidence_key = normalized_schedule_content(second.get("evidence", ""))
    if min(len(first_evidence_key), len(second_evidence_key)) >= 40 and first_evidence_key == second_evidence_key:
        return True
    if first.get("startDate") != second.get("startDate"):
        return False
    if first.get("eventType") != second.get("eventType"):
        return False
    if normalized_news_title(first.get("organizer", "")) != normalized_news_title(second.get("organizer", "")):
        return False
    first_time, second_time = schedule_time_minutes(first.get("startTime", "")), schedule_time_minutes(second.get("startTime", ""))
    if first_time is not None and second_time is not None and abs(first_time - second_time) > 60:
        return False
    first_location = normalized_news_title(first.get("location", ""))
    second_location = normalized_news_title(second.get("location", ""))
    if first_location and second_location and first_location != second_location:
        cities = {"서울", "부산", "대전", "대구", "광주", "인천", "세종", "제주"}
        first_cities = {city for city in cities if city in first_location}
        second_cities = {city for city in cities if city in second_location}
        if first_cities and second_cities and first_cities.isdisjoint(second_cities):
            return False
    first_text = f"{first.get('title', '')} {first.get('evidence', '')}"
    second_text = f"{second.get('title', '')} {second.get('evidence', '')}"
    first_codes = schedule_product_codes(first_text)
    second_codes = schedule_product_codes(second_text)
    if first_codes and second_codes:
        return bool(first_codes & second_codes)
    first_evidence = normalized_news_visible_text(first.get("evidence", ""))
    second_evidence = normalized_news_visible_text(second.get("evidence", ""))
    if min(len(first_evidence), len(second_evidence)) >= 28 and first_evidence == second_evidence:
        return True
    first_tokens = schedule_event_tokens(first_text)
    second_tokens = schedule_event_tokens(second_text)
    if not first_tokens or not second_tokens:
        return False
    shared = first_tokens & second_tokens
    coverage = len(shared) / max(min(len(first_tokens), len(second_tokens)), 1)
    return len(shared) >= 2 and coverage >= 0.45


def merge_institution_schedule_group(group: list[dict]) -> dict:
    representative = dict(max(group, key=schedule_story_score))
    sources = list(dict.fromkeys(
        value
        for item in [representative, *group]
        for value in [*metadata_values(item.get("sources")), str(item.get("source", "")).strip()]
        if value
    ))
    urls = list(dict.fromkeys(
        value
        for item in [representative, *group]
        for value in [*metadata_values(item.get("sourceUrls")), str(item.get("url", "")).strip()]
        if value
    ))
    source_ids = list(dict.fromkeys(
        value
        for item in group
        for value in [*metadata_values(item.get("sourceItemIds")), str(item.get("sourceItemId", "")).strip()]
        if value
    ))
    evidence_keys: set[str] = set()
    for item in group:
        item_urls = [*metadata_values(item.get("sourceUrls")), str(item.get("url", "")).strip()]
        canonical_urls = {canonical_news_url(url) or url for url in item_urls if url}
        if canonical_urls:
            evidence_keys.update(canonical_urls)
        else:
            evidence_keys.add(
                f"{item.get('source', '')}|{item.get('publishedAt', '')}|"
                f"{normalized_news_title(item.get('title', ''))}"
            )
    previous_count = max(max(1, int(item.get("duplicateCount") or 1)) for item in group)
    representative["sources"] = sources
    representative["sourceUrls"] = urls[:8]
    representative["sourceItemIds"] = source_ids[:12]
    representative["duplicateCount"] = max(previous_count, len(evidence_keys), 1)
    representative["source"] = sources[0] if sources else str(representative.get("source", ""))
    representative["url"] = urls[0] if urls else str(representative.get("url", ""))
    if representative.get("eventType") == "국회일정":
        stable_material = "|".join([
            str(representative.get("organizer", "")),
            "국회일정",
            str(representative.get("startDate", "")),
        ])
    else:
        stable_material = "|".join([
            str(representative.get("organizer", "")),
            str(representative.get("eventType", "")),
            str(representative.get("startDate", "")),
            str(representative.get("startTime", "")),
            normalized_news_title(representative.get("location", ""))[:60],
            normalized_news_title(representative.get("title", ""))[:100],
            normalized_schedule_content(representative.get("evidence", ""))[:160],
        ])
    representative["id"] = hashlib.sha1(stable_material.encode("utf-8")).hexdigest()[:16]
    representative.pop("sourceItemId", None)
    return representative


def dedupe_institution_schedules(items: list[dict]) -> list[dict]:
    remaining = list(items)
    groups: list[list[dict]] = []
    while remaining:
        group = [remaining.pop(0)]
        changed = True
        while changed:
            changed = False
            for candidate in list(remaining):
                if any(same_institution_schedule(candidate, member) for member in group):
                    group.append(candidate)
                    remaining.remove(candidate)
                    changed = True
        groups.append(group)
    return [merge_institution_schedule_group(group) for group in groups]


def schedule_article_score(item: dict) -> tuple[int, str]:
    visible = f"{item.get('title', '')} {item.get('summary', '')}"
    score = 4 if item.get("sourceType") == "news" else 0
    score += 3 if SCHEDULE_EVENT_CUE.search(visible) else 0
    score += 3 if schedule_date_mentions(visible, str(item.get("publishedAt", ""))) else 0
    return score, str(item.get("publishedAt", ""))


def assembly_agenda_event_title(value: str, fallback: str) -> str:
    """국회 종합일정 중 배출권 관련 실제 행사명을 제목으로 사용한다."""
    text = clean_html(value, 4_000)
    fragments = re.split(r"[\r\n•●○◇◆|;/]+|\s+-\s+", text)
    candidates = [
        fragment.strip(" -–—·,:：")
        for fragment in fragments
        if SCHEDULE_MARKET_CONTEXT.search(fragment)
        and SCHEDULE_EVENT_CUE.search(fragment)
        and not SCHEDULE_NON_EVENT_CONTEXT.search(fragment)
    ]
    if not candidates:
        event_noun = r"설명회|공청회|간담회|세미나|포럼|토론회|심포지엄|컨퍼런스|협의회|회의"
        pattern = re.compile(
            rf"([가-힣A-Za-z0-9·()「」『』'\"\s,:：\-]{{4,180}}?(?:{event_noun}))",
            re.IGNORECASE,
        )
        candidates = [
            match.group(1).strip(" -–—·,:：")
            for match in pattern.finditer(text)
            if SCHEDULE_MARKET_CONTEXT.search(match.group(1))
            and not SCHEDULE_NON_EVENT_CONTEXT.search(match.group(1))
        ]
    if not candidates:
        return fallback

    title = min(candidates, key=lambda item: (len(item), item))
    comma_parts = [part.strip() for part in re.split(r"[,，]", title) if part.strip()]
    focused = [
        part for part in comma_parts
        if SCHEDULE_MARKET_CONTEXT.search(part) and SCHEDULE_EVENT_CUE.search(part)
    ]
    if focused:
        title = min(focused, key=len)
    title = re.sub(r"^\s*\d{1,2}(?::\d{2})?\s*", "", title)
    title = re.sub(
        r"^[가-힣A-Za-z0-9· ]{2,100}(?:의원실|위원회|국회의원)\s*(?:등|주최|공동주최)?\s*",
        "",
        title,
    )
    return clean_html(title, 180) or fallback


def is_routable_assembly_agenda_article(item: dict) -> bool:
    return bool(
        is_assembly_agenda_article(item)
        and assembly_agenda_event_title(str(item.get("summary", "")), "")
    )


def extract_assembly_agenda_schedule(item: dict, article_text: str = "") -> list[dict]:
    """같은 날짜의 국회 종합일정 보도를 대표기사 한 건으로 만든다."""
    published_at = str(item.get("publishedAt", ""))
    published = schedule_iso_date(published_at)
    if not published:
        return []

    source_title = clean_html(str(item.get("title", "")), 180) or "오늘의 국회일정"
    mentions = schedule_date_mentions(source_title, published_at)
    inference_order = {
        "explicit": 0,
        "yearless": 1,
        "next_year": 2,
        "next_month": 3,
        "cross_year": 4,
        "relative": 5,
    }
    mention = min(
        mentions,
        key=lambda value: (inference_order.get(str(value.get("inference", "")), 9), int(value.get("start", 0))),
    ) if mentions else None
    agenda_date = mention.get("date") if mention else published
    if not isinstance(agenda_date, datetime):
        agenda_date = published
    start_date = agenda_date.date().isoformat()

    source_url = str(item.get("url", ""))
    source_item_id = str(item.get("id", ""))
    evidence = clean_html(str(item.get("summary", "")), 360) or source_title
    event_title = assembly_agenda_event_title(f"{item.get('summary', '')} {article_text}", "")
    if not event_title:
        return []
    content_signature = normalized_schedule_content(article_text or f"{source_title} {evidence}")
    content_fingerprint = hashlib.sha1(content_signature.encode("utf-8")).hexdigest() if content_signature else ""
    sources = list(dict.fromkeys([
        str(item.get("source", "")).strip(),
        *metadata_values(item.get("duplicateSources")),
    ]))
    sources = [value for value in sources if value]
    event_key = f"대한민국 국회|국회일정|{start_date}"
    return [{
        "id": hashlib.sha1(event_key.encode("utf-8")).hexdigest()[:16],
        "title": event_title,
        "sourceTitle": source_title,
        "eventType": "국회일정",
        "startDate": start_date,
        "endDate": start_date,
        "dateInference": str(mention.get("inference", "published")) if mention else "published",
        "startTime": "",
        "timezone": "Asia/Seoul",
        "organizer": "대한민국 국회",
        "location": "",
        "status": "confirmed",
        "evidence": evidence,
        "publishedAt": published_at,
        "source": str(item.get("source", "")),
        "sourceType": "news",
        "url": source_url,
        "sourceItemId": source_item_id,
        "sourceItemIds": [source_item_id] if source_item_id else [],
        "sourceUrls": [source_url] if source_url else [],
        "sources": sources,
        "duplicateCount": max(1, int(item.get("duplicateCount") or 1)),
        "contentFingerprint": content_fingerprint,
        "contentSignature": content_signature,
    }]


def extract_institution_schedules(item: dict, article_text: str = "") -> list[dict]:
    published_at = str(item.get("publishedAt", ""))
    published = schedule_iso_date(published_at)
    if not published:
        return []
    if is_assembly_agenda_article(item):
        return extract_assembly_agenda_schedule(item, article_text)
    visible = f"{item.get('title', '')}. {item.get('summary', '')}"
    full_text = article_text or visible
    content_signature = normalized_schedule_content(full_text)
    content_fingerprint = hashlib.sha1(content_signature.encode("utf-8")).hexdigest() if content_signature else ""
    source_url = str(item.get("url", ""))
    source_item_id = str(item.get("id", ""))
    schedules: list[dict] = []
    seen: set[str] = set()
    for sentence in schedule_sentences(full_text):
        if not SCHEDULE_EVENT_CUE.search(sentence):
            continue
        if not SCHEDULE_MARKET_CONTEXT.search(sentence):
            continue
        if SCHEDULE_NON_EVENT_CONTEXT.search(sentence):
            continue
        mentions = schedule_date_mentions(sentence, published_at)
        if not mentions:
            continue
        organizer = schedule_organizer(f"{sentence} {item.get('title', '')} {item.get('summary', '')}", item)
        if not organizer:
            continue
        event_type = schedule_event_type(sentence)
        for mention in mentions:
            event_date = mention["date"]
            end_value = mention.get("endDate")
            effective_end = end_value if isinstance(end_value, datetime) else event_date
            before = sentence[max(0, int(mention["start"]) - 18) : int(mention["start"])]
            if SCHEDULE_PAST_CUE.search(before):
                continue
            if effective_end.date() < published.date():
                continue
            if SCHEDULE_PAST_CUE.search(sentence) and not SCHEDULE_FUTURE_CUE.search(sentence):
                continue
            if event_date.date() > (published + timedelta(days=SCHEDULE_HORIZON_DAYS)).date():
                continue
            start_date = event_date.date().isoformat()
            end_date = end_value.date().isoformat() if isinstance(end_value, datetime) else start_date
            start_time = schedule_time_value(sentence)
            location = schedule_location(sentence)
            source_title = clean_html(str(item.get("title", "")), 180)
            title = schedule_event_title(sentence, source_title or sentence)
            evidence = clean_html(sentence, 360)
            event_key = "|".join([
                organizer,
                event_type,
                start_date,
                end_date,
                start_time,
                normalized_news_title(evidence)[:120],
            ])
            if event_key in seen:
                continue
            seen.add(event_key)
            schedules.append({
                "id": hashlib.sha1(event_key.encode("utf-8")).hexdigest()[:16],
                "title": title,
                "sourceTitle": source_title,
                "eventType": event_type,
                "startDate": start_date,
                "endDate": end_date,
                "dateInference": str(mention.get("inference", "explicit")),
                "startTime": start_time,
                "timezone": "Asia/Seoul",
                "organizer": organizer,
                "location": location,
                "status": schedule_status(sentence),
                "evidence": evidence,
                "publishedAt": published_at,
                "source": str(item.get("source", "")),
                "sourceType": str(item.get("sourceType", "official")),
                "url": source_url,
                "sourceItemId": source_item_id,
                "sourceItemIds": [source_item_id] if source_item_id else [],
                "sourceUrls": [source_url] if source_url else [],
                "sources": [str(item.get("source", ""))] if item.get("source") else [],
                "duplicateCount": max(1, int(item.get("duplicateCount") or 1)),
                "contentFingerprint": content_fingerprint,
                "contentSignature": content_signature,
            })
    return schedules


def valid_existing_schedule_year(item: dict) -> bool:
    """과거 수집기가 연도 없는 날짜를 다음 해로 넘긴 잘못된 일정을 제거한다."""
    published = schedule_iso_date(item.get("publishedAt"))
    start = schedule_iso_date(item.get("startDate"))
    if not published or not start:
        return False
    if start.year <= published.year:
        return True

    evidence = f"{item.get('evidence', '')} {item.get('title', '')}"
    if re.search(rf"(?:{start.year}\s*년|{start.year}[./-]\d)|내년|다음\s*해", evidence):
        return True
    if str(item.get("dateInference", "")) in {"next_year", "explicit"}:
        return True
    return bool(
        published.month >= 11
        and start.month <= 2
        and SCHEDULE_CROSS_YEAR_CUE.search(evidence)
        and not SCHEDULE_PAST_CUE.search(evidence)
    )


def build_institution_schedules(source_items: list[dict], existing: list[dict]) -> list[dict]:
    ordered = sorted(source_items, key=schedule_article_score, reverse=True)
    recent_cutoff = (datetime.now(KST).date() - timedelta(days=90)).isoformat()
    body_candidates = [
        item
        for item in ordered
        if str(item.get("publishedAt", "")) >= recent_cutoff
        and not is_assembly_agenda_article(item)
        and re.match(r"^https?://", str(item.get("url", "")), re.IGNORECASE)
    ][:SCHEDULE_BODY_VERIFY_LIMIT]
    body_urls = {str(item.get("url", "")) for item in body_candidates}

    def fetch_for_item(item: dict) -> tuple[str, str]:
        url = str(item.get("url", ""))
        return url, fetch_article_text(url)

    with ThreadPoolExecutor(max_workers=min(6, len(body_candidates) or 1)) as executor:
        body_map = dict(executor.map(fetch_for_item, body_candidates)) if body_candidates else {}

    extracted: list[dict] = []
    for item in source_items:
        url = str(item.get("url", ""))
        extracted.extend(extract_institution_schedules(item, body_map.get(url, "") if url in body_urls else ""))

    valid_existing = [
        item
        for item in existing
        if isinstance(item, dict)
        and valid_existing_schedule_year(item)
        and (
            item.get("eventType") == "국회일정"
            or (
                SCHEDULE_MARKET_CONTEXT.search(str(item.get("evidence") or item.get("title") or ""))
                and not SCHEDULE_NON_EVENT_CONTEXT.search(str(item.get("evidence") or item.get("title") or ""))
            )
        )
    ]
    combined = valid_existing + extracted
    deduped = dedupe_institution_schedules(combined)
    today = datetime.now(KST).date()
    minimum = today - timedelta(days=SCHEDULE_RETENTION_DAYS)
    maximum = today + timedelta(days=SCHEDULE_HORIZON_DAYS)
    retained = []
    for item in deduped:
        start = schedule_iso_date(item.get("startDate"))
        if not start or not (minimum <= start.date() <= maximum):
            continue
        retained.append(item)

    def order_key(item: dict) -> tuple[int, int, str]:
        value = schedule_iso_date(item.get("startDate"))
        ordinal = value.date().toordinal() if value else 0
        return (0, ordinal, str(item.get("startTime", ""))) if value and value.date() >= today else (1, -ordinal, str(item.get("startTime", "")))

    return sorted(retained, key=order_key)[:MAX_SCHEDULE_ITEMS]


def match_title_or_body(item: dict, keywords: list[str]) -> dict | None:
    # 검색 RSS가 이미 제목+본문 검색 결과로 돌려준 항목은 상세 페이지를
    # 다시 열지 않는다. 이중 본문 조회가 Actions 10분 초과의 주원인이었다.
    if item.get("_trustedSearchMatch"):
        return item

    feed_summary = clean_html(item.get("summary", ""), 20_000)
    feed_text = f"{item.get('title', '')} {feed_summary}".lower()
    matched = [keyword for keyword in keywords if keyword.lower() in feed_text]
    if matched:
        item["matchedKeywords"] = matched
        # 본문에서만 검색된 자료도 이후 병합 단계에서 사라지지 않도록
        # 키워드가 실제로 포함된 문맥을 요약문으로 보존한다.
        item["summary"] = keyword_excerpt(feed_summary, matched)
        return item

    article_text = fetch_article_text(item.get("url", ""))
    article_lower = article_text.lower()
    matched = [keyword for keyword in keywords if keyword.lower() in article_lower]
    if not matched:
        return None
    item["matchedKeywords"] = matched
    item["category"] = category_for(f"{item.get('title', '')} {article_text}".lower())
    item["summary"] = keyword_excerpt(article_text, matched)
    return item


def filter_title_and_body(items: list[dict], keywords: list[str]) -> list[dict]:
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=min(6, len(items))) as executor:
        checked = list(executor.map(lambda item: match_title_or_body(item, keywords), items))
    return [item for item in checked if item]


def normalize_date(value: str) -> str:
    value = (value or "").strip()
    try:
        parsed = parsedate_to_datetime(value.replace(" KST ", " +0900 "))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=KST)
        return parsed.astimezone(KST).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        pass
    match = re.search(r"(20\d{2})[./-]?(\d{1,2})[./-]?(\d{1,2})", value)
    if not match:
        return datetime.now(KST).date().isoformat()
    year, month, day = map(int, match.groups())
    return f"{year:04d}-{month:02d}-{day:02d}"


def child_text(item: ET.Element, name: str) -> str:
    node = item.find(name)
    if node is not None and node.text:
        return node.text.strip()
    for child in item:
        if child.tag.rsplit("}", 1)[-1].lower() == name.lower() and child.text:
            return child.text.strip()
    return ""


NEWS_DOMESTIC_MARKET_HARD = re.compile(
    r"(?<![A-Za-z0-9])K[- ]?ETS(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])(?:KAU|KCU)\d{2}(?![A-Za-z0-9])|(?<![A-Za-z0-9])KOC(?![A-Za-z0-9])|"
    r"(?:한국|국내).{0,18}(?:배출권거래제|배출권시장|탄소시장)|"
    r"(?:한국거래소|(?<![A-Za-z0-9])KRX(?![A-Za-z0-9])).{0,24}배출권|"
    r"배출권.{0,24}(?:한국거래소|(?<![A-Za-z0-9])KRX(?![A-Za-z0-9]))",
    re.IGNORECASE,
)
NEWS_OVERSEAS_MARKET_HARD = re.compile(
    r"(?<![A-Za-z0-9])(?:EU|UK|NZ)[ -]?ETS(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])(?:EUA|UKA|NZU|RGGI|WCI|CCER|CEA)(?![A-Za-z0-9])|"
    r"(?:유럽연합|유럽|(?<![A-Za-z0-9])EU(?![A-Za-z0-9])|중국|영국|일본|베트남|뉴질랜드|호주|캘리포니아|미국|캐나다|대만).{0,36}"
    r"(?:배출권거래제|탄소\s*시장|탄소\s*거래소|탄소\s*배출권|배출권(?:\s*(?:가격|거래|경매|할당))?)|"
    r"(?:배출권거래제|탄소\s*시장|탄소\s*거래소|탄소\s*배출권|배출권(?:\s*(?:가격|거래|경매|할당))?).{0,36}"
    r"(?:유럽연합|유럽|(?<![A-Za-z0-9])EU(?![A-Za-z0-9])|중국|영국|일본|베트남|뉴질랜드|호주|캘리포니아|미국|캐나다|대만)",
    re.IGNORECASE,
)
NEWS_DOMESTIC_RESPONSE = re.compile(
    r"(?:CBAM|탄소국경조정|EU\s*규제|EU[ -]?ETS).{0,45}"
    r"(?:대응|대비|수출|통상|설명회|지원|준비|가이드|인증|애로|경쟁력|국내\s*기업|우리\s*기업)|"
    r"(?:대응|대비|수출|통상|설명회|지원|준비|가이드|인증|애로|경쟁력|국내\s*기업|우리\s*기업).{0,45}"
    r"(?:CBAM|탄소국경조정|EU\s*규제|EU[ -]?ETS)",
    re.IGNORECASE,
)
NEWS_FOREIGN_SUBJECT_ACTION = re.compile(
    r"^[^가-힣A-Za-z0-9]{0,12}(?:(?<![A-Za-z0-9])EU(?![A-Za-z0-9])|유럽연합|유럽|중국|영국|일본|베트남|뉴질랜드|호주|캘리포니아|미국|캐나다|대만|독일|프랑스|日|中|美)"
    r"(?:[,·:\s]|은|는|이|가).{0,90}(?:시행|발효|도입|개편|강화|경매|할당|거래|가격|확대|폐지)",
    re.IGNORECASE,
)
NEWS_FOREIGN_SOURCE = re.compile(
    r"Reuters|Bloomberg|Carbon\s*Pulse|S&P\s*Global|Argus|Euractiv|Financial\s*Times|"
    r"The\s*Guardian|CNBC|BBC|Xinhua|신화망|Vietnam\.vn|VnExpress|Nikkei|Japan\s*Times|"
    r"China\s*Daily|European\s*Commission|EU\s*Commission|ICAP|World\s*Bank",
    re.IGNORECASE,
)
NEWS_GENERIC_FOREIGN = re.compile(
    r"유럽연합|유럽|(?<![A-Za-z0-9])EU(?![A-Za-z0-9])|중국|영국|일본|베트남|뉴질랜드|호주|캘리포니아|미국|캐나다|대만|독일|프랑스|日|中|美",
    re.IGNORECASE,
)


def normalized_region_text(value: object) -> str:
    return re.sub(
        r"\s+",
        " ",
        unicodedata.normalize("NFKC", html.unescape(clean_html(str(value or ""), 4_000))),
    ).strip()


def news_region_for(item: dict) -> str:
    """기사의 발행국이 아니라 주된 배출권 시장이 국내인지 해외인지 판정한다."""
    title = normalized_region_text(item.get("title", ""))
    summary = normalized_region_text(item.get("summary", ""))
    source = normalized_region_text(item.get("source", ""))

    if NEWS_DOMESTIC_MARKET_HARD.search(title) or NEWS_DOMESTIC_RESPONSE.search(title):
        return "국내"
    if NEWS_OVERSEAS_MARKET_HARD.search(title):
        return "해외"

    domestic_score = 0
    foreign_score = 0
    domestic_score += 12 if NEWS_DOMESTIC_MARKET_HARD.search(title) else 0
    domestic_score += 7 if NEWS_DOMESTIC_MARKET_HARD.search(summary) else 0
    domestic_score += 10 if NEWS_DOMESTIC_RESPONSE.search(title) else 0
    domestic_score += 5 if NEWS_DOMESTIC_RESPONSE.search(summary) else 0
    foreign_score += 12 if NEWS_OVERSEAS_MARKET_HARD.search(title) else 0
    foreign_score += 7 if NEWS_OVERSEAS_MARKET_HARD.search(summary) else 0
    foreign_score += 8 if NEWS_FOREIGN_SUBJECT_ACTION.search(title) else 0
    foreign_score += 3 if NEWS_GENERIC_FOREIGN.search(title) else 0
    foreign_score += 1 if NEWS_GENERIC_FOREIGN.search(summary) else 0
    foreign_source = bool(NEWS_FOREIGN_SOURCE.search(source))
    foreign_score += 2 if foreign_source else 0
    if foreign_source and re.search(r"탄소\s*배출권|배출권거래제|탄소\s*시장|탄소\s*거래소", title, re.IGNORECASE):
        foreign_score += 6
    domestic_score += 1 if re.search(r"(?:\.kr|\.co\.kr|뉴스|신문|일보|경제|미디어)$", source, re.IGNORECASE) else 0
    return "해외" if foreign_score >= 8 and foreign_score >= domestic_score + 3 else "국내"


def category_for(text: str) -> str:
    if re.search(
        r"유상\s*(?:할당\s*)?경매|배출권.{0,20}(?:입찰|경매)|(?:입찰|경매).{0,20}배출권|"
        r"(?:EU|UK|NZ)[ -]?ETS.{0,20}경매|경매.{0,20}(?:EU|UK|NZ)[ -]?ETS|응찰|낙찰|유찰",
        text,
        re.IGNORECASE,
    ):
        return "유상경매"
    if re.search(r"시장안정|예비분|K-MSR|공급\s*조정|추가\s*공급", text, re.IGNORECASE):
        return "시장안정"
    if re.search(r"상쇄|외부사업|KOC|KCU|감축실적|방법론", text, re.IGNORECASE):
        return "상쇄·외부사업"
    if re.search(r"(?:KAU|EUA|UKA|NZU)\d*|종가|거래량|가격|시황|강세|약세|급등|급락", text, re.IGNORECASE):
        return "시장·가격"
    return "제도"


def parse_rss(payload: bytes) -> ET.Element:
    xml_text = payload.decode("utf-8", errors="replace")
    xml_text = re.sub(
        r"(<link>)(.*?)(</link>)",
        lambda match: match.group(1)
        + re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)", "&amp;", match.group(2))
        + match.group(3),
        xml_text,
        flags=re.DOTALL,
    )
    return ET.fromstring(xml_text)


def fetch_rss(url: str) -> ET.Element:
    """기후부의 www/bare 호스트 중 응답하는 주소를 사용한다."""
    candidates = [url]
    if "https://www.mcee.go.kr/" in url:
        candidates.append(url.replace("https://www.mcee.go.kr/", "https://mcee.go.kr/", 1))
    elif "https://mcee.go.kr/" in url:
        candidates.append(url.replace("https://mcee.go.kr/", "https://www.mcee.go.kr/", 1))

    errors: list[str] = []
    for candidate in candidates:
        request = urllib.request.Request(
            candidate,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; ETS-SIGNAL/2.0; +https://ebrain725.github.io)",
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
                "Connection": "close",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=RSS_TIMEOUT_SECONDS) as response:
                return parse_rss(response.read())
        except Exception as exc:
            errors.append(f"{urllib.parse.urlsplit(candidate).netloc}: {exc}")
    raise RuntimeError(" / ".join(errors))


def rss_search_url(url: str, keyword: str, max_items: int) -> str:
    """기후부 RSS에 제목+본문 검색 조건을 붙인다."""
    parsed = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    query.update(
        {
            "searchKey": "titleOrContent",
            "searchValue": keyword,
            "maxPageItems": str(max_items),
            "pagerOffset": "0",
        }
    )
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment)
    )


def source_candidates(root: ET.Element, source: dict, matched_keyword: str) -> list[dict]:
    candidates: list[dict] = []
    for item in root.findall(".//item"):
        title = clean_html(child_text(item, "title"), 180)
        description = clean_html(child_text(item, "description"), 2_000)
        link = re.sub(r";jsessionid=[^?]+", "", html.unescape(child_text(item, "link")))
        published = normalize_date(child_text(item, "pubDate") or child_text(item, "date"))
        haystack = f"{title} {description}".lower()
        stable_key = link or f"{published}|{title}"
        candidates.append(
            {
                "id": hashlib.sha1(stable_key.encode("utf-8")).hexdigest()[:16],
                "publishedAt": published,
                "title": title,
                "category": category_for(haystack),
                "summary": description,
                "source": source["name"],
                "sourceType": "official",
                "section": source_section(source),
                "url": link,
                "matchedKeywords": [matched_keyword],
                "_trustedSearchMatch": bool(matched_keyword),
            }
        )
    return candidates


def fetch_source(source: dict, keywords: list[str]) -> list[dict]:
    # 검색어 없는 RSS는 최신 게시물 일부만 내려준다. 관리자 키워드마다
    # 기후부의 제목+본문 검색을 실행해야 과거 공식자료까지 놓치지 않는다.
    per_keyword = max(10, min(int(source.get("maxItemsPerKeyword", 30)), 50))
    candidates_by_key: dict[str, dict] = {}
    keyword_errors: list[str] = []

    def fetch_keyword(keyword: str) -> tuple[str, list[dict] | None, str | None]:
        try:
            root = fetch_rss(rss_search_url(source["url"], keyword, per_keyword))
        except Exception as exc:
            return keyword, None, str(exc)
        return keyword, source_candidates(root, source, keyword), None

    # 키워드 요청을 제한적으로 병렬 실행해 GitHub Actions의 전체 지연을 줄인다.
    with ThreadPoolExecutor(max_workers=min(3, len(keywords))) as executor:
        keyword_results = list(executor.map(fetch_keyword, keywords))

    for keyword, candidates, error in keyword_results:
        if error:
            keyword_errors.append(f"{keyword}: {error}")
            continue
        for candidate in candidates or []:
            key = candidate.get("url") or f"{candidate.get('publishedAt')}|{candidate.get('title')}"
            if key in candidates_by_key:
                matched = candidates_by_key[key].setdefault("matchedKeywords", [])
                if keyword not in matched:
                    matched.append(keyword)
            else:
                candidates_by_key[key] = candidate

    # 검색형 RSS가 일시적으로 막히면 기본 RSS의 최신 자료를 제목·본문으로 재검사한다.
    if not candidates_by_key and keyword_errors:
        try:
            root = fetch_rss(source["url"])
            fallback_candidates = source_candidates(root, source, "")
            for candidate in fallback_candidates:
                candidate["matchedKeywords"] = []
            recovered = filter_title_and_body(fallback_candidates, keywords)
            for candidate in recovered:
                key = candidate.get("url") or f"{candidate.get('publishedAt')}|{candidate.get('title')}"
                candidates_by_key[key] = candidate
        except Exception as exc:
            keyword_errors.append(f"기본 RSS 복구: {exc}")

    if not candidates_by_key and keyword_errors:
        raise RuntimeError("키워드별 공식검색과 기본 RSS 복구에 모두 실패했습니다: " + " | ".join(keyword_errors))
    if keyword_errors:
        print(
            f"{source.get('name', '기후부 공식자료')} 일부 키워드 검색 경고: " + " | ".join(keyword_errors),
            file=sys.stderr,
        )

    candidates = sorted(
        candidates_by_key.values(),
        key=lambda item: (item.get("publishedAt", ""), item.get("title", "")),
        reverse=True,
    )
    return filter_title_and_body(candidates, keywords)


def fetch_krx_board_html(path: str, values: dict[str, object]) -> str:
    """한국거래소 배출권시장 게시판의 공개 목록·상세 HTML 조각을 받는다."""
    request = urllib.request.Request(
        f"https://ets.krx.co.kr/board/ETS01030000/{path}",
        data=urllib.parse.urlencode(values).encode("utf-8"),
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; ETS-SIGNAL/2.0; +https://ebrain725.github.io)",
            "Accept": "text/html, */*; q=0.1",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": "https://ets.krx.co.kr/board/ETS01030000/bbs",
            "X-Requested-With": "XMLHttpRequest",
            "Connection": "close",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=KRX_NOTICE_TIMEOUT_SECONDS) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read(2_000_000).decode(charset, errors="replace")


def krx_notice_rows(payload: str) -> list[dict]:
    """상단 고정 공지를 제외하고 KRX 일반 목록(tbody.datalist)만 해석한다."""
    datalist = ""
    for attributes, body in re.findall(r"<tbody\b([^>]*)>([\s\S]*?)</tbody>", payload, re.IGNORECASE):
        class_match = re.search(r"\bclass\s*=\s*(['\"])(.*?)\1", attributes, re.IGNORECASE | re.DOTALL)
        classes = {value.lower() for value in class_match.group(2).split()} if class_match else set()
        if "datalist" in classes:
            datalist = body
            break
    if not datalist:
        return []

    rows: list[dict] = []
    for row_html in re.findall(r"<tr\b[^>]*>([\s\S]*?)</tr>", datalist, re.IGNORECASE):
        link_match = re.search(
            r"<a\b([^>]*\bdata-view\s*=\s*(['\"])(\d+)\2[^>]*)>([\s\S]*?)</a>",
            row_html,
            re.IGNORECASE,
        )
        date_match = None
        for cell_html in re.findall(r"<td\b[^>]*>([\s\S]*?)</td>", row_html, re.IGNORECASE):
            candidate = clean_html(cell_html, 100)
            exact_date = re.fullmatch(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})", candidate)
            if exact_date:
                date_match = exact_date
        if not link_match or not date_match:
            continue
        source_id = link_match.group(3)
        title = clean_html(link_match.group(4), 300)
        if not title:
            continue
        year, month, day = map(int, date_match.groups())
        try:
            published_at = datetime(year, month, day).date().isoformat()
        except ValueError:
            continue
        rows.append(
            {
                "sourceId": source_id,
                "publishedAt": published_at,
                "title": title,
            }
        )
    return rows


def krx_notice_summary(source_id: str) -> str:
    detail_html = fetch_krx_board_html("view", {"bbsSeq": source_id})
    for attributes, body in re.findall(
        r"<textarea\b([^>]*)>([\s\S]*?)</textarea>",
        detail_html,
        re.IGNORECASE,
    ):
        if re.search(r"\bname\s*=\s*(['\"])contn\1", attributes, re.IGNORECASE):
            # KRX 응답은 본문 HTML 자체가 이스케이프돼 있어 한 번 먼저 복원한다.
            summary = clean_html(html.unescape(body), 1_200)
            if summary:
                return summary
            break
    raise RuntimeError("공지 상세 본문을 해석하지 못했습니다.")


def fetch_krx_notices(source: dict, keywords: list[str]) -> list[dict]:
    """한국거래소 배출권시장 공지 최신글을 상세 본문과 함께 수집한다."""
    max_items = max(1, min(int(source.get("maxItems", 20)), 50))
    pages = max(1, (max_items + 9) // 10)
    rows_by_id: dict[str, dict] = {}
    page_errors: list[str] = []
    for page in range(1, pages + 1):
        try:
            payload = fetch_krx_board_html(
                "list",
                {
                    "bbsId": "OPN03010000T8",
                    "bbsUrl": "ETS01030000",
                    "curPage": page,
                    "searchType": "",
                    "bbsSeq": "",
                    "boardStyle": "normal",
                    "language": "ko",
                    "srchTitle": "",
                    "srchWord": "",
                    "srchWord1": "",
                },
            )
            page_rows = krx_notice_rows(payload)
            if not page_rows:
                if page == 1:
                    raise RuntimeError("첫 페이지의 일반 공지 목록을 해석하지 못했습니다.")
                page_errors.append(f"{page}페이지: 일반 공지 목록이 비어 있습니다.")
                break
            for row in page_rows:
                rows_by_id.setdefault(row["sourceId"], row)
        except Exception as exc:
            page_errors.append(f"{page}페이지: {exc}")
            if page == 1:
                break

    rows = sorted(
        rows_by_id.values(),
        key=lambda item: (item.get("publishedAt", ""), int(item.get("sourceId", 0))),
        reverse=True,
    )[:max_items]
    if not rows:
        message = "한국거래소 공지 목록을 해석하지 못했습니다."
        if page_errors:
            message += " " + " | ".join(page_errors)
        raise RuntimeError(message)
    if page_errors:
        print("한국거래소 공지 목록 일부 경고: " + " | ".join(page_errors), file=sys.stderr)

    def enrich(row: dict) -> tuple[dict, str, str | None]:
        try:
            return row, krx_notice_summary(str(row["sourceId"])), None
        except Exception as exc:
            return row, "", f"{row['sourceId']}: {exc}"

    with ThreadPoolExecutor(max_workers=min(4, len(rows))) as executor:
        details = list(executor.map(enrich, rows))

    board_url = str(source.get("url") or "https://ets.krx.co.kr/board/ETS01030000/bbs").split("#", 1)[0]
    items: list[dict] = []
    detail_errors: list[str] = []
    for row, detail, detail_error in details:
        if detail_error:
            detail_errors.append(detail_error)
            summary = "한국거래소 배출권시장 공지사항입니다. 원문에서 세부 내용을 확인하세요."
        else:
            summary = str(detail or "한국거래소 배출권시장 공지사항입니다. 원문에서 세부 내용을 확인하세요.")
        material = f"{row['title']} {summary}"
        matched = [keyword for keyword in keywords if keyword.lower() in material.lower()]
        items.append(
            {
                "id": f"krx-ets-{row['sourceId']}",
                "sourceId": row["sourceId"],
                "publishedAt": row["publishedAt"],
                "title": row["title"],
                "category": category_for(material),
                "summary": summary,
                "source": source.get("name", "한국거래소 배출권 공지사항"),
                "sourceType": "official",
                "section": "krx_notice",
                "url": f"{board_url}#view={row['sourceId']}",
                "matchedKeywords": matched,
                "_trustedSearchMatch": True,
                "_detailFetchFailed": bool(detail_error),
            }
        )
    if detail_errors:
        shown_errors = detail_errors[:5]
        remaining = len(detail_errors) - len(shown_errors)
        suffix = f" | 외 {remaining}건" if remaining else ""
        print("한국거래소 공지 본문 일부 경고: " + " | ".join(shown_errors) + suffix, file=sys.stderr)
    return items


def fetch_google_news(search: dict, keywords: list[str], lookback_days: int) -> list[dict]:
    keyword_query = " OR ".join(f'"{keyword.replace(chr(34), "")}"' for keyword in keywords)
    params = urllib.parse.urlencode(
        {
            "q": f"({keyword_query}) when:{lookback_days}d",
            "hl": "ko",
            "gl": "KR",
            "ceid": "KR:ko",
        }
    )
    root = fetch_rss(f"https://news.google.com/rss/search?{params}")
    candidates = []
    for item in root.findall(".//item"):
        raw_title = clean_html(child_text(item, "title"), 180)
        title = re.sub(r"(?:\s+-\s+[^-]+)+$", "", raw_title).strip() or raw_title
        description = clean_html(child_text(item, "description"), 2_000)
        haystack = f"{title} {description}".lower()
        link = html.unescape(child_text(item, "link"))
        published = normalize_date(child_text(item, "pubDate") or child_text(item, "date"))
        source_name = child_text(item, "source") or search.get("name", "Google News")
        stable_key = link or f"{published}|{title}"
        candidates.append(
            {
                "id": hashlib.sha1(stable_key.encode("utf-8")).hexdigest()[:16],
                "publishedAt": published,
                "title": title,
                "category": category_for(haystack),
                "summary": description,
                "source": source_name,
                "sourceType": "news",
                "url": link,
                "searchProvider": "Google News RSS",
                "matchedKeywords": [keyword for keyword in keywords if keyword.lower() in haystack],
                "_trustedSearchMatch": True,
            }
        )
    return filter_title_and_body(candidates[:80], keywords)


def normalize_naver_pub_date(value: str) -> str:
    """NAVER의 RFC 2822 날짜만 허용해 잘못된 기사가 오늘 기사로 둔갑하지 않게 한다."""
    try:
        parsed = parsedate_to_datetime(str(value or "").strip())
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=KST)
        return parsed.astimezone(KST).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


def parse_naver_news_payload(
    payload: bytes | str | dict,
    *,
    query_keyword: str,
    keywords: list[str],
    cutoff_date: str,
) -> list[dict]:
    """NAVER API HUB JSON을 기존 뉴스 항목 스키마로 변환한다.

    네트워크·환경변수에 의존하지 않는 순수 파서라 고정 fixture로 검증할 수 있다.
    """
    if isinstance(payload, bytes):
        document = json.loads(payload.decode("utf-8"))
    elif isinstance(payload, str):
        document = json.loads(payload)
    else:
        document = payload
    if not isinstance(document, dict) or not isinstance(document.get("items"), list):
        raise ValueError("NAVER 뉴스 응답에 items 배열이 없습니다.")

    candidates: list[dict] = []
    for raw_item in document["items"]:
        if not isinstance(raw_item, dict):
            continue
        title = clean_html(str(raw_item.get("title", "")), 180)
        description = clean_html(str(raw_item.get("description", "")), 2_000)
        original_link = html.unescape(str(raw_item.get("originallink", ""))).strip()
        naver_link = html.unescape(str(raw_item.get("link", ""))).strip()
        link = next(
            (
                candidate
                for candidate in (original_link, naver_link)
                if re.match(r"^https?://", candidate, re.IGNORECASE)
            ),
            "",
        )
        if not title or not link:
            continue
        published = normalize_naver_pub_date(str(raw_item.get("pubDate", "")))
        if not published:
            continue
        if cutoff_date and published < cutoff_date:
            continue
        haystack = f"{title} {description}".lower()
        matched = news_field_phrase_matches(title, description, keywords)
        body_score = naver_body_candidate_score(haystack)
        if not matched and body_score < 2:
            # NAVER는 "탄소시장"을 "탄소"와 "시장"으로 분리해 전혀 다른
            # 기사를 반환할 수 있다. 검색됐다는 사실만으로는 저장하지 않는다.
            continue
        stable_key = canonical_news_url(link) or f"{published}|{title}"
        candidate = {
            "id": hashlib.sha1(stable_key.encode("utf-8")).hexdigest()[:16],
            "publishedAt": published,
            "title": title,
            "category": category_for(haystack),
            "summary": description,
            "source": publisher_name_from_url(original_link or naver_link),
            "sourceType": "news",
            "section": "news",
            "url": link,
            "searchProvider": "NAVER API HUB",
            "matchedKeywords": matched,
        }
        if matched:
            candidate["_trustedSearchMatch"] = True
        elif query_keyword:
            candidate["_bodyVerificationQueries"] = [query_keyword]
            candidate["_bodyCandidateScore"] = body_score
        candidates.append(candidate)
    return candidates


def verify_naver_news_candidates(items: list[dict], keywords: list[str]) -> list[dict]:
    """제목·요약 불일치 후보 중 관련성이 높은 소수만 본문에서 재검증한다."""
    verified = [dict(item) for item in items if item.get("_trustedSearchMatch")]
    pending = [dict(item) for item in items if not item.get("_trustedSearchMatch")]
    pending.sort(
        key=lambda item: (
            int(item.get("_bodyCandidateScore") or 0),
            item.get("publishedAt", ""),
            item.get("title", ""),
        ),
        reverse=True,
    )
    for item in pending[:NAVER_BODY_VERIFY_LIMIT]:
        article_text = fetch_article_text(item.get("url", ""))
        matched = news_phrase_matches(article_text, keywords)
        if not matched:
            continue
        item["matchedKeywords"] = matched
        item["category"] = category_for(f"{item.get('title', '')} {article_text}".lower())
        item["summary"] = keyword_excerpt(article_text, matched)
        item["_trustedSearchMatch"] = True
        verified.append(item)
    return verified


def fetch_naver_news(
    keywords: list[str],
    lookback_days: int,
    client_id: str,
    client_secret: str,
) -> list[dict]:
    """NAVER 뉴스 검색을 키워드별로 실행하고 부분 실패는 나머지 결과로 복구한다."""
    if not client_id or not client_secret:
        raise RuntimeError("NAVER API HUB Client ID와 Client Secret이 모두 필요합니다.")
    if not keywords:
        return []
    cutoff_date = (datetime.now(KST).date() - timedelta(days=lookback_days)).isoformat()

    def fetch_keyword(keyword: str) -> tuple[str, list[dict] | None, str | None]:
        params = urllib.parse.urlencode(
            {
                "query": keyword,
                "display": str(NAVER_MAX_DISPLAY),
                "start": "1",
                "sort": "date",
                "format": "json",
            }
        )
        request = urllib.request.Request(
            f"{NAVER_API_HUB_URL}?{params}",
            headers={
                "X-NCP-APIGW-API-KEY-ID": client_id,
                "X-NCP-APIGW-API-KEY": client_secret,
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (compatible; ETS-LIVE-DASHBOARD/3.0)",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=NAVER_TIMEOUT_SECONDS) as response:
                payload = response.read(5_000_000)
            return keyword, parse_naver_news_payload(
                payload,
                query_keyword=keyword,
                keywords=keywords,
                cutoff_date=cutoff_date,
            ), None
        except Exception as exc:
            # 예외 문자열에는 URL과 상태코드만 포함되며 인증 헤더 값은 절대 기록하지 않는다.
            return keyword, None, str(exc)

    with ThreadPoolExecutor(max_workers=min(3, len(keywords))) as executor:
        results = list(executor.map(fetch_keyword, keywords))

    candidates_by_key: dict[str, dict] = {}
    keyword_errors: list[str] = []
    for keyword, items, error in results:
        if error:
            keyword_errors.append(f"{keyword}: {error}")
            continue
        for item in items or []:
            key = canonical_news_url(item.get("url", "")) or (
                f"{item.get('publishedAt', '')}|{normalized_news_title(item.get('title', ''))}"
            )
            if key not in candidates_by_key:
                candidates_by_key[key] = item
                continue
            candidates_by_key[key] = merge_news_metadata(candidates_by_key[key], item)

    if not candidates_by_key and keyword_errors:
        raise RuntimeError("모든 NAVER 뉴스 키워드 검색에 실패했습니다: " + " | ".join(keyword_errors))
    if keyword_errors:
        print("NAVER 뉴스 일부 키워드 검색 경고: " + " | ".join(keyword_errors), file=sys.stderr)

    candidates = verify_naver_news_candidates(list(candidates_by_key.values()), keywords)
    candidates = sorted(
        candidates,
        key=lambda item: (item.get("publishedAt", ""), item.get("title", "")),
        reverse=True,
    )
    return candidates


def main() -> int:
    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    keywords = [str(value).strip() for value in settings.get("policyKeywords", []) if str(value).strip()]
    sources = settings.get("policySources", [])
    news_searches = settings.get("policyNewsSearches", [])
    naver_client_id = os.getenv(NAVER_CLIENT_ID_ENV, "").strip()
    naver_client_secret = os.getenv(NAVER_CLIENT_SECRET_ENV, "").strip()
    naver_enabled = bool(naver_client_id and naver_client_secret)
    naver_config_warning = ""
    if bool(naver_client_id) != bool(naver_client_secret):
        naver_config_warning = (
            f"{NAVER_CLIENT_ID_ENV}와 {NAVER_CLIENT_SECRET_ENV} 중 하나만 설정되어 "
            "NAVER 뉴스 수집을 건너뜁니다."
        )
    elif news_searches and not naver_enabled:
        naver_config_warning = (
            f"{NAVER_CLIENT_ID_ENV}와 {NAVER_CLIENT_SECRET_ENV}이 설정되지 않아 "
            "NAVER 뉴스 수집을 건너뜁니다."
        )
    news_keyword_file = str(settings.get("newsKeywordFile", "config/news_keywords.txt")).strip()
    news_keywords = load_keyword_file(news_keyword_file) if (news_searches or naver_enabled) else []
    lookback_days = max(1, min(int(settings.get("policyLookbackDays", 30)), 90))
    if not (sources or news_searches or naver_enabled):
        raise RuntimeError("config/settings.json에 검색 자료를 설정하세요.")
    if sources and not keywords:
        raise RuntimeError("config/settings.json에 공식자료 검색 키워드를 설정하세요.")
    if (news_searches or naver_enabled) and not news_keywords:
        raise RuntimeError(f"{news_keyword_file}에 뉴스 검색 키워드를 한 개 이상 입력하세요.")

    collected: list[dict] = []
    errors: list[str] = []
    if naver_config_warning:
        errors.append(f"NAVER 뉴스: {naver_config_warning}")
    attempted_sources = 0
    successful_sources = 0
    for source in sources:
        attempted_sources += 1
        try:
            print(f"수집 시작: {source.get('name', '기후부 공식자료')}", flush=True)
            source_items = (
                fetch_krx_notices(source, keywords)
                if source.get("collector") == "krxBoard"
                else fetch_source(source, keywords)
            )
            collected.extend(source_items)
            successful_sources += 1
            print(f"수집 완료: {source.get('name', '기후부 공식자료')} {len(source_items)}건", flush=True)
        except Exception as exc:  # 네트워크 또는 제공처 오류를 다음 자료와 분리
            errors.append(f"{source.get('name', 'RSS')}: {exc}")
    for search in news_searches:
        attempted_sources += 1
        try:
            print(f"수집 시작: {search.get('name', '뉴스검색')}", flush=True)
            news_items = fetch_google_news(search, news_keywords, lookback_days)
            collected.extend(news_items)
            successful_sources += 1
            print(f"수집 완료: {search.get('name', '뉴스검색')} {len(news_items)}건", flush=True)
        except Exception as exc:
            errors.append(f"{search.get('name', '뉴스검색')}: {exc}")

    if naver_enabled:
        attempted_sources += 1
        try:
            print("수집 시작: NAVER 뉴스", flush=True)
            naver_items = fetch_naver_news(
                news_keywords,
                lookback_days,
                naver_client_id,
                naver_client_secret,
            )
            collected.extend(naver_items)
            successful_sources += 1
            print(f"수집 완료: NAVER 뉴스 {len(naver_items)}건", flush=True)
        except Exception as exc:
            # NAVER 장애·인증 오류가 있어도 Google 뉴스와 공식자료 결과는 저장한다.
            errors.append(f"NAVER 뉴스: {exc}")

    source_count = attempted_sources
    if successful_sources == 0:
        raise RuntimeError("모든 정책·뉴스 수집에 실패했습니다: " + " | ".join(errors))

    existing_document: dict = {}
    existing = []
    existing_schedules: list[dict] = []
    if OUTPUT_PATH.exists():
        try:
            existing_document = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
            existing = existing_document.get("items", [])
            existing_schedules = existing_document.get("institutionSchedules", [])
            if not isinstance(existing_schedules, list):
                existing_schedules = []
        except (OSError, json.JSONDecodeError):
            existing_document = {}
            existing = []
            existing_schedules = []

    merged: dict[str, dict] = {}
    for item in existing + collected:
        item["section"] = policy_section(item)
        haystack = f"{item.get('title', '')} {item.get('summary', '')}".lower()
        allowed_keywords = news_keywords if item.get("sourceType") == "news" else keywords
        matched_keywords = {str(value).strip().lower() for value in item.get("matchedKeywords", [])}
        if item.get("sourceType") == "news":
            visible_matches = news_field_phrase_matches(
                str(item.get("title", "")),
                str(item.get("summary", "")),
                news_keywords,
            )
            if not visible_matches:
                # Google/NAVER 모두 실제 제목·요약 문구를 최종 기준으로 삼는다.
                # NAVER 본문 검증 통과 건은 키워드 문맥을 요약에 보존하므로 통과한다.
                continue
            item["matchedKeywords"] = visible_matches
            canonical_url = canonical_news_url(item.get("url", ""))
            key = (
                f"news-url|{canonical_url}"
                if canonical_url
                else f"news|{item.get('publishedAt')}|{normalized_news_title(item.get('title', ''))}"
            )
            if key in merged:
                merged[key] = merge_news_metadata(merged[key], item)
            else:
                merged[key] = item
        else:
            trusted_official = item["section"] == "krx_notice" or item.get("_trustedSearchMatch")
            if not trusted_official and not any(
                keyword.lower() in haystack or keyword.lower() in matched_keywords
                for keyword in allowed_keywords
            ):
                continue
            if item["section"] == "krx_notice":
                stable_id = item.get("sourceId") or item.get("id")
                if not stable_id:
                    stable_id = f"{item.get('publishedAt')}|{item.get('title')}"
                key = f"krx_notice|{stable_id}"
                if key in merged and item.get("_detailFetchFailed"):
                    previous_summary = str(merged[key].get("summary", "")).strip()
                    if previous_summary:
                        item["summary"] = previous_summary
                        item["category"] = category_for(f"{item.get('title', '')} {previous_summary}")
            else:
                normalized_title = re.sub(r"\s+", " ", str(item.get("title", ""))).strip().lower()
                key = f"{item['section']}|{item.get('publishedAt')}|{normalized_title}"
            merged[key] = item

    limit = max(1, min(int(settings.get("maxPolicyItems", 60)), 200))
    max_news = max(0, min(int(settings.get("maxNewsItems", 24)), limit))
    ranked = sorted(merged.values(), key=lambda item: (item.get("publishedAt", ""), item.get("title", "")), reverse=True)
    all_news = dedupe_news([item for item in ranked if item.get("sourceType") == "news"])
    for item in all_news:
        visible_text = f"{item.get('title', '')} {item.get('summary', '')}"
        item["category"] = category_for(visible_text)
        item["region"] = news_region_for(item)
    # 국회 종합일정 기사는 기관일정에만 두고, 제외 후 뉴스 정원을 다시 채운다.
    news = [item for item in all_news if not is_routable_assembly_agenda_article(item)][:max_news]
    official = [item for item in ranked if item.get("sourceType") != "news"]
    institution_schedules = build_institution_schedules(
        [item for item in official if policy_section(item) != "krx_notice"] + all_news,
        existing_schedules,
    )

    # 어느 한 게시판의 최신글이 많아도 세 공식자료 탭이 비지 않도록 균형 배분한다.
    official_capacity = max(0, limit - len(news))
    official_sections = ("press", "notice", "krx_notice")
    base_quota, quota_remainder = divmod(official_capacity, len(official_sections))
    selected_official: list[dict] = []
    for index, section in enumerate(official_sections):
        section_quota = base_quota + (1 if index < quota_remainder else 0)
        section_items = [item for item in official if policy_section(item) == section]
        selected_official.extend(section_items[:section_quota])

    def official_item_key(item: dict) -> str:
        fallback = f"{item.get('publishedAt')}|{item.get('title')}"
        stable_id = item.get("sourceId") or item.get("id") or fallback
        return f"{policy_section(item)}|{stable_id}"

    selected_keys = {official_item_key(item) for item in selected_official}
    extras = [
        item for item in official
        if official_item_key(item) not in selected_keys
    ]
    selected_official.extend(extras[: max(0, official_capacity - len(selected_official))])
    items = sorted(selected_official + news, key=lambda item: (item.get("publishedAt", ""), item.get("title", "")), reverse=True)[:limit]
    for item in items:
        item.pop("_trustedSearchMatch", None)
        item.pop("_bodyVerificationQueries", None)
        item.pop("_bodyCandidateScore", None)
        item.pop("_detailFetchFailed", None)
        item.pop("impact", None)
        item.pop("impactReason", None)
        item.pop("impactSource", None)
    output = {
        "lastSync": datetime.now(KST).isoformat(timespec="seconds"),
        "keywords": keywords,
        "newsKeywords": news_keywords,
        "sourceCount": source_count,
        "newsProviders": [
            *(["Google News RSS"] if news_searches else []),
            *(["NAVER API HUB"] if naver_enabled else []),
        ],
        "warning": " | ".join(errors) if errors else None,
        "aiInsight": generate_policy_insight(items),
        "institutionSchedules": institution_schedules,
        "items": items,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"정책자료 {len(items)}건, 기관일정 {len(institution_schedules)}건 저장, "
        f"신규 수집 {len(collected)}건"
    )
    if errors:
        print("일부 수집 경고: " + " | ".join(errors), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
