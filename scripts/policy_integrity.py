#!/usr/bin/env python3
"""Compare accepted official posts by publisher identity, never by row counts."""
from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit

CLIMATE_HOSTS = {"mcee.go.kr", "me.go.kr"}
INDUSTRY_HOSTS = {"motir.go.kr", "motie.go.kr"}


def classified_section(item: dict) -> str:
    """Use the publisher's board to correct cross-ministry labels."""
    if publisher(item) in INDUSTRY_HOSTS:
        match = re.search(r"/kor/article/([^/]+)/", str(item.get("url", "")))
        board = match.group(1) if match else item.get("sourceBoard")
        if board:
            return "motie_press" if board in {"ATCL3f49a5a8c", "ATCLe0854704d"} else "motie_notice"
    return str(item.get("section") or "")


def publisher(item: dict) -> str:
    host = (urlsplit(html.unescape(str(item.get("url") or ""))).hostname or "").lower()
    return host.removeprefix("www.")


def official_key(item: dict) -> str | None:
    if str(item.get("sourceType", "")).lower() == "news" or item.get("section") == "news":
        return None
    url = html.unescape(str(item.get("url") or "").strip())
    parsed = urlsplit(url)
    host = publisher(item)
    query = {key.lower(): value for key, value in parse_qs(parsed.query).items()}
    if host in CLIMATE_HOSTS and query.get("boardid"):
        return "climate|" + query["boardid"][0]
    if host in INDUSTRY_HOSTS:
        match = re.search(r"/kor/article/([^/]+)/(\d+)(?:/|$)", parsed.path)
        if match:
            return "industry|" + "|".join(match.groups())
        if item.get("sourceBoard") and item.get("sourceId"):
            return f"industry|{item['sourceBoard']}|{item['sourceId']}"
    if host == "ets.krx.co.kr" or item.get("section") == "krx_notice":
        match = re.search(r"(?:view=|krx-ets-)(\d+)", url + " " + str(item.get("id", "")))
        identifier = match.group(1) if match else str(item.get("sourceId") or "")
        if identifier:
            return "krx|" + identifier
    if url:
        return "url|" + urlunsplit(("https", host, parsed.path, parsed.query, parsed.fragment))
    identifier = str(item.get("id") or "").strip()
    if identifier:
        return f"id|{item.get('source', '')}|{identifier}"
    raise RuntimeError("공식 게시물의 URL과 ID가 모두 없습니다.")


def identities(items: list[dict]) -> set[str]:
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise RuntimeError("공식자료 items 형식 오류")
    return {key for item in items if (key := official_key(item)) is not None}


def assert_preserved(before: list[dict], after: list[dict]) -> dict:
    previous, current = identities(before), identities(after)
    missing = sorted(previous - current)
    if missing:
        raise RuntimeError(f"공식 게시물 고유 ID {len(missing)}건 누락: {missing[:20]}")
    return {"previousUnique": len(previous), "currentUnique": len(current), "missing": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, default=Path("public/data/policies.json"))
    args = parser.parse_args()
    before = json.loads(args.before.read_text(encoding="utf-8"))["items"]
    after = json.loads(args.after.read_text(encoding="utf-8"))["items"]
    print("OFFICIAL_ID_RETENTION=" + json.dumps(assert_preserved(before, after)))


if __name__ == "__main__":
    main()
