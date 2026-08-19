#!/usr/bin/env python3
"""기후에너지환경부 RSS에서 설정 키워드가 포함된 자료를 수집한다."""

from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "config" / "settings.json"
OUTPUT_PATH = ROOT / "public" / "data" / "policies.json"
KST = ZoneInfo("Asia/Seoul")


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


def impact_for(text: str) -> str:
    downside = ("추가 공급", "공급 확대", "방출", "매각 확대")
    upside = ("공급 축소", "매각 축소", "감축 강화", "할당량 축소")
    if any(word in text for word in downside):
        return "하방"
    if any(word in text for word in upside):
        return "상방"
    return "중립"


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
                "impact": impact_for(haystack),
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
                "impact": impact_for(haystack),
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
    news = [item for item in ranked if item.get("sourceType") == "news"][:max_news]
    official = [item for item in ranked if item.get("sourceType") != "news"]
    items = sorted(official + news, key=lambda item: (item.get("publishedAt", ""), item.get("title", "")), reverse=True)[:limit]
    output = {
        "lastSync": datetime.now(KST).isoformat(timespec="seconds"),
        "keywords": keywords,
        "sourceCount": source_count,
        "warning": " | ".join(errors) if errors else None,
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
