#!/usr/bin/env python3
# PRESS_TAB_FIX_VERSION = "2026-08-20-v3.1-fast-current-board"
"""기후부 공식자료와 시장 뉴스의 제목·원문 본문에서 설정 키워드를 검색한다."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from datetime import datetime
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
    explicit = str(item.get("section", "")).strip().lower()
    if explicit in {"press", "notice"}:
        return explicit
    source = str(item.get("source", ""))
    url = str(item.get("url", ""))
    if "보도자료" in source or re.search(r"(?:menuId=(?:286|10598)|boardMasterId=(?:1|939))(?:&|$)", url):
        return "press"
    return "notice"


def source_section(source: dict) -> str:
    explicit = str(source.get("type", "")).strip().lower()
    if explicit in {"press", "notice"}:
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
        return "최근 기후부 공식자료가 수집되면 정책 변화가 시장 수급에 미칠 영향을 분석합니다."
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
    value = re.sub(r"\[[^\]]+\]|\([^)]*\)", " ", (value or "").lower())
    return re.sub(r"[^0-9a-z가-힣]+", "", value)


def title_bigrams(value: str) -> set[str]:
    return {value[index : index + 2] for index in range(max(0, len(value) - 1))}


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
    return ratio >= 0.69 or dice >= 0.58


def dedupe_news(items: list[dict]) -> list[dict]:
    remaining = list(items)
    groups: list[list[dict]] = []
    while remaining:
        group = [remaining.pop(0)]
        changed = True
        while changed:
            changed = False
            for candidate in list(remaining):
                if candidate.get("publishedAt") != group[0].get("publishedAt"):
                    continue
                if any(similar_news_title(candidate.get("title", ""), member.get("title", "")) for member in group):
                    group.append(candidate)
                    remaining.remove(candidate)
                    changed = True
        groups.append(group)

    result: list[dict] = []
    for group in groups:
        representative = max(group, key=lambda item: (len(item.get("summary", "")), len(item.get("title", ""))))
        representative = dict(representative)
        sources = list(dict.fromkeys(item.get("source", "") for item in group if item.get("source")))
        representative["duplicateCount"] = len(group)
        representative["duplicateSources"] = sources
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
        "당신은 한국 배출권거래제(K-ETS) 정책 분석가입니다. 아래 기후부 공식 보도자료와 공지사항만 근거로 최근 정책 인사이트를 작성하세요. "
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


def html_attribute(tag_text: str, name: str) -> str:
    match = re.search(rf"\b{name}\s*=\s*(['\"])(.*?)\1", tag_text, re.IGNORECASE | re.DOTALL)
    return html.unescape(match.group(2)).strip() if match else ""


def extract_article_text(page_html: str) -> str:
    candidates: list[str] = []
    for match in re.finditer(r'"articleBody"\s*:\s*("(?:\\.|[^"\\])*")', page_html, re.IGNORECASE):
        try:
            candidates.append(clean_html(json.loads(match.group(1)), 20_000))
        except (json.JSONDecodeError, TypeError):
            pass
    for meta in re.findall(r"<meta\b[^>]*>", page_html, re.IGNORECASE):
        key = f"{html_attribute(meta, 'property')} {html_attribute(meta, 'name')}".lower()
        if re.search(r"og:description|twitter:description|description", key):
            candidates.append(clean_html(html_attribute(meta, "content"), 20_000))
    block_pattern = re.compile(
        r"<(article|main)\b[^>]*>([\s\S]*?)</\1>"
        r"|<(?:div|section)\b[^>]*(?:class|id)\s*=\s*(['\"])[^'\"]*"
        r"(?:article[-_ ]?body|article[-_ ]?content|news[-_ ]?body|news[-_ ]?content|story[-_ ]?body|"
        r"entry[-_ ]?content|post[-_ ]?content|board[-_ ]?view|board[-_ ]?content|bbs[-_ ]?view|"
        r"view[-_ ]?content|view[-_ ]?cont|view[-_ ]?txt)[^'\"]*\3[^>]*>([\s\S]*?)</(?:div|section)>",
        re.IGNORECASE,
    )
    for match in block_pattern.finditer(page_html):
        candidates.append(clean_html(match.group(2) or match.group(4) or "", 20_000))
    useful = [text for text in candidates if len(text) >= 40]
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
    positions = [lower.find(keyword.lower()) for keyword in matched_keywords]
    positions = [position for position in positions if position >= 0]
    start = max(0, min(positions) - 80) if positions else 0
    excerpt = text[start : start + limit].strip()
    return f"{'…' if start else ''}{excerpt}{'…' if start + limit < len(text) else ''}"


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


def category_for(text: str) -> str:
    if "유상경매" in text or "경매" in text or "유상할당" in text:
        return "유상경매"
    if "상쇄" in text or "외부사업" in text:
        return "상쇄"
    if "시장안정" in text or "예비분" in text:
        return "시장안정"
    if "할당계획" in text or "계획기간" in text or "할당대상" in text:
        return "할당계획"
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
        title = re.sub(r"\s+-\s+[^-]+$", "", raw_title).strip() or raw_title
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
                "matchedKeywords": [keyword for keyword in keywords if keyword.lower() in haystack],
                "_trustedSearchMatch": True,
            }
        )
    return filter_title_and_body(candidates[:80], keywords)


def main() -> int:
    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    keywords = [str(value).strip() for value in settings.get("policyKeywords", []) if str(value).strip()]
    sources = settings.get("policySources", [])
    news_searches = settings.get("policyNewsSearches", [])
    news_keyword_file = str(settings.get("newsKeywordFile", "config/news_keywords.txt")).strip()
    news_keywords = load_keyword_file(news_keyword_file) if news_searches else []
    lookback_days = max(1, min(int(settings.get("policyLookbackDays", 30)), 90))
    if not (sources or news_searches):
        raise RuntimeError("config/settings.json에 검색 자료를 설정하세요.")
    if sources and not keywords:
        raise RuntimeError("config/settings.json에 공식자료 검색 키워드를 설정하세요.")
    if news_searches and not news_keywords:
        raise RuntimeError(f"{news_keyword_file}에 뉴스 검색 키워드를 한 개 이상 입력하세요.")

    collected: list[dict] = []
    errors: list[str] = []
    for source in sources:
        try:
            print(f"수집 시작: {source.get('name', '기후부 공식자료')}", flush=True)
            source_items = fetch_source(source, keywords)
            collected.extend(source_items)
            print(f"수집 완료: {source.get('name', '기후부 공식자료')} {len(source_items)}건", flush=True)
        except Exception as exc:  # 네트워크 또는 제공처 오류를 다음 자료와 분리
            errors.append(f"{source.get('name', 'RSS')}: {exc}")
    for search in news_searches:
        try:
            print(f"수집 시작: {search.get('name', '뉴스검색')}", flush=True)
            news_items = fetch_google_news(search, news_keywords, lookback_days)
            collected.extend(news_items)
            print(f"수집 완료: {search.get('name', '뉴스검색')} {len(news_items)}건", flush=True)
        except Exception as exc:
            errors.append(f"{search.get('name', '뉴스검색')}: {exc}")

    source_count = len(sources) + len(news_searches)
    if len(errors) == source_count:
        raise RuntimeError("모든 정책·뉴스 수집에 실패했습니다: " + " | ".join(errors))

    existing = []
    if OUTPUT_PATH.exists():
        try:
            existing = json.loads(OUTPUT_PATH.read_text(encoding="utf-8")).get("items", [])
        except (OSError, json.JSONDecodeError):
            existing = []

    merged: dict[str, dict] = {}
    for item in existing + collected:
        item["section"] = policy_section(item)
        haystack = f"{item.get('title', '')} {item.get('summary', '')}".lower()
        allowed_keywords = news_keywords if item.get("sourceType") == "news" else keywords
        matched_keywords = {str(value).strip().lower() for value in item.get("matchedKeywords", [])}
        if not item.get("_trustedSearchMatch") and not any(
            keyword.lower() in haystack or keyword.lower() in matched_keywords
            for keyword in allowed_keywords
        ):
            continue
        if item.get("sourceType") == "news":
            key = item.get("url") or f"news|{item.get('publishedAt')}|{item.get('title')}"
        else:
            normalized_title = re.sub(r"\s+", " ", str(item.get("title", ""))).strip().lower()
            key = f"{item['section']}|{item.get('publishedAt')}|{normalized_title}"
        merged[key] = item

    limit = max(1, min(int(settings.get("maxPolicyItems", 60)), 200))
    max_news = max(0, min(int(settings.get("maxNewsItems", 24)), limit))
    ranked = sorted(merged.values(), key=lambda item: (item.get("publishedAt", ""), item.get("title", "")), reverse=True)
    news = dedupe_news([item for item in ranked if item.get("sourceType") == "news"])[:max_news]
    official = [item for item in ranked if item.get("sourceType") != "news"]

    # 최신 공지사항이 많아도 보도자료 탭이 비지 않도록 공식자료 영역을 균형 배분한다.
    official_capacity = max(0, limit - len(news))
    press = [item for item in official if policy_section(item) == "press"]
    notices = [item for item in official if policy_section(item) == "notice"]
    press_quota = (official_capacity + 1) // 2
    notice_quota = official_capacity // 2
    selected_official = press[:press_quota] + notices[:notice_quota]
    selected_keys = {item.get("id") or f"{item.get('publishedAt')}|{item.get('title')}" for item in selected_official}
    extras = [
        item for item in official
        if (item.get("id") or f"{item.get('publishedAt')}|{item.get('title')}") not in selected_keys
    ]
    selected_official.extend(extras[: max(0, official_capacity - len(selected_official))])
    items = sorted(selected_official + news, key=lambda item: (item.get("publishedAt", ""), item.get("title", "")), reverse=True)[:limit]
    for item in items:
        item.pop("_trustedSearchMatch", None)
        item.pop("impact", None)
        item.pop("impactReason", None)
        item.pop("impactSource", None)
    output = {
        "lastSync": datetime.now(KST).isoformat(timespec="seconds"),
        "keywords": keywords,
        "newsKeywords": news_keywords,
        "sourceCount": source_count,
        "warning": " | ".join(errors) if errors else None,
        "aiInsight": generate_policy_insight(items),
        "items": items,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"정책자료 {len(items)}건 저장, 신규 수집 {len(collected)}건")
    if errors:
        print("일부 RSS 경고: " + " | ".join(errors), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
