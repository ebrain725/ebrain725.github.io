#!/usr/bin/env python3
"""Policy collector compatibility module with append-only news retention.

The existing GitHub Actions workflow is intentionally left unchanged: it still
executes this file and commits ``policies.json``. Before the core collector runs,
this wrapper snapshots every previously accepted news item. After collection it
merges that snapshot, the committed recovery archive, and newly collected news
back into ``policies.json``. Therefore older news cannot disappear when relevance
rules or source responses change, while the workflow's approved output-file list
remains valid.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from sync_policies_core import *  # noqa: F401,F403
import sync_policies_core as _core
import news_retention as _retention


def _load_document(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return document if isinstance(document, dict) else {}


def _news_rows(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in document.get("items", [])
        if _retention.is_valid_news(item)
    ]


def _retention_snapshot() -> list[dict[str, Any]]:
    """Return the union candidates available before the core collector runs."""
    rows: list[dict[str, Any]] = []
    rows.extend(_news_rows(_load_document(_retention.HISTORY_PATH)))
    rows.extend(_news_rows(_load_document(_retention.POLICY_PATH)))
    return rows


def main() -> int:
    before_rows = _retention_snapshot()
    result = _core.main()
    if result not in (None, 0):
        return int(result)

    # Use temporary archive/audit files during routine collection. Only
    # policies.json is changed, so the pre-existing Actions allow-list remains
    # valid. The committed 524-item recovery archive remains a safety baseline.
    with tempfile.TemporaryDirectory(prefix="ets-news-retention-") as directory:
        temporary_root = Path(directory)
        temporary_history = temporary_root / "news-history.json"
        temporary_audit = temporary_root / "news-retention-audit.json"
        temporary_history.write_text(
            json.dumps(
                {
                    "schemaVersion": "1.1",
                    "retentionPolicy": "append-only",
                    "source": "pre-collection policies + committed recovery archive",
                    "itemCount": len(before_rows),
                    "items": before_rows,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        audit = _retention.run(
            _retention.POLICY_PATH,
            temporary_history,
            temporary_audit,
            _retention.SETTINGS_PATH,
            recover=False,
            maximum_commits=1,
        )

    final_count = int(audit["counts"]["finalNewsCount"])
    if final_count < len(before_rows):
        raise RuntimeError(
            f"append-only 뉴스 감소 감지: 최종 {final_count}건 < 사전 후보 {len(before_rows)}건"
        )
    print(
        "APPEND_ONLY_NEWS_RETAINED="
        f"{final_count} before_candidates={len(before_rows)} "
        f"new_or_enriched={max(0, final_count - len(before_rows))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
