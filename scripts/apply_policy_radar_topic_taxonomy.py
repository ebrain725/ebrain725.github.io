#!/usr/bin/env python3
"""Apply the shared 38-topic taxonomy to official policy-radar histories.

The collector configuration intentionally searches a broad superset. This module
performs the final contextual decision, preserves all KRX ETS-board notices, and
removes broad ministry matches that do not satisfy the required policy or
industrial context.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
KST = ZoneInfo("Asia/Seoul")
DEFAULT_TAXONOMY = ROOT / "config" / "policy_radar_topics.json"
DEFAULT_POLICY = ROOT / "public" / "data" / "policies.json"
DEFAULT_CLIMATE = ROOT / "public" / "data" / "policy-official-history.json"
DEFAULT_CLIMATE_SUMMARY = ROOT / "public" / "data" / "policy-official-history-summary.json"
DEFAULT_MOTIE = ROOT / "public" / "data" / "motie-policy-history.json"
DEFAULT_SUMMARY = ROOT / "public" / "data" / "policy-radar-topic-summary.json"
OFFICIAL_SECTIONS = {"press", "notice", "krx_notice", "motie_press", "motie_notice"}
CLIMATE_SECTIONS = {"press", "notice", "krx_notice"}
MOTIE_SECTIONS = {"motie_press", "motie_notice"}
ASCII_TOKEN_ALIASES = {
    "ets": r"(?<![a-z0-9])ets(?![a-z0-9])",
    "kets": r"(?<![a-z0-9])k\s*[-‐‑‒–—−_]?\s*ets(?![a-z0-9])",
    "euets": r"(?<![a-z0-9])eu\s*[-‐‑‒–—−_]?\s*ets(?![a-z0-9])",
    "eua": r"(?<![a-z0-9])eua(?:s)?(?![a-z0-9])",
    "smp": r"(?<![a-z0-9])smp(?![a-z0-9])",
    "rps": r"(?<![a-z0-9])rps(?![a-z0-9])",
    "re100": r"(?<![a-z0-9])re\s*[-‐‑‒–—−_]?\s*100(?![a-z0-9])",
    "cbam": r"(?<![a-z0-9])cbam(?![a-z0-9])",
    "msr": r"(?<![a-z0-9])msr(?![a-z0-9])",
    "kmsr": r"(?<![a-z0-9])k\s*[-‐‑‒–—−_]?\s*msr(?![a-z0-9])",
    "itmo": r"(?<![a-z0-9])itmos?(?![a-z0-9])",
    "koc": r"(?<![a-z0-9])koc(?![a-z0-9])",
    "kcu": r"(?<![a-z0-9])kcu(?![a-z0-9])",
    "kau": r"(?<![a-z0-9])kau\s*\d{2}(?![a-z0-9])",
    "lng": r"(?<![a-z0-9])lng(?![a-z0-9])",
    "shutdown": r"(?<![a-z0-9])shut\s*down(?![a-z0-9])",
    "netzero": r"(?<![a-z0-9])net\s*[-‐‑‒–—−_]?\s*zero(?![a-z0-9])",
    "china": r"(?<![a-z0-9])china(?![a-z0-9])",
}


def clean_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(str(value or "")))
    text = re.sub(r"<script\b.*?</script>|<style\b.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def compact_text(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", clean_text(value).lower())


def list_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [clean_text(item) for item in value if clean_text(item)]
    if isinstance(value, str) and clean_text(value):
        return [clean_text(value)]
    return []


def dedupe(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = clean_text(value)
        if text and text not in result:
            result.append(text)
    return result


def atomic_write(path: Path, document: dict[str, Any]) -> bool:
    content = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)
    return True


def term_present(term: Any, raw_text: str, compact: str) -> bool:
    normalized = compact_text(term)
    if not normalized:
        return False
    pattern = ASCII_TOKEN_ALIASES.get(normalized)
    if pattern:
        return bool(re.search(pattern, raw_text.lower(), re.IGNORECASE))
    if re.fullmatch(r"[a-z0-9]+", normalized):
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", raw_text.lower()))
    return normalized in compact


def any_present(terms: Iterable[Any], raw_text: str, compact: str) -> bool:
    return any(term_present(term, raw_text, compact) for term in terms)


def matching_terms(terms: Iterable[Any], raw_text: str, compact: str) -> list[str]:
    return dedupe(term for term in terms if term_present(term, raw_text, compact))


def rule_matches(rule: dict[str, Any], title: str, content: str) -> tuple[bool, list[str], list[str]]:
    fields = str(rule.get("fields") or "title").lower()
    material = title if fields == "title" else f"{title} {content}".strip()
    compact = compact_text(material)
    if not compact:
        return False, [], []

    trigger_terms = list_values(rule.get("any"))
    if trigger_terms and not any_present(trigger_terms, material, compact):
        return False, [], []

    all_terms = list_values(rule.get("all"))
    if all_terms and not all(term_present(term, material, compact) for term in all_terms):
        return False, [], []

    context_any = list_values(rule.get("contextAny"))
    if context_any and not any_present(context_any, material, compact):
        return False, [], []

    context_groups = rule.get("contextGroups") or []
    if context_groups and not all(
        any_present(list_values(group), material, compact)
        for group in context_groups
        if isinstance(group, list)
    ):
        return False, [], []

    exclude_any = list_values(rule.get("excludeAny"))
    if exclude_any and any_present(exclude_any, material, compact):
        return False, [], []

    evidence_terms = dedupe([
        *matching_terms(trigger_terms, material, compact),
        *matching_terms(all_terms, material, compact),
        *matching_terms(context_any, material, compact),
    ])
    matched_fields: list[str] = []
    title_compact = compact_text(title)
    content_compact = compact_text(content)
    if any(term_present(term, title, title_compact) for term in evidence_terms):
        matched_fields.append("title")
    if fields != "title" and any(term_present(term, content, content_compact) for term in evidence_terms):
        matched_fields.append("content")
    return True, evidence_terms, matched_fields or ["title" if fields == "title" else "content"]


def classify_item(item: dict[str, Any], taxonomy: dict[str, Any]) -> dict[str, Any] | None:
    section = str(item.get("section") or "").strip().lower()
    title = clean_text(item.get("title"))
    summary = clean_text(item.get("summary") or item.get("content") or item.get("description"))
    stored_keywords = list_values(item.get("matchedKeywords"))
    content = " ".join(value for value in [summary, " ".join(stored_keywords)] if value)

    matched_topics: list[dict[str, Any]] = []
    evidence_terms: list[str] = []
    evidence_fields: list[str] = []
    for topic in taxonomy.get("topics", []):
        if not isinstance(topic, dict):
            continue
        topic_terms: list[str] = []
        topic_fields: list[str] = []
        matched = False
        for rule in topic.get("rules", []):
            if not isinstance(rule, dict):
                continue
            accepted, terms, fields = rule_matches(rule, title, content)
            if accepted:
                matched = True
                topic_terms.extend(terms)
                topic_fields.extend(fields)
        if matched:
            matched_topics.append(topic)
            evidence_terms.extend(topic_terms or list_values(topic.get("searchTerms"))[:1])
            evidence_fields.extend(topic_fields)

    if section != "krx_notice" and not matched_topics:
        return None

    direct = section == "krx_notice" or any(topic.get("scope") == "direct" for topic in matched_topics)
    normalized = dict(item)
    normalized["title"] = title
    normalized["summary"] = summary or "원문에서 세부 내용을 확인하세요."
    normalized["matchedKeywords"] = dedupe([*stored_keywords, *evidence_terms])
    normalized["matchedFields"] = dedupe([*list_values(item.get("matchedFields")), *evidence_fields])
    normalized["topicIds"] = [str(topic.get("id")) for topic in matched_topics]
    normalized["matchedTopics"] = [str(topic.get("label")) for topic in matched_topics]
    normalized["topicGroups"] = dedupe(topic.get("group") for topic in matched_topics)
    normalized["primaryTopic"] = normalized["matchedTopics"][0] if normalized["matchedTopics"] else "한국거래소 배출권시장 공지"
    normalized["keywordScope"] = "direct" if direct else "related"
    normalized["relevanceType"] = normalized["keywordScope"]
    normalized["taxonomyVersion"] = str(taxonomy.get("schemaVersion") or "")
    if section == "krx_notice" and not matched_topics:
        normalized["sourceScope"] = "direct-ets-board"
    return normalized


def stable_key(item: dict[str, Any]) -> str:
    section = str(item.get("section") or item.get("sourceType") or "").strip().lower()
    source_id = str(item.get("sourceId") or item.get("id") or "").strip()
    if source_id:
        return f"{section}|id|{source_id}"
    url = str(item.get("url") or "").strip()
    if url:
        return f"{section}|url|{url}"
    return f"{section}|{str(item.get('publishedAt') or '')[:10]}|{compact_text(item.get('title'))}"


def sort_items(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            str(item.get("publishedAt") or item.get("date") or ""),
            str(item.get("title") or ""),
            str(item.get("sourceId") or item.get("id") or ""),
        ),
        reverse=True,
    )


def section_counts(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    counter = Counter(str(item.get("section") or "") for item in items)
    return dict(sorted(counter.items()))


def scope_counts(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    counter = Counter(str(item.get("keywordScope") or "") for item in items)
    return dict(sorted(counter.items()))


def topic_counts(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for item in items:
        counter.update(list_values(item.get("matchedTopics")))
    return dict(counter.most_common())


def coverage_by_section(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for section in sorted({str(item.get("section") or "") for item in items}):
        rows = [item for item in items if str(item.get("section") or "") == section]
        dates = [str(item.get("publishedAt") or "")[:10] for item in rows if str(item.get("publishedAt") or "")]
        result[section] = {
            "records": len(rows),
            "earliest": min(dates) if dates else None,
            "latest": max(dates) if dates else None,
            "direct": sum(item.get("keywordScope") == "direct" for item in rows),
            "related": sum(item.get("keywordScope") == "related" for item in rows),
        }
    return result


def update_generic_metadata(document: dict[str, Any], items: list[dict[str, Any]], version: str) -> None:
    counts = section_counts(items)
    coverage = coverage_by_section(items)
    now = datetime.now(KST).isoformat(timespec="seconds")
    document["items"] = sort_items(items)
    document["topicTaxonomy"] = {
        "version": version,
        "appliedAt": now,
        "itemCount": len(items),
        "counts": counts,
        "scopeCounts": scope_counts(items),
        "topicCounts": topic_counts(items),
    }
    for key in ("itemCount", "officialItemCount"):
        if key in document:
            document[key] = len(items)
    if isinstance(document.get("counts"), dict):
        document["counts"].update(counts)
    if isinstance(document.get("coverage"), dict):
        for section, values in coverage.items():
            current = document["coverage"].get(section)
            if isinstance(current, dict):
                current.update(values)
    validation = document.get("validation")
    if isinstance(validation, dict):
        validation["itemCount"] = len(items)
        validation["counts"] = counts
        validation["scopeCounts"] = scope_counts(items)
        validation["topicCounts"] = topic_counts(items)


def update_climate_summary(
    path: Path,
    climate_items: list[dict[str, Any]],
    version: str,
) -> None:
    try:
        document = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        document = {}
    counts = section_counts(climate_items)
    scopes = defaultdict(Counter)
    for item in climate_items:
        scopes[str(item.get("section") or "")][str(item.get("keywordScope") or "")] += 1
    document["verifiedAt"] = datetime.now(KST).isoformat(timespec="seconds")
    document["keywordExpansionVersion"] = version
    document["officialItemCount"] = len(climate_items)
    document["counts"] = counts
    document["directCounts"] = {section: values.get("direct", 0) for section, values in scopes.items()}
    document["relatedCounts"] = {section: values.get("related", 0) for section, values in scopes.items()}
    document["earliest"] = {
        section: values.get("earliest") for section, values in coverage_by_section(climate_items).items()
    }
    document["latest"] = {
        section: values.get("latest") for section, values in coverage_by_section(climate_items).items()
    }
    document["topicCounts"] = topic_counts(climate_items)
    validation = document.setdefault("validation", {})
    validation.update(
        {
            "itemCount": len(climate_items),
            "counts": counts,
            "directCounts": document["directCounts"],
            "relatedCounts": document["relatedCounts"],
            "topicCounts": document["topicCounts"],
        }
    )
    atomic_write(path, document)


def apply_taxonomy(
    taxonomy_path: Path = DEFAULT_TAXONOMY,
    policy_path: Path = DEFAULT_POLICY,
    climate_path: Path = DEFAULT_CLIMATE,
    motie_path: Path = DEFAULT_MOTIE,
    climate_summary_path: Path = DEFAULT_CLIMATE_SUMMARY,
    summary_path: Path = DEFAULT_SUMMARY,
) -> dict[str, Any]:
    taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    topics = taxonomy.get("topics") or []
    if len(topics) != 38:
        raise RuntimeError(f"정책 레이더 주제는 38개여야 합니다: {len(topics)}")
    version = str(taxonomy.get("schemaVersion") or "")
    if not version:
        raise RuntimeError("정책 레이더 taxonomy schemaVersion이 없습니다.")

    climate_document = json.loads(climate_path.read_text(encoding="utf-8"))
    motie_document = json.loads(motie_path.read_text(encoding="utf-8"))
    policy_document = json.loads(policy_path.read_text(encoding="utf-8"))

    climate_items: list[dict[str, Any]] = []
    rejected_climate = 0
    for raw in climate_document.get("items", []):
        if not isinstance(raw, dict) or str(raw.get("section") or "") not in CLIMATE_SECTIONS:
            continue
        item = classify_item(raw, taxonomy)
        if item is None:
            rejected_climate += 1
        else:
            climate_items.append(item)

    motie_items: list[dict[str, Any]] = []
    rejected_motie = 0
    for raw in motie_document.get("items", []):
        if not isinstance(raw, dict) or str(raw.get("section") or "") not in MOTIE_SECTIONS:
            continue
        item = classify_item(raw, taxonomy)
        if item is None:
            rejected_motie += 1
        else:
            motie_items.append(item)

    climate_by_key = {stable_key(item): item for item in climate_items}
    motie_by_key = {stable_key(item): item for item in motie_items}
    climate_items = sort_items(climate_by_key.values())
    motie_items = sort_items(motie_by_key.values())

    update_generic_metadata(climate_document, climate_items, version)
    update_generic_metadata(motie_document, motie_items, version)
    update_climate_summary(climate_summary_path, climate_items, version)

    dynamic_items = [
        dict(item)
        for item in policy_document.get("items", [])
        if isinstance(item, dict)
        and (
            str(item.get("sourceType") or "") == "news"
            or str(item.get("section") or "") == "news"
            or str(item.get("section") or "") not in OFFICIAL_SECTIONS
        )
    ]
    merged: dict[str, dict[str, Any]] = {}
    for item in [*climate_items, *motie_items, *dynamic_items]:
        merged[stable_key(item)] = item
    policy_document["items"] = sort_items(merged.values())
    policy_document["topicTaxonomy"] = {
        "version": version,
        "appliedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "topicCount": len(topics),
        "officialItemCount": len(climate_items) + len(motie_items),
        "officialCounts": section_counts([*climate_items, *motie_items]),
        "officialScopeCounts": scope_counts([*climate_items, *motie_items]),
    }

    summary = {
        "schemaVersion": version,
        "generatedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "startDate": str(taxonomy.get("startDate") or "2015-01-01"),
        "topicCount": len(topics),
        "officialItemCount": len(climate_items) + len(motie_items),
        "counts": section_counts([*climate_items, *motie_items]),
        "scopeCounts": scope_counts([*climate_items, *motie_items]),
        "coverage": coverage_by_section([*climate_items, *motie_items]),
        "topicCounts": topic_counts([*climate_items, *motie_items]),
        "rejectedContextlessCandidates": {
            "climateMinistry": rejected_climate,
            "industryMinistry": rejected_motie,
        },
        "topics": [
            {
                "id": topic.get("id"),
                "label": topic.get("label"),
                "group": topic.get("group"),
                "scope": topic.get("scope"),
                "count": topic_counts([*climate_items, *motie_items]).get(str(topic.get("label")), 0),
            }
            for topic in topics
        ],
    }

    atomic_write(climate_path, climate_document)
    atomic_write(motie_path, motie_document)
    atomic_write(policy_path, policy_document)
    atomic_write(summary_path, summary)
    return summary


def self_test() -> None:
    taxonomy = json.loads(DEFAULT_TAXONOMY.read_text(encoding="utf-8"))
    if len(taxonomy.get("topics") or []) != 38:
        raise AssertionError("38개 주제가 아닙니다.")

    examples = [
        ({"section": "press", "title": "배출권 유상경매 물량과 일정 공고", "summary": ""}, "direct", "유상경매 물량·일정"),
        ({"section": "motie_press", "title": "폭염으로 최대 전력수요 경신", "summary": ""}, "related", "폭염·한파·전력수요"),
        ({"section": "motie_press", "title": "정유공장 화재로 가동중단", "summary": ""}, "related", "화재·폭발·침수·가동중단"),
        ({"section": "motie_press", "title": "지역 축제 화재 예방 캠페인", "summary": ""}, None, None),
        ({"section": "notice", "title": "EU ETS와 EUA 시장 동향", "summary": ""}, "direct", "EU ETS·EUA"),
        ({"section": "notice", "title": "기후정책 소송 결과", "summary": ""}, "related", "제도 확정 지연·소송"),
    ]
    for item, expected_scope, expected_topic in examples:
        result = classify_item(item, taxonomy)
        if expected_scope is None:
            if result is not None:
                raise AssertionError(f"오탐 예시가 통과했습니다: {item} -> {result}")
            continue
        if result is None or result.get("keywordScope") != expected_scope:
            raise AssertionError(f"범위 판정 오류: {item} -> {result}")
        if expected_topic not in result.get("matchedTopics", []):
            raise AssertionError(f"주제 판정 오류: {item} -> {result}")

    krx = classify_item(
        {"section": "krx_notice", "title": "시스템 점검 안내", "summary": "", "source": "한국거래소"},
        taxonomy,
    )
    if not krx or krx.get("keywordScope") != "direct":
        raise AssertionError("한국거래소 배출권 전용게시판 기본 직접성 판정 실패")
    print("38개 정책 레이더 주제 분류기 자체검증 완료")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--climate-history", type=Path, default=DEFAULT_CLIMATE)
    parser.add_argument("--climate-summary", type=Path, default=DEFAULT_CLIMATE_SUMMARY)
    parser.add_argument("--motie-history", type=Path, default=DEFAULT_MOTIE)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    result = apply_taxonomy(
        taxonomy_path=args.taxonomy,
        policy_path=args.policy,
        climate_path=args.climate_history,
        motie_path=args.motie_history,
        climate_summary_path=args.climate_summary,
        summary_path=args.summary,
    )
    print(
        "정책 레이더 38개 주제 적용 완료: "
        f"공식자료 {result['officialItemCount']:,}건 / "
        f"직접 {result['scopeCounts'].get('direct', 0):,}건 / "
        f"연관 {result['scopeCounts'].get('related', 0):,}건"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
