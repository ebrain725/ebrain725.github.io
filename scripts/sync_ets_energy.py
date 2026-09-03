#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.ets_fundamentals.core import (  # noqa: E402
    CollectorError,
    NormalizedDataset,
    SourceRun,
    archive_raw_payload,
    iso_kst,
    load_json,
    parse_iso_date,
    publish_dataset,
    run_id_for,
    validate_public_dataset,
)
from scripts.ets_fundamentals.kpx_supply import (  # noqa: E402
    FetchResult,
    KpxSupplyTodayAdapter,
    detect_response_format,
)

KST = ZoneInfo("Asia/Seoul")
DEFAULT_CONFIG = REPO_ROOT / "config" / "ets_energy_sources.json"
DEFAULT_PUBLIC_ROOT = REPO_ROOT / "public" / "data" / "fundamentals" / "power-energy" / "raw"
DEFAULT_ARCHIVE_ROOT = REPO_ROOT / "source-data" / "ets-energy" / "raw"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect independent ETS electricity and energy source data.",
    )
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        help="Source id to run. May be supplied more than once.",
    )
    parser.add_argument(
        "--expected-date",
        help=(
            "Optional response-date guard in YYYY-MM-DD. This is validation only; "
            "the active KPX API does not provide historical-date queries."
        ),
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--public-root", type=Path, default=DEFAULT_PUBLIC_ROOT)
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument(
        "--fixture",
        type=Path,
        help="Read one local KPX JSON/XML fixture instead of calling the API.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate already-published raw data and exit.",
    )
    return parser


def _load_config(path: Path) -> Mapping[str, Any]:
    config = load_json(path, None)
    if not isinstance(config, Mapping):
        raise CollectorError(f"Config must be a JSON object: {path}")
    if config.get("category") != "power_energy":
        raise CollectorError("Config category must be 'power_energy'")
    sources = config.get("sources")
    if not isinstance(sources, list) or not sources:
        raise CollectorError("Config must contain a non-empty sources list")
    source_ids = [str(value.get("id", "")) for value in sources if isinstance(value, Mapping)]
    if len(source_ids) != len(set(source_ids)):
        raise CollectorError("Source ids must be unique")
    if any(not source_id for source_id in source_ids):
        raise CollectorError("Source ids must not be empty")
    return config


def _selected_sources(
    config: Mapping[str, Any], requested_ids: Sequence[str] | None
) -> list[Mapping[str, Any]]:
    sources = [value for value in config["sources"] if isinstance(value, Mapping)]
    if requested_ids:
        requested = set(requested_ids)
        selected = [value for value in sources if str(value.get("id")) in requested]
        missing = sorted(requested - {str(value.get("id")) for value in selected})
        if missing:
            raise CollectorError(f"Unknown source id(s): {', '.join(missing)}")
        return selected
    return [value for value in sources if bool(value.get("enabled", False))]


def _fixture_fetch(path: Path) -> FetchResult:
    payload = path.read_bytes()
    response_format = detect_response_format(payload)
    return FetchResult(
        payload=payload,
        response_format=response_format,
        endpoint_id="local_fixture",
        content_type=(
            "application/json" if response_format == "json" else "application/xml"
        ),
        request_variant="fixture",
    )


def _repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def run_collection(args: argparse.Namespace) -> list[dict[str, Any]]:
    config = _load_config(args.config)
    selected = _selected_sources(config, args.sources)
    if not selected:
        raise CollectorError("No enabled source selected")
    if args.fixture and len(selected) != 1:
        raise CollectorError("--fixture requires exactly one selected source")
    expected_date = parse_iso_date(args.expected_date) if args.expected_date else None

    results: list[dict[str, Any]] = []
    for source in selected:
        adapter_name = str(source.get("adapter", ""))
        if adapter_name != "kpx_supply_today_5m":
            raise CollectorError(f"Unsupported adapter: {adapter_name}")

        adapter = KpxSupplyTodayAdapter(source)
        collected_at = datetime.now(KST).replace(microsecond=0)
        if args.fixture:
            fetch_result = _fixture_fetch(args.fixture)
        else:
            secret_env = str(source.get("secret_env", "KPX_OPENAPI_SERVICE_KEY"))
            fetch_result = adapter.fetch(service_key=os.environ.get(secret_env, ""))
        parsed = adapter.parse(fetch_result, expected_date=expected_date)

        raw_relative, raw_sha256 = archive_raw_payload(
            archive_root=args.archive_root,
            source_id=adapter.spec.source_id,
            observation_date=parsed.observation_date,
            response_format=fetch_result.response_format,
            payload=fetch_result.payload,
            metadata={
                "source_id": adapter.spec.source_id,
                "source_name": adapter.spec.source_name,
                "organization": adapter.spec.organization,
                "observation_date": parsed.observation_date.isoformat(),
                "collected_at": iso_kst(collected_at),
                "endpoint_id": fetch_result.endpoint_id,
                "request_variant": fetch_result.request_variant,
                "response_format": fetch_result.response_format,
                "content_type": fetch_result.content_type,
                "response_record_count": parsed.response_record_count,
                "normalized_record_count": len(parsed.records),
            },
        )
        raw_path = args.archive_root / raw_relative
        source_run = SourceRun(
            collected_at=iso_kst(collected_at),
            run_id=run_id_for(collected_at),
            endpoint_id=fetch_result.endpoint_id,
            request_variant=fetch_result.request_variant,
            response_format=fetch_result.response_format,
            raw_archive_path=_repo_relative(raw_path),
            raw_sha256=raw_sha256,
            response_record_count=parsed.response_record_count,
            normalized_record_count=len(parsed.records),
            warnings=parsed.warnings,
        )
        published = publish_dataset(
            public_root=args.public_root,
            dataset=NormalizedDataset(
                spec=adapter.spec,
                observation_date=parsed.observation_date,
                records=parsed.records,
                source_run=source_run,
            ),
        )
        results.append(
            {
                "source_id": adapter.spec.source_id,
                "observation_date": parsed.observation_date.isoformat(),
                "response_record_count": parsed.response_record_count,
                "normalized_record_count": len(parsed.records),
                "published_record_count": published["record_count"],
                "warnings": list(parsed.warnings),
                "raw_sha256": raw_sha256,
                "daily_json": published["json"],
                "daily_csv": published["csv"],
            }
        )
    return results


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.validate_only:
            result: Any = validate_public_dataset(public_root=args.public_root)
        else:
            result = run_collection(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (CollectorError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
