from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.ets_fundamentals.core import (  # noqa: E402
    NormalizedDataset,
    SourceRun,
    archive_raw_payload,
    publish_dataset,
    validate_public_dataset,
)
from scripts.ets_fundamentals.kpx_supply import (  # noqa: E402
    KpxApiError,
    build_spec,
    parse_kpx_supply_payload,
)

KST = ZoneInfo("Asia/Seoul")
FIXTURES = Path(__file__).parent / "fixtures"
SOURCE_CONFIG = {
    "id": "kpx_supply_today_5m",
    "name_ko": "한국전력거래소 오늘 전력수급현황",
    "organization": "한국전력거래소",
    "dataset_page": "https://www.data.go.kr/data/15158703/openapi.do",
}


class KpxSupplyParserTest(unittest.TestCase):
    def test_parses_json_rows_and_preserves_12_digit_timestamp(self) -> None:
        parsed = parse_kpx_supply_payload(
            (FIXTURES / "kpx_supply_today_5m.json").read_bytes(),
            content_type="application/json",
        )
        self.assertEqual(parsed.observation_date, date(2026, 9, 2))
        self.assertEqual(parsed.response_record_count, 2)
        self.assertEqual(len(parsed.records), 2)
        self.assertEqual(parsed.records[0]["observed_at"], "2026-09-02T23:50:00+09:00")
        self.assertEqual(parsed.records[1]["observed_at"], "2026-09-02T23:55:00+09:00")
        self.assertEqual(parsed.records[1]["supply_capacity"], 105300.0)
        self.assertEqual(parsed.records[1]["forecast_peak_demand"], 71850.0)

    def test_parses_xml_row(self) -> None:
        parsed = parse_kpx_supply_payload(
            (FIXTURES / "kpx_supply_today_5m.xml").read_bytes(),
            content_type="application/xml",
        )
        self.assertEqual(parsed.response_record_count, 1)
        self.assertEqual(len(parsed.records), 1)
        self.assertEqual(parsed.records[0]["supply_reserve_rate"], 46.25)

    def test_rejects_wrong_expected_date(self) -> None:
        with self.assertRaises(KpxApiError):
            parse_kpx_supply_payload(
                (FIXTURES / "kpx_supply_today_5m.json").read_bytes(),
                content_type="application/json",
                expected_date=date(2026, 9, 1),
            )

    def test_spec_uses_official_units(self) -> None:
        spec = build_spec(SOURCE_CONFIG)
        units = {field.id: field.unit for field in spec.metrics}
        self.assertEqual(units["system_demand"], "MW")
        self.assertEqual(units["supply_reserve_rate"], "%")
        self.assertEqual(spec.expected_records_per_day, 288)


class PublicationTest(unittest.TestCase):
    def _source_run(self, *, raw_path: str, raw_sha: str, response_count: int) -> SourceRun:
        return SourceRun(
            collected_at="2026-09-03T00:01:00+09:00",
            run_id="20260903T000100+0900",
            endpoint_id="fixture",
            request_variant="fixture",
            response_format="json",
            raw_archive_path=raw_path,
            raw_sha256=raw_sha,
            response_record_count=response_count,
            normalized_record_count=response_count,
        )

    def test_later_partial_run_does_not_shrink_published_day(self) -> None:
        full = parse_kpx_supply_payload(
            (FIXTURES / "kpx_supply_today_5m.json").read_bytes(),
            content_type="application/json",
        )
        partial = parse_kpx_supply_payload(
            (FIXTURES / "kpx_supply_today_5m.xml").read_bytes(),
            content_type="application/xml",
        )
        spec = build_spec(SOURCE_CONFIG)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public_root = root / "public"
            archive_root = root / "archive"
            raw_relative, raw_sha = archive_raw_payload(
                archive_root=archive_root,
                source_id=spec.source_id,
                observation_date=full.observation_date,
                response_format="json",
                payload=(FIXTURES / "kpx_supply_today_5m.json").read_bytes(),
                metadata={"endpoint_id": "fixture"},
            )
            publish_dataset(
                public_root=public_root,
                dataset=NormalizedDataset(
                    spec=spec,
                    observation_date=full.observation_date,
                    records=full.records,
                    source_run=self._source_run(
                        raw_path=raw_relative.as_posix(),
                        raw_sha=raw_sha,
                        response_count=2,
                    ),
                ),
            )
            publish_dataset(
                public_root=public_root,
                dataset=NormalizedDataset(
                    spec=spec,
                    observation_date=partial.observation_date,
                    records=partial.records,
                    source_run=self._source_run(
                        raw_path=raw_relative.as_posix(),
                        raw_sha=raw_sha,
                        response_count=1,
                    ),
                ),
            )

            daily_path = (
                public_root
                / "kpx_supply_today_5m"
                / "daily"
                / "2026"
                / "2026-09-02.json"
            )
            daily = json.loads(daily_path.read_text(encoding="utf-8"))
            self.assertEqual(len(daily["records"]), 2)
            latest_row = daily["records"][-1]
            self.assertEqual(latest_row["supply_capacity"], 105301.0)
            self.assertEqual(daily["coverage"]["record_count"], 2)
            self.assertEqual(daily["coverage"]["expected_records_per_day"], 288)

            validation = validate_public_dataset(public_root=public_root)
            self.assertEqual(validation["source_count"], 1)
            self.assertEqual(validation["partition_count"], 1)
            self.assertEqual(validation["total_record_count"], 2)
            self.assertEqual(validation["latest_source_count"], 1)

    def test_cli_fixture_does_not_require_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = [
                sys.executable,
                str(REPO_ROOT / "scripts" / "sync_ets_energy.py"),
                "--fixture",
                str(FIXTURES / "kpx_supply_today_5m.xml"),
                "--expected-date",
                "2026-09-02",
                "--public-root",
                str(root / "public"),
                "--archive-root",
                str(root / "archive"),
            ]
            environment = dict(os.environ)
            environment.pop("KPX_OPENAPI_SERVICE_KEY", None)
            completed = subprocess.run(
                command,
                cwd=REPO_ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            output = json.loads(completed.stdout)
            self.assertEqual(output[0]["normalized_record_count"], 1)
            self.assertEqual(output[0]["published_record_count"], 1)


if __name__ == "__main__":
    unittest.main()
