#!/usr/bin/env python3
"""기후에너지환경부 RSS에서 설정 키워드가 포함된 자료를 수집한다."""

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
from difflib import SequenceMatcher
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "config" / "settings.json"
OUTPUT_PATH = ROOT / "public" / "data" / "policies.json"
KST = ZoneInfo("Asia/Seoul")


def fallback_policy_insight(items: list[dict]) -> str:
    official = [item for item in items if item.get("sourceType") != "news"][:10]
    if not official:
        return "최근 기후부 공식자료가 수집되면 정책 변화가 시장 수급에 미칠 영향을 분석합니다."
    haystack = " ".join(f"{item.get('title', '')} {item.get('summary', '')}" for item in official)
    if re.search(r"시장안정|예비분|K-MSR", haystack, re.IGNORECASE):
        return "최근 공식자료는 시장안정화 장치와 공급조절 체계 구체화에 무게가 실립니다. 가격 급변 시 공급 대응 가능성이 커지는 만큼 발동 기준과 실제 조정물량을 확인해야 합니다."
    if re.search(r"유상|경매|입찰", haystack):
        return "최근 공식자료는 유상경매 일정과 공급 경로 조정에 초점이 맞춰져 있습니다. 단기 가격은 경매 공급물량과 응찰강도가 현물 수급을 얼마나 흡수하는지에 좌우될 가능성이 큽니다."
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
        "정책 변화의 핵심, 배출권 수급에 작용할 수 있는 경로, 후속 확인 변수를 자연스러운 한국어 2문장, 110~190자로 작성하세요. 문장만 출력하세요.\n\n" + materials
    )
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps({"model": model, "input": prompt, "max_output_tokens": 300}, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            text = extract_response_text(json.loads(response.read().decode("utf-8")))
        text = re.sub(r"\s+", " ", text).strip().strip('"')
        if len(text) >= 40:
            result.update({"summary": text[:260], "source": "openai", "model": model})
    except Exception as exc:
        print(f"AI 정책 인사이트 경고: {exc}", file=sys.stderr)
    return result


def clean_html(value: str, limit: int = 260) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else f"{text[: limit - 1].rstrip()}…"


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
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 ETS-SIGNAL/1.0", "Accept": "application/rss+xml, application/xml, text/xml"},
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        payload = response.read()
    return parse_rss(payload)


def fetch_source(source: dict, keywords: list[str]) -> list[dict]:
    root = fetch_rss(source["url"])
    result = []
    for item in root.findall(".//item"):
        title = clean_html(child_text(item, "title"), 180)
        description = clean_html(child_text(item, "description"))
        link = re.sub(r";jsessionid=[^?]+", "", html.unescape(child_text(item, "link")))
        published = normalize_date(child_text(item, "pubDate") or child_text(item, "date"))
        haystack = f"{title} {description}".lower()
        matched = [keyword for keyword in keywords if keyword.lower() in haystack]
        if not matched:
            continue
        stable_key = link or f"{published}|{title}"
        result.append(
            {
                "id": hashlib.sha1(stable_key.encode("utf-8")).hexdigest()[:16],
                "publishedAt": published,
                "title": title,
                "category": category_for(haystack),
                "summary": description,
                "source": source["name"],
                "sourceType": "official",
                "url": link,
                "matchedKeywords": matched,
            }
        )
    return result


def fetch_google_news(search: dict, keywords: list[str], lookback_days: int) -> list[dict]:
    params = urllib.parse.urlencode(
        {
            "q": f"{search['query']} when:{lookback_days}d",
            "hl": "ko",
            "gl": "KR",
            "ceid": "KR:ko",
        }
    )
    root = fetch_rss(f"https://news.google.com/rss/search?{params}")
    result = []
    for item in root.findall(".//item"):
        raw_title = clean_html(child_text(item, "title"), 180)
        title = re.sub(r"\s+-\s+[^-]+$", "", raw_title).strip() or raw_title
        description = clean_html(child_text(item, "description"))
        haystack = f"{title} {description}".lower()
        matched = [keyword for keyword in keywords if keyword.lower() in haystack]
        if not matched:
            continue
        link = html.unescape(child_text(item, "link"))
        published = normalize_date(child_text(item, "pubDate") or child_text(item, "date"))
        source_name = child_text(item, "source") or search.get("name", "Google News")
        stable_key = link or f"{published}|{title}"
        result.append(
            {
                "id": hashlib.sha1(stable_key.encode("utf-8")).hexdigest()[:16],
                "publishedAt": published,
                "title": title,
                "category": category_for(haystack),
                "summary": description,
                "source": source_name,
                "sourceType": "news",
                "url": link,
                "matchedKeywords": matched,
            }
        )
    return result


def main() -> int:
    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    keywords = [str(value).strip() for value in settings.get("policyKeywords", []) if str(value).strip()]
    sources = settings.get("policySources", [])
    news_searches = settings.get("policyNewsSearches", [])
    lookback_days = max(1, min(int(settings.get("policyLookbackDays", 30)), 90))
    if not keywords or not (sources or news_searches):
        raise RuntimeError("config/settings.json에 키워드와 검색 자료를 설정하세요.")

    collected: list[dict] = []
    errors: list[str] = []
    for source in sources:
        try:
            collected.extend(fetch_source(source, keywords))
        except Exception as exc:  # 네트워크 또는 제공처 오류를 다음 자료와 분리
            errors.append(f"{source.get('name', 'RSS')}: {exc}")
    for search in news_searches:
        try:
            collected.extend(fetch_google_news(search, keywords, lookback_days))
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
        haystack = f"{item.get('title', '')} {item.get('summary', '')}".lower()
        if not any(keyword.lower() in haystack for keyword in keywords):
            continue
        key = item.get("url") or f"{item.get('publishedAt')}|{item.get('title')}"
        merged[key] = item

    limit = max(1, min(int(settings.get("maxPolicyItems", 60)), 200))
    max_news = max(0, min(int(settings.get("maxNewsItems", 24)), limit))
    ranked = sorted(merged.values(), key=lambda item: (item.get("publishedAt", ""), item.get("title", "")), reverse=True)
    news = dedupe_news([item for item in ranked if item.get("sourceType") == "news"])[:max_news]
    official = [item for item in ranked if item.get("sourceType") != "news"]
    items = sorted(official + news, key=lambda item: (item.get("publishedAt", ""), item.get("title", "")), reverse=True)[:limit]
    for item in items:
        item.pop("impact", None)
        item.pop("impactReason", None)
        item.pop("impactSource", None)
    output = {
        "lastSync": datetime.now(KST).isoformat(timespec="seconds"),
        "keywords": keywords,
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
