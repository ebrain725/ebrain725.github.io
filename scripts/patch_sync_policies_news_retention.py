#!/usr/bin/env python3
"""Patch sync-policies.yml to enforce append-only news retention."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / ".github" / "workflows" / "sync-policies.yml"


def patch(path: Path) -> bool:
    original = path.read_text(encoding="utf-8")
    text = original

    if "python scripts/sync_policies_retained.py" not in text:
        pattern = re.compile(r"(?m)^(\s*)python scripts/sync_policies\.py\s*$")
        text, count = pattern.subn(
            lambda match: f"{match.group(1)}python scripts/sync_policies_retained.py",
            text,
            count=1,
        )
        if count != 1:
            raise RuntimeError(f"정책 수집 실행문을 고유하게 찾지 못했습니다: {count}")

    if "test -f scripts/news_retention.py" not in text:
        anchor = "          test -f scripts/sync_policies.py || {\n"
        addition = (
            "          test -f scripts/news_retention.py || {\n"
            "            echo \"::error::scripts/news_retention.py 파일을 찾지 못했습니다.\"\n"
            "            exit 1\n"
            "          }\n"
            "          test -f scripts/sync_policies_retained.py || {\n"
            "            echo \"::error::scripts/sync_policies_retained.py 파일을 찾지 못했습니다.\"\n"
            "            exit 1\n"
            "          }\n"
        )
        if anchor not in text:
            raise RuntimeError("수집기 파일검사 삽입 위치를 찾지 못했습니다.")
        text = text.replace(anchor, addition + anchor, 1)

    old_tuple = '("sync_policies.py", "sync_bills.py", "sync_assembly_seminars.py")'
    new_tuple = '("sync_policies.py", "news_retention.py", "sync_policies_retained.py", "sync_bills.py", "sync_assembly_seminars.py")'
    if new_tuple not in text:
        if old_tuple not in text:
            raise RuntimeError("수집기 문법검사 목록을 찾지 못했습니다.")
        text = text.replace(old_tuple, new_tuple, 1)

    text = text.replace(
        "if output.stat().st_size > 5_000_000:",
        "if output.stat().st_size > 35_000_000:",
        1,
    )
    text = text.replace(
        "policies.json이 5MB를 초과해 비정상으로 판단했습니다.",
        "policies.json이 35MB를 초과해 비정상으로 판단했습니다.",
        1,
    )

    if "뉴스 보관파일 건수 확인" not in text:
        anchor = '          print(f"정책 검증 완료: 전체 {len(payload[\'items\'])}건, 뉴스 {news_count}건")\n'
        addition = (
            '          history_path = Path("public/data/news-history.json")\n'
            '          audit_path = Path("public/data/news-retention-audit.json")\n'
            '          if not history_path.is_file() or not audit_path.is_file():\n'
            '              raise SystemExit("append-only 뉴스 보관파일 또는 감사파일이 없습니다.")\n'
            '          news_history = json.loads(history_path.read_text(encoding="utf-8"))\n'
            '          news_audit = json.loads(audit_path.read_text(encoding="utf-8"))\n'
            '          archive_count = int(news_history.get("itemCount", -1))\n'
            '          audited_count = int((news_audit.get("counts") or {}).get("finalNewsCount", -1))\n'
            '          if news_count != archive_count or news_count != audited_count:\n'
            '              raise SystemExit(\n'
            '                  f"뉴스 보관 건수 불일치: policies={news_count}, archive={archive_count}, audit={audited_count}"\n'
            '              )\n'
            '          print(f"뉴스 보관파일 건수 확인: {archive_count}건")\n'
        )
        if anchor not in text:
            raise RuntimeError("뉴스 검증 삽입 위치를 찾지 못했습니다.")
        text = text.replace(anchor, anchor + addition, 1)

    if "public/data/news-history.json" not in text.split("git add --", 1)[-1]:
        anchor = "            public/data/policies.json \\\n"
        addition = (
            "            public/data/news-history.json \\\n"
            "            public/data/news-retention-audit.json \\\n"
        )
        if anchor not in text:
            raise RuntimeError("git add 목록 삽입 위치를 찾지 못했습니다.")
        text = text.replace(anchor, anchor + addition, 1)

    required_markers = (
        "python scripts/sync_policies_retained.py",
        "test -f scripts/news_retention.py",
        "news_retention.py\", \"sync_policies_retained.py",
        "if output.stat().st_size > 35_000_000:",
        "뉴스 보관파일 건수 확인",
        "public/data/news-history.json",
        "public/data/news-retention-audit.json",
    )
    missing = [marker for marker in required_markers if marker not in text]
    if missing:
        raise RuntimeError(f"영구 패치 검증 실패: {missing}")

    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed = patch(args.path)
    if args.check and changed:
        raise SystemExit("검사 모드에서 추가 변경이 발생했습니다.")
    print(f"SYNC_POLICIES_NEWS_RETENTION_PATCH={'CHANGED' if changed else 'UNCHANGED'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
