#!/usr/bin/env python3
"""Policy collector compatibility module with append-only news retention."""
from __future__ import annotations

from sync_policies_core import *  # noqa: F401,F403
import sync_policies_core as _core
import news_retention as _retention


def main() -> int:
    result = _core.main()
    if result not in (None, 0):
        return int(result)
    audit = _retention.run(
        _retention.POLICY_PATH,
        _retention.HISTORY_PATH,
        _retention.AUDIT_PATH,
        _retention.SETTINGS_PATH,
        recover=False,
        maximum_commits=1,
    )
    print(
        "APPEND_ONLY_NEWS_RETAINED="
        + str(audit["counts"]["finalNewsCount"])
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
