#!/usr/bin/env python3
"""Publish taxonomy and Excel from the same policy list that the dashboard reads."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

from export_policy_radar_excel import TABS, load
from export_policy_radar_excel_compatible import TEMPLATE_B64, generate, policy_source
from sync_policy_event_taxonomy import (
    ALL_OFFICIAL_SECTIONS,
    KST,
    POLICY_PATH,
    TAXONOMY_PATH,
    atomic_write,
    coverage,
    enrich_existing,
    event_counts,
    is_classified,
    load_taxonomy,
    record_key,
    scope_counts,
    section_scope_counts,
    unique_section_counts,
)

POLICIES = "policies.json"
SUMMARY = "policy-event-taxonomy-summary.json"
MANIFEST = "policy-radar-excel.json"
WORKBOOK = "ets-policy-radar-events.xlsx"
FIXED_WORKBOOK = "ets-policy-radar-events-fixed.xlsx"


def publish_snapshot(data_dir: Path, taxonomy_path: Path, template_path: Path) -> dict:
    policy_path = data_dir / POLICIES
    summary_path = data_dir / SUMMARY
    policy = load(policy_path)
    taxonomy = load_taxonomy(taxonomy_path)
    summary = load(summary_path) if summary_path.exists() else {}

    official = enrich_existing(policy["items"], taxonomy, sections=ALL_OFFICIAL_SECTIONS)
    by_identity = {record_key(item): item for item in official}
    if len(by_identity) != len(official):
        raise RuntimeError("공개 정책 목록에 같은 게시 기관의 원문 ID가 중복됩니다.")
    for name in ("policy-official-history.json", "motie-policy-history.json"):
        history = load(data_dir / name)
        missing = {record_key(item) for item in history["items"]} - by_identity.keys()
        if missing:
            raise RuntimeError(f"{name} 원문 ID {len(missing)}건이 공개 정책 목록에 없습니다.")

    policy["items"] = [
        by_identity[record_key(item)] if item.get("section") in ALL_OFFICIAL_SECTIONS else item
        for item in policy["items"]
    ]
    published_counts = dict(unique_section_counts(official))
    retained = sorted(record_key(item) for item in official if not is_classified(item))
    taxonomy_counts = event_counts(official)
    metadata = dict(policy.get("policyEventTaxonomy") or {})
    metadata.update({
        "version": taxonomy["version"],
        "configFile": "config/policy_event_taxonomy.json",
        "summaryFile": f"data/{SUMMARY}",
        "eventCount": len(taxonomy["events"]),
        "officialItemCount": len(official),
        "taxonomyMatchedItemCount": len(official) - len(retained),
        "retainedUnmatchedItemCount": len(retained),
        "scopeCounts": scope_counts(official),
        "eventCounts": taxonomy_counts,
    })
    policy["policyEventTaxonomy"] = metadata
    atomic_write(policy_path, policy)
    source = policy_source(policy, policy_path.read_bytes())

    previous = summary.get("previousSectionCounts") or published_counts
    summary.update({
        "schemaVersion": summary.get("schemaVersion", "1.0"),
        "taxonomyVersion": taxonomy["version"],
        "eventCount": len(taxonomy["events"]),
        "events": [
            {"id": item["id"], "label": item["label"], "priority": item.get("priority")}
            for item in taxonomy["events"]
        ],
        "sectionCounts": published_counts,
        "previousSectionCounts": previous,
        "addedCounts": {name: count - previous.get(name, 0) for name, count in published_counts.items()},
        "scopeCounts": section_scope_counts(official),
        "eventCounts": taxonomy_counts,
        "sourceCoverage": coverage(official, ALL_OFFICIAL_SECTIONS),
        "publicationVerifiedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "policySource": source,
        "validation": {
            "officialItemCount": len(official),
            "dashboardOfficialCount": len(official),
            "sectionCounts": published_counts,
            "scopeCounts": section_scope_counts(official),
            "eventCounts": taxonomy_counts,
            "duplicateKeys": 0,
            "taxonomyMatchedItemCount": len(official) - len(retained),
            "retainedUnmatchedItemCount": len(retained),
            "retainedUnmatchedKeys": retained,
            "unclassified": 0,
        },
    })
    atomic_write(summary_path, summary)
    generate(
        policy_path, data_dir / "bills.json", data_dir / "assembly_seminars.json",
        data_dir / WORKBOOK, data_dir / FIXED_WORKBOOK, data_dir / MANIFEST,
        template_path,
    )
    return verify_snapshot(data_dir)


def verify_snapshot(data_dir: Path) -> dict:
    policy_raw = (data_dir / POLICIES).read_bytes()
    policy = json.loads(policy_raw)
    summary = load(data_dir / SUMMARY)
    manifest = load(data_dir / MANIFEST)
    source = policy_source(policy, policy_raw)
    if source != summary.get("policySource") or source != manifest.get("policySource"):
        raise RuntimeError("정책 목록·분류 요약·엑셀의 원본 SHA-256이 다릅니다.")
    official = [item for item in policy["items"] if item.get("section") in ALL_OFFICIAL_SECTIONS]
    expected_counts = unique_section_counts(official)
    if len({record_key(item) for item in official}) != len(official):
        raise RuntimeError("공개 정책 목록에 같은 게시 기관의 원문 ID가 중복됩니다.")
    if summary.get("sectionCounts") != dict(expected_counts) or any(
        manifest["counts"].get(TABS[name]) != count for name, count in expected_counts.items()
    ):
        raise RuntimeError("정책 목록·분류 요약·엑셀의 공식자료 구분별 건수가 다릅니다.")
    if summary.get("eventCounts") != event_counts(official) or summary.get("scopeCounts") != section_scope_counts(official):
        raise RuntimeError("공개 정책 목록과 분류 요약의 이벤트·직접성 집계가 다릅니다.")
    for filename in (WORKBOOK, FIXED_WORKBOOK):
        checksum = hashlib.sha256((data_dir / filename).read_bytes()).hexdigest()
        if checksum != manifest.get("fileSha256"):
            raise RuntimeError(f"공개 엑셀 SHA-256 불일치: {filename}")
    return source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=POLICY_PATH.parent)
    parser.add_argument("--taxonomy", type=Path, default=TAXONOMY_PATH)
    parser.add_argument("--template-b64", type=Path, default=TEMPLATE_B64)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    source = (
        verify_snapshot(args.data_dir) if args.verify_only
        else publish_snapshot(args.data_dir, args.taxonomy, args.template_b64)
    )
    print(f"POLICY_SNAPSHOT_VERIFIED official={source['officialItemCount']} sha256={source['sha256']}")


if __name__ == "__main__":
    main()
