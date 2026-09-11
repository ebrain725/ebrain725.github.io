#!/usr/bin/env python3
"""Preserve policy-radar news as an append-only archive.

The daily collector may change its relevance filters over time. Previously accepted
news must not disappear merely because a later run re-evaluates the current
``policies.json`` with a narrower filter. This module merges the current dataset,
the persistent news archive, and (when requested) historical Git snapshots.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import tempfile
import unicodedata
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "config" / "settings.json"
POLICY_PATH = ROOT / "public" / "data" / "policies.json"
HISTORY_PATH = ROOT / "public" / "data" / "news-history.json"
AUDIT_PATH = ROOT / "public" / "data" / "news-retention-audit.json"
KST = timezone(timedelta(hours=9))

TRACKING_KEYS = {
    "fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "ref", "referrer",
    "source", "spm", "ved", "usg", "ocid", "cmpid", "campaign",
}
PLACEHOLDER_MARKERS = (
    "api 연결 필요", "api unavailable", "수집 실패", "연결 대기",
)
LIST_FIELDS = {
    "matchedKeywords", "matchedFields", "eventTypes", "eventTypeIds",
    "eventCategories", "eventCategoryIds", "topicIds", "matchedTopics",
    "topicGroups", "keywords", "topics",
}
LONG_TEXT_FIELDS = {"summary", "description", "evidence", "content"}


def clean_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(str(value or "")))
    return re.sub(r"\s+", " ", text).strip()


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


def load_json(path: Path, *, required: bool = True) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise RuntimeError(f"필수 JSON 파일이 없습니다: {path}")
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise RuntimeError(f"JSON 최상위 값이 객체가 아닙니다: {path}")
    return document


def is_news(item: Any) -> bool:
    return isinstance(item, dict) and (
        clean_text(item.get("sourceType")).lower() == "news"
        or clean_text(item.get("section")).lower() == "news"
    )


def is_valid_news(item: Any) -> bool:
    if not is_news(item):
        return False
    title = clean_text(item.get("title"))
    url = clean_text(item.get("url"))
    if not title or not url:
        return False
    lower = title.lower()
    if item.get("isPlaceholder") or item.get("placeholder"):
        return False
    if any(marker in lower for marker in PLACEHOLDER_MARKERS):
        return False
    return True


def canonical_url(value: Any) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError:
        return raw
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return raw
    host = parsed.hostname.lower().removeprefix("www.")
    port = f":{parsed.port}" if parsed.port else ""
    query = []
    for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower.startswith("utm_") or key_lower in TRACKING_KEYS:
            continue
        query.append((key, value))
    query.sort()
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urllib.parse.urlunsplit(
        (parsed.scheme.lower(), f"{host}{port}", path, urllib.parse.urlencode(query, doseq=True), "")
    )


def normalized_title(value: Any) -> str:
    title = clean_text(value).lower()
    for _ in range(3):
        title = re.sub(r"^\s*[\[【(][^\]】)]{1,40}[\]】)]\s*", "", title)
    title = re.sub(r"\s*[-|｜]\s*[^-|｜]{1,30}$", "", title)
    return re.sub(r"[^0-9a-z가-힣]+", "", title)[:300]


def item_keys(item: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    url = canonical_url(item.get("url"))
    published = clean_text(item.get("publishedAt") or item.get("date"))[:10]
    title = normalized_title(item.get("title"))
    if url:
        keys.append(f"url|{url}")
    if published and title:
        keys.append(f"date-title|{published}|{title}")
    return list(dict.fromkeys(keys))


def list_values(value: Any) -> list[str]:
    if isinstance(value, list):
        values = value
    elif value in (None, ""):
        values = []
    else:
        values = [value]
    result: list[str] = []
    for raw in values:
        text = clean_text(raw)
        if text and text not in result:
            result.append(text)
    return result


def merge_items(previous: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    merged = dict(previous)
    for key, value in candidate.items():
        if key in LIST_FIELDS:
            combined = [*list_values(merged.get(key)), *list_values(value)]
            merged[key] = list(dict.fromkeys(combined))
            continue
        if key in LONG_TEXT_FIELDS:
            if len(clean_text(value)) > len(clean_text(merged.get(key))):
                merged[key] = value
            continue
        if value not in (None, "", [], {}):
            merged[key] = value
    merged["sourceType"] = "news"
    merged["section"] = "news"
    merged["url"] = clean_text(merged.get("url"))
    return merged


class NewsIndex:
    def __init__(self) -> None:
        self.items: list[dict[str, Any] | None] = []
        self.index: dict[str, int] = {}

    def add(self, raw: Any) -> bool:
        if not is_valid_news(raw):
            return False
        item = dict(raw)
        item["sourceType"] = "news"
        item["section"] = "news"
        keys = item_keys(item)
        matches = sorted({self.index[key] for key in keys if key in self.index})
        if not matches:
            position = len(self.items)
            self.items.append(item)
        else:
            position = matches[0]
            existing = self.items[position] or {}
            self.items[position] = merge_items(existing, item)
            for duplicate_position in matches[1:]:
                duplicate = self.items[duplicate_position]
                if duplicate is not None:
                    self.items[position] = merge_items(self.items[position] or {}, duplicate)
                    self.items[duplicate_position] = None
            item = self.items[position] or item
        for key in item_keys(item):
            self.index[key] = position
        return True

    def values(self) -> list[dict[str, Any]]:
        return [item for item in self.items if item is not None]


def news_items(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in document.get("items", []) if is_valid_news(item)]


def git_json(commit: str, path: str) -> dict[str, Any] | None:
    completed = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout:
        return None
    try:
        document = json.loads(completed.stdout.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return document if isinstance(document, dict) else None


def recover_git_history(index: NewsIndex, maximum_commits: int) -> dict[str, Any]:
    paths = ["public/data/news-history.json", "public/data/policies.json"]
    completed = subprocess.run(
        [
            "git", "log", f"--max-count={maximum_commits}", "--format=%H", "--",
            *paths,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    commits = list(dict.fromkeys(line.strip() for line in completed.stdout.splitlines() if line.strip()))
    snapshots = 0
    historical_max = 0
    valid_rows = 0
    before = len(index.values())
    for commit_number, commit in enumerate(commits, 1):
        for path in paths:
            document = git_json(commit, path)
            if document is None:
                continue
            snapshots += 1
            rows = news_items(document)
            historical_max = max(historical_max, len(rows))
            valid_rows += len(rows)
            for item in rows:
                index.add(item)
        if commit_number % 25 == 0:
            print(
                f"뉴스 Git 이력 복구: {commit_number}/{len(commits)}개 커밋, "
                f"고유 {len(index.values()):,}건",
                flush=True,
            )
    return {
        "sourceCommitsScanned": len(commits),
        "sourceSnapshotsScanned": snapshots,
        "historicalRowsScanned": valid_rows,
        "historicalMaxSnapshotCount": historical_max,
        "recoveredFromGitHistory": len(index.values()) - before,
    }


def published_date(item: dict[str, Any]) -> str:
    return clean_text(item.get("publishedAt") or item.get("date"))[:10]


def sort_news(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            published_date(item),
            clean_text(item.get("title")),
            canonical_url(item.get("url")),
        ),
        reverse=True,
    )


def run(
    policy_path: Path,
    history_path: Path,
    audit_path: Path,
    settings_path: Path,
    *,
    recover: bool,
    maximum_commits: int,
) -> dict[str, Any]:
    settings = load_json(settings_path)
    policy = load_json(policy_path)
    history = load_json(history_path, required=False)

    current_rows = news_items(policy)
    archive_rows = news_items(history)
    index = NewsIndex()
    for item in archive_rows:
        index.add(item)
    archive_unique = len(index.values())
    for item in current_rows:
        index.add(item)
    current_archive_union = len(index.values())

    recovery = {
        "sourceCommitsScanned": 0,
        "sourceSnapshotsScanned": 0,
        "historicalRowsScanned": 0,
        "historicalMaxSnapshotCount": 0,
        "recoveredFromGitHistory": 0,
    }
    if recover:
        recovery = recover_git_history(index, maximum_commits)

    final_news = sort_news(index.values())
    minimum = max(
        len(current_rows),
        len(archive_rows),
        int(recovery.get("historicalMaxSnapshotCount", 0)),
    )
    allow_decrease = bool(settings.get("allowNewsDecrease", False))
    if not allow_decrease and len(final_news) < minimum:
        raise RuntimeError(
            f"append-only 뉴스 감소 감지: 최종 {len(final_news)}건 < 안전 하한 {minimum}건"
        )

    non_news = [item for item in policy.get("items", []) if not is_news(item)]
    policy["items"] = sorted(
        [*non_news, *final_news],
        key=lambda item: (
            clean_text(item.get("publishedAt") or item.get("date"))[:10],
            clean_text(item.get("title")),
        ),
        reverse=True,
    )
    now = datetime.now(KST).isoformat(timespec="seconds")
    earliest = min((published_date(item) for item in final_news if published_date(item)), default="")
    latest = max((published_date(item) for item in final_news if published_date(item)), default="")
    policy["newsRetention"] = {
        "policy": "append-only",
        "lastMergedAt": now,
        "itemCount": len(final_news),
        "archiveBefore": len(archive_rows),
        "currentBefore": len(current_rows),
        "gitRecoveryEnabled": recover,
    }

    history_document = {
        "schemaVersion": "1.1",
        "generatedAt": now,
        "retentionPolicy": "append-only",
        "source": "policies.json + persistent archive + Git history recovery",
        "itemCount": len(final_news),
        "earliestPublishedAt": earliest,
        "latestPublishedAt": latest,
        **recovery,
        "items": final_news,
    }
    audit = {
        "schemaVersion": "1.0",
        "generatedAt": now,
        "status": "verified",
        "retentionPolicy": "append-only",
        "counts": {
            "currentPoliciesNewsBefore": len(current_rows),
            "archiveRowsBefore": len(archive_rows),
            "archiveUniqueBefore": archive_unique,
            "currentArchiveUnion": current_archive_union,
            "finalNewsCount": len(final_news),
            "nonNewsCount": len(non_news),
            "policyItemsAfter": len(policy["items"]),
        },
        "coverage": {
            "earliestPublishedAt": earliest,
            "latestPublishedAt": latest,
        },
        "safetyFloor": minimum,
        "allowNewsDecrease": allow_decrease,
        **recovery,
        "sha256": {
            "newsItems": hashlib.sha256(
                json.dumps(final_news, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
        },
    }

    atomic_write(policy_path, policy)
    atomic_write(history_path, history_document)
    atomic_write(audit_path, audit)
    return audit


def self_test() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        settings = {"allowNewsDecrease": False}
        policy = {
            "items": [
                {"id": "official", "sourceType": "official", "title": "정책", "publishedAt": "2026-01-01"},
                {"id": "n2", "sourceType": "news", "section": "news", "title": "둘째 뉴스", "publishedAt": "2026-02-02", "source": "언론", "url": "https://example.com/2?utm_source=x"},
            ]
        }
        history = {
            "items": [
                {"id": "n1", "sourceType": "news", "section": "news", "title": "첫 뉴스", "publishedAt": "2026-02-01", "source": "언론", "url": "https://example.com/1"},
                {"id": "n2-old", "sourceType": "news", "section": "news", "title": "둘째 뉴스", "publishedAt": "2026-02-02", "source": "언론", "url": "https://example.com/2"},
            ]
        }
        for path, document in ((root / "settings.json", settings), (root / "policies.json", policy), (root / "news.json", history)):
            path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        result = run(
            root / "policies.json", root / "news.json", root / "audit.json", root / "settings.json",
            recover=False, maximum_commits=0,
        )
        assert result["counts"]["finalNewsCount"] == 2
        assert len(load_json(root / "policies.json")["items"]) == 3
    print("NEWS_RETENTION_SELF_TEST=PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy-path", type=Path, default=POLICY_PATH)
    parser.add_argument("--history-path", type=Path, default=HISTORY_PATH)
    parser.add_argument("--audit-path", type=Path, default=AUDIT_PATH)
    parser.add_argument("--settings-path", type=Path, default=SETTINGS_PATH)
    parser.add_argument("--recover-git-history", action="store_true")
    parser.add_argument("--maximum-commits", type=int, default=300)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    result = run(
        args.policy_path, args.history_path, args.audit_path, args.settings_path,
        recover=args.recover_git_history, maximum_commits=max(1, args.maximum_commits),
    )
    print("NEWS_RETENTION_RESULT=" + json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
