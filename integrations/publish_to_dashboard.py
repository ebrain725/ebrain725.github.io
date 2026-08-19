#!/usr/bin/env python3
"""텔레그램 발송 결과 JSON을 별도 GitHub Pages 저장소에 게시한다."""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_VERSION = "2022-11-28"
TARGET_PATH = "public/data/briefing.json"


def request_json(url: str, token: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "ETS-SIGNAL-Briefing-Publisher/1.0",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return 404, {}
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API 오류 {exc.code}: {detail[:300]}") from exc


def normalize_payload(payload: dict) -> dict:
    if isinstance(payload.get("items"), list) and payload["items"]:
        payload = payload["items"][0]
    date = str(payload.get("date") or payload.get("briefingDate") or "").strip()
    title = str(payload.get("title") or "배출권 데일리 브리핑").strip()
    content = str(payload.get("content") or payload.get("message") or "").strip()
    if not date or not content:
        raise ValueError("브리핑 JSON에는 date(YYYY-MM-DD)와 content가 필요합니다.")
    return {
        "date": date,
        "title": title,
        "summary": str(payload.get("summary") or "").strip(),
        "content": content,
        "marketTone": str(payload.get("marketTone") or payload.get("market_tone") or "중립").strip(),
        "outlook": str(payload.get("outlook") or "").strip(),
        "source": str(payload.get("source") or "Telegram").strip(),
    }


def publish_briefing(payload: dict) -> str:
    token = os.environ.get("PAGES_REPO_TOKEN", "").strip()
    repository = os.environ.get("DASHBOARD_REPO", "ebrain725/ebrain725.github.io").strip()
    branch = os.environ.get("DASHBOARD_BRANCH", "main").strip()
    if not token:
        raise RuntimeError("GitHub Actions Secret PAGES_REPO_TOKEN이 없습니다.")
    item = normalize_payload(payload)
    encoded_path = urllib.parse.quote(TARGET_PATH, safe="/")
    api_url = f"https://api.github.com/repos/{repository}/contents/{encoded_path}"
    status, current = request_json(f"{api_url}?ref={urllib.parse.quote(branch)}", token)
    sha = current.get("sha") if status == 200 else None
    if sha:
        decoded = base64.b64decode(current.get("content", "")).decode("utf-8")
        document = json.loads(decoded)
        items = document.get("items", []) if isinstance(document, dict) else []
    else:
        items = []
    items = [item, *[old for old in items if str(old.get("date") or old.get("briefingDate")) != item["date"]]][:60]
    document = {"updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "items": items}
    update = {
        "message": f"data: publish briefing {item['date']}",
        "content": base64.b64encode((json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        update["sha"] = sha
    request_json(api_url, token, method="PUT", payload=update)
    return f"https://{repository.split('/', 1)[0]}.github.io/data/briefing.json"


def main() -> int:
    if len(sys.argv) != 2:
        print("사용법: python publish_to_dashboard.py briefing_payload.json", file=sys.stderr)
        return 2
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(publish_briefing(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
