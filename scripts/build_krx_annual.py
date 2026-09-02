#!/usr/bin/env python3
"""Build the 2015-2025 KRX annual company dashboard dataset.

The source archive is preserved outside the public site at
``source-data/krx-annual/Policy.zip``.  Generate the public JSON with:

    python scripts/build_krx_annual.py

An alternate archive or output path can be supplied with:

    python scripts/build_krx_annual.py \
      --input /path/to/Policy.zip \
      --output public/data/krx-annual.json

JSON contract (schemaVersion 1.1.0)
-----------------------------------
The top-level object contains ``source``, ``meta``, ``metrics``,
``classifications``, ``entities``, ``entityRelations``, ``rows`` and
``quality``.  ``rows`` is a flat company-year table.  Every row has the
following stable fields::

    {
      "year": 2025,
      "sector": "산업",
      "industry": "1차 철강 제조업",
      "companyId": "company-...",
      "companyName": "...",
      "aliases": ["..."],
      "relatedCompanyNames": ["..."],
      "entityRelations": [{"relationType": "...", "direction": "..."}],
      "rawClassifications": [{"sector": "...", "industry": "..."}],
      "allocationType": "Y" | "N" | null,
      "metrics": {
        "preAllocation": int | null,
        "additionalAllocation": int | null,
        "cancellation": int | null,
        "adjustedAllocation": int | null,
        "verifiedEmissions": int | null,
        "carryover": int | null,
        "carryoverAllowance": int | null,
        "carryoverOffset": int | null,
        "borrow": int | null,
        "offsetIssued": int | null,
        "finalBalance": int | null
      },
      "missing": {"metricName": bool, ...},
      "missingReason": {"metricName": "...", ...},
      "qualityFlags": ["..."]
    }

Missing and zero are deliberately different.  A company absent from a sparse
official list (additional allocation, cancellation, carryover, borrowing or
offset issuance) is zero.  Absence from a plan-period pre-allocation list is
also zero.  A genuinely blank source cell is null.  Verified emissions is the
core annual register, so a company-year without an emissions row is null.
Derived values are null whenever a required input is null.

Only safe legal-form spelling variants are consolidated automatically.  Raw
names and raw sector/industry labels remain available for audit.  Mergers and
spin-offs backed by the official relation registry are linked for search and
lineage display but never joined for metric aggregation.  Unverified fuzzy
name matches are intentionally not joined.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import tempfile
import unicodedata
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "source-data" / "krx-annual" / "Policy.zip"
DEFAULT_RELATIONS = ROOT / "source-data" / "krx-annual" / "entity-relations.json"
DEFAULT_OUTPUT = ROOT / "public" / "data" / "krx-annual.json"

SCHEMA_VERSION = "1.1.0"
BUILDER_VERSION = "1.1.0"
YEARS = tuple(range(2015, 2026))
YEAR_SET = set(YEARS)

BASE_METRICS = (
    "preAllocation",
    "additionalAllocation",
    "cancellation",
    "verifiedEmissions",
    "carryoverAllowance",
    "carryoverOffset",
    "borrow",
    "offsetIssued",
)
ALL_METRICS = (
    "preAllocation",
    "additionalAllocation",
    "cancellation",
    "adjustedAllocation",
    "verifiedEmissions",
    "carryover",
    "carryoverAllowance",
    "carryoverOffset",
    "borrow",
    "offsetIssued",
    "finalBalance",
)

SPARSE_ZERO_METRICS = {
    "preAllocation",
    "additionalAllocation",
    "cancellation",
    "carryoverAllowance",
    "carryoverOffset",
    "borrow",
    "offsetIssued",
}

CLASSIFICATION_PRIORITY = {
    "verifiedEmissions": 100,
    "preAllocation": 90,
    "additionalAllocation": 80,
    "cancellation": 80,
    "carryoverAllowance": 70,
    "carryoverOffset": 70,
    "borrow": 60,
    "offsetIssued": 50,
}

SECTOR_ORDER = (
    "전환",
    "산업",
    "건물",
    "수송",
    "폐기물",
    "공공·기타",
    "미분류",
)

STANDARD_SECTORS = {
    "전환",
    "산업",
    "건물",
    "수송",
    "폐기물",
    "공공·기타",
}

LEGACY_INDUSTRY_SECTORS = {
    "발전에너지": "전환",
    "발전·에너지": "전환",
    "집단에너지": "전환",
    "산업단지": "전환",
    "건물": "건물",
    "건물통신제외": "건물",
    "통신": "건물",
    "항공": "수송",
    "폐기물": "폐기물",
    "수도": "공공·기타",
    "광업": "산업",
    "기계": "산업",
    "디스플레이": "산업",
    "목재": "산업",
    "반도체": "산업",
    "비철금속": "산업",
    "석유화학": "산업",
    "섬유": "산업",
    "시멘트": "산업",
    "요업": "산업",
    "유리": "산업",
    "음식료품": "산업",
    "자동차": "산업",
    "전기전자": "산업",
    "정유": "산업",
    "제지": "산업",
    "조선": "산업",
    "철강": "산업",
}

INDUSTRY_TEXT_REPLACEMENTS = {
    "비알콜": "비알코올",
    "냉ㆍ온수": "냉·온수",
    "냉온수": "냉·온수",
    "공기조절": "공기 조절",
    "배관공급": "배관 공급",
    "금속가공": "금속 가공",
    "기초화학": "기초 화학",
    "기초 의약물질": "기초 의약 물질",
    "전자부품": "전자 부품",
    "식용빙과류": "식용 빙과류",
    "곡물가공품": "곡물 가공품",
    "비금속광물": "비금속 광물",
}

FORM_MARKERS = {
    "stock": ("주식회사", "(주)", "㈜"),
    "limited_liability": ("유한책임회사",),
    "limited": ("유한회사", "(유)"),
    "partnership": ("합자회사", "(합)"),
}


@dataclass(frozen=True)
class Observation:
    raw_name: str
    year: int
    metric: str
    value: int | None
    raw_sector: str | None
    raw_industry: str | None
    source_file: str
    source_row: int
    allocation_type: str | None = None


@dataclass(frozen=True)
class AliasInfo:
    core: str
    legal_form: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build public/data/krx-annual.json from Policy.zip"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"source archive (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"output JSON (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--relations",
        type=Path,
        default=DEFAULT_RELATIONS,
        help=f"official entity-relation registry (default: {DEFAULT_RELATIONS})",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="write compact JSON instead of indented JSON",
    )
    return parser.parse_args()


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def compact_key(value: Any) -> str:
    text = normalize_text(value).upper()
    return re.sub(r"[^0-9A-Z가-힣]", "", text)


def company_alias_info(raw_name: str) -> AliasInfo:
    text = unicodedata.normalize("NFKC", raw_name).strip()
    detected: set[str] = set()
    for form, markers in FORM_MARKERS.items():
        for marker in markers:
            if marker in text:
                detected.add(form)
                text = text.replace(marker, " ")

    legal_form = next(iter(detected)) if len(detected) == 1 else None
    if len(detected) > 1:
        # An unusual mixed-form name must not be joined with another entity.
        legal_form = "mixed-" + "-".join(sorted(detected))

    core = compact_key(text)
    if not core:
        raise ValueError(f"업체명을 정규화할 수 없습니다: {raw_name!r}")
    return AliasInfo(core=core, legal_form=legal_form)


def build_entity_groups(
    observations: Iterable[Observation],
) -> tuple[dict[str, str], list[dict[str, Any]], dict[str, Any]]:
    appearances: dict[str, Counter[int]] = defaultdict(Counter)
    forms_by_core: dict[str, set[str]] = defaultdict(set)
    alias_info: dict[str, AliasInfo] = {}

    for observation in observations:
        name = observation.raw_name
        appearances[name][observation.year] += 1
        if name not in alias_info:
            alias_info[name] = company_alias_info(name)
        form = alias_info[name].legal_form
        if form:
            forms_by_core[alias_info[name].core].add(form)

    group_for_name: dict[str, str] = {}
    for name, info in alias_info.items():
        conflicting_forms = forms_by_core[info.core]
        if len(conflicting_forms) <= 1:
            group_for_name[name] = info.core
        else:
            # When both e.g. 주식회사 and 유한회사가 exist for the same core,
            # an omitted legal form is not enough evidence to select either.
            suffix = info.legal_form or "unspecified"
            group_for_name[name] = f"{info.core}|{suffix}"

    aliases_by_group: dict[str, list[str]] = defaultdict(list)
    for name, group_key in group_for_name.items():
        aliases_by_group[group_key].append(name)

    entity_id_for_group = {
        group_key: "company-"
        + hashlib.sha256(group_key.encode("utf-8")).hexdigest()[:12]
        for group_key in aliases_by_group
    }

    name_to_entity: dict[str, str] = {}
    entities: list[dict[str, Any]] = []
    alias_group_count = 0
    for group_key, aliases in aliases_by_group.items():
        if len(aliases) > 1:
            alias_group_count += 1

        def display_score(name: str) -> tuple[int, int, int, int, str]:
            years = appearances[name]
            long_form = int(
                "주식회사" in name
                or "유한회사" in name
                or "유한책임회사" in name
                or "합자회사" in name
            )
            return (
                max(years),
                sum(years.values()),
                long_form,
                len(name),
                name,
            )

        display_name = max(aliases, key=display_score)
        entity_id = entity_id_for_group[group_key]
        alias_details = []
        for alias in sorted(aliases, key=lambda item: (min(appearances[item]), item)):
            years = appearances[alias]
            alias_details.append(
                {
                    "name": alias,
                    "firstYear": min(years),
                    "lastYear": max(years),
                    "occurrences": sum(years.values()),
                }
            )
            name_to_entity[alias] = entity_id

        entities.append(
            {
                "id": entity_id,
                "name": display_name,
                "aliases": sorted(aliases),
                "aliasDetails": alias_details,
                "normalization": "safe-legal-form-only-v1",
            }
        )

    entities.sort(key=lambda item: (item["name"], item["id"]))
    stats = {
        "rawCompanyNameCount": len(alias_info),
        "entityCount": len(entities),
        "aliasGroupCount": alias_group_count,
        "consolidatedAliasCount": len(alias_info) - len(entities),
        "conflictingLegalFormCoreCount": sum(
            len(forms) > 1 for forms in forms_by_core.values()
        ),
    }
    return name_to_entity, entities, stats


def decode_csv(data: bytes, filename: str) -> str:
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"CSV 문자 인코딩을 해석할 수 없습니다: {filename}")


def parse_integer(raw_value: Any) -> int | None:
    text = normalize_text(raw_value).replace(",", "")
    if text == "":
        return None
    if not re.fullmatch(r"[+-]?\d+", text):
        raise ValueError(f"정수가 아닌 수량입니다: {raw_value!r}")
    return int(text)


def clean_classification(value: Any) -> str | None:
    text = normalize_text(value)
    return text or None


def allocation_type(value: Any) -> str | None:
    text = normalize_text(value).upper()
    if text in {"Y", "N"}:
        return text
    if not text:
        return None
    raise ValueError(f"유상여부 값이 Y/N이 아닙니다: {value!r}")


class ArchiveReader:
    def __init__(self, path: Path) -> None:
        if not path.is_file():
            raise FileNotFoundError(f"원본 ZIP을 찾을 수 없습니다: {path}")
        self.path = path
        self.archive = zipfile.ZipFile(path)
        self.names = set(self.archive.namelist())
        self.source_stats: dict[str, dict[str, Any]] = {}

    def close(self) -> None:
        self.archive.close()

    def resolve(self, basename: str) -> str:
        candidates = sorted(
            name for name in self.names if Path(name).name == basename
        )
        if len(candidates) != 1:
            raise FileNotFoundError(
                f"ZIP에서 {basename!r} 파일을 정확히 하나 찾지 못했습니다: "
                f"{candidates}"
            )
        return candidates[0]

    def rows(self, basename: str) -> list[dict[str, str]]:
        member = self.resolve(basename)
        text = decode_csv(self.archive.read(member), basename)
        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows and not text.strip():
            raise ValueError(f"CSV가 비어 있습니다: {basename}")
        self.source_stats.setdefault(
            basename,
            {
                "file": basename,
                "rows": len(rows),
                "blankNumericCells": 0,
            },
        )
        return rows

    def note_blank(self, basename: str) -> None:
        self.source_stats[basename]["blankNumericCells"] += 1


def observe(
    observations: list[Observation],
    reader: ArchiveReader,
    *,
    basename: str,
    row: dict[str, str],
    row_number: int,
    year: int,
    metric: str,
    value_column: str,
    allocation: str | None = None,
) -> None:
    if year not in YEAR_SET:
        return
    raw_name = normalize_text(row.get("업체명"))
    if not raw_name:
        raise ValueError(f"{basename}:{row_number} 업체명이 비어 있습니다.")
    value = parse_integer(row.get(value_column))
    if value is None:
        reader.note_blank(basename)
    observations.append(
        Observation(
            raw_name=raw_name,
            year=year,
            metric=metric,
            value=value,
            raw_sector=clean_classification(row.get("부문")),
            raw_industry=clean_classification(row.get("업종")),
            source_file=basename,
            source_row=row_number,
            allocation_type=allocation,
        )
    )


def parse_total_caps(reader: ArchiveReader) -> tuple[dict[str, int], dict[str, int]]:
    annual_caps: dict[str, int] = {}
    plan_caps: dict[str, int] = {}
    for plan in (1, 2, 3):
        basename = f"배출권총수량_{plan}차.csv"
        rows = reader.rows(basename)
        if len(rows) != 1:
            raise ValueError(f"{basename}은 정확히 한 행이어야 합니다.")
        row = rows[0]
        plan_total = parse_integer(row.get("계획기간 배출권 총수량"))
        if plan_total is None:
            reader.note_blank(basename)
            raise ValueError(f"{basename} 계획기간 총수량이 비어 있습니다.")
        plan_caps[str(plan)] = plan_total
        for column, raw in row.items():
            match = re.fullmatch(r"(20\d{2})년 총수량", column or "")
            if not match:
                continue
            year = int(match.group(1))
            if year not in YEAR_SET:
                continue
            value = parse_integer(raw)
            if value is None:
                reader.note_blank(basename)
                raise ValueError(f"{basename} {year}년 총수량이 비어 있습니다.")
            annual_caps[str(year)] = value
    if set(map(int, annual_caps)) != YEAR_SET:
        missing = sorted(YEAR_SET - set(map(int, annual_caps)))
        raise ValueError(f"연도별 공식 총수량이 누락됐습니다: {missing}")
    return annual_caps, plan_caps


def parse_preallocation(
    reader: ArchiveReader, observations: list[Observation]
) -> None:
    for plan in (1, 2, 3):
        basename = f"사전할당량_{plan}차.csv"
        rows = reader.rows(basename)
        for row_number, row in enumerate(rows, start=2):
            allocation = allocation_type(row.get("유상여부"))
            for column in row:
                match = re.fullmatch(r"(20\d{2})년", column or "")
                if not match:
                    continue
                observe(
                    observations,
                    reader,
                    basename=basename,
                    row=row,
                    row_number=row_number,
                    year=int(match.group(1)),
                    metric="preAllocation",
                    value_column=column,
                    allocation=allocation,
                )


def parse_wide_metric(
    reader: ArchiveReader,
    observations: list[Observation],
    *,
    filename_prefix: str,
    column_prefix: str,
    metric: str,
) -> None:
    for plan in (1, 2, 3):
        basename = f"{filename_prefix}_{plan}차.csv"
        rows = reader.rows(basename)
        pattern = re.compile(rf"{re.escape(column_prefix)}\((20\d{{2}})년\)")
        for row_number, row in enumerate(rows, start=2):
            for column in row:
                match = pattern.fullmatch(column or "")
                if not match:
                    continue
                observe(
                    observations,
                    reader,
                    basename=basename,
                    row=row,
                    row_number=row_number,
                    year=int(match.group(1)),
                    metric=metric,
                    value_column=column,
                )


def parse_annual_sources(
    reader: ArchiveReader, observations: list[Observation]
) -> None:
    definitions = (
        ("인증배출량", "verifiedEmissions", "인증 배출량(톤)", "이행연도"),
        ("배출권차입량", "borrow", "차입배출권 수량", "연도"),
        (
            "상쇄배출권 발행량",
            "offsetIssued",
            "상쇄배출권 발행수량",
            "이행연도",
        ),
    )
    for year in YEARS:
        for prefix, metric, value_column, year_column in definitions:
            basename = f"{prefix}_{year}년.csv"
            rows = reader.rows(basename)
            for row_number, row in enumerate(rows, start=2):
                row_year_text = re.sub(r"[^0-9]", "", row.get(year_column, ""))
                row_year = int(row_year_text) if row_year_text else year
                if row_year != year:
                    raise ValueError(
                        f"{basename}:{row_number} 파일연도와 행연도가 다릅니다: "
                        f"{row_year}"
                    )
                observe(
                    observations,
                    reader,
                    basename=basename,
                    row=row,
                    row_number=row_number,
                    year=year,
                    metric=metric,
                    value_column=value_column,
                )

        basename = f"배출권이월량_{year}년.csv"
        rows = reader.rows(basename)
        for row_number, row in enumerate(rows, start=2):
            row_year_text = re.sub(r"[^0-9]", "", row.get("연도", ""))
            row_year = int(row_year_text) if row_year_text else year
            if row_year != year:
                raise ValueError(
                    f"{basename}:{row_number} 파일연도와 행연도가 다릅니다: "
                    f"{row_year}"
                )
            for metric, column in (
                ("carryoverAllowance", "할당배출권"),
                ("carryoverOffset", "상쇄배출권"),
            ):
                observe(
                    observations,
                    reader,
                    basename=basename,
                    row=row,
                    row_number=row_number,
                    year=year,
                    metric=metric,
                    value_column=column,
                )


def standardize_industry_text(value: str) -> str:
    text = normalize_text(value)
    for old, new in INDUSTRY_TEXT_REPLACEMENTS.items():
        text = text.replace(old, new)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def industry_equivalence_key(value: str) -> str:
    text = standardize_industry_text(value)
    return compact_key(text)


def build_industry_aliases(
    observations: Iterable[Observation],
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    candidates: dict[str, Counter[str]] = defaultdict(Counter)
    latest_year: dict[tuple[str, str], int] = {}
    raw_counts: Counter[str] = Counter()

    for observation in observations:
        raw = observation.raw_industry
        if not raw:
            sector_key = compact_key(observation.raw_sector)
            if sector_key in LEGACY_INDUSTRY_SECTORS:
                raw = observation.raw_sector
        if not raw:
            continue
        standardized = standardize_industry_text(raw)
        key = industry_equivalence_key(standardized)
        candidates[key][standardized] += 1
        latest_year[(key, standardized)] = max(
            latest_year.get((key, standardized), 0), observation.year
        )
        raw_counts[raw] += 1

    canonical_by_key: dict[str, str] = {}
    for key, labels in candidates.items():
        canonical_by_key[key] = max(
            labels,
            key=lambda label: (
                latest_year[(key, label)],
                labels[label],
                len(label),
                label,
            ),
        )

    raw_to_standard: dict[str, str] = {}
    mappings = []
    for raw, count in sorted(raw_counts.items()):
        standard = canonical_by_key[industry_equivalence_key(raw)]
        raw_to_standard[raw] = standard
        mappings.append(
            {"raw": raw, "standard": standard, "occurrences": count}
        )
    return raw_to_standard, mappings


def standardize_sector(raw_sector: str | None, raw_industry: str | None) -> str:
    sector = normalize_text(raw_sector)
    sector_compact = compact_key(sector)
    industry_compact = compact_key(raw_industry)

    if sector in STANDARD_SECTORS:
        return sector
    if sector_compact == "공공및폐기물":
        waste_tokens = ("폐기물", "하수", "폐수", "분뇨")
        if any(token in normalize_text(raw_industry) for token in waste_tokens):
            return "폐기물"
        return "공공·기타"
    if sector_compact in {"공공기타", "공공및기타"}:
        return "공공·기타"
    if sector_compact in LEGACY_INDUSTRY_SECTORS:
        return LEGACY_INDUSTRY_SECTORS[sector_compact]
    if industry_compact in LEGACY_INDUSTRY_SECTORS:
        return LEGACY_INDUSTRY_SECTORS[industry_compact]
    return "미분류"


def choose_classification(
    observations: list[Observation],
    industry_aliases: dict[str, str],
) -> tuple[str, str, list[dict[str, Any]], list[str]]:
    grouped: dict[tuple[str | None, str | None], dict[str, Any]] = {}
    for observation in observations:
        key = (observation.raw_sector, observation.raw_industry)
        item = grouped.setdefault(
            key,
            {
                "sector": observation.raw_sector,
                "industry": observation.raw_industry,
                "sources": set(),
                "metrics": set(),
                "count": 0,
                "priority": 0,
            },
        )
        item["sources"].add(observation.source_file)
        item["metrics"].add(observation.metric)
        item["count"] += 1
        item["priority"] = max(
            item["priority"], CLASSIFICATION_PRIORITY[observation.metric]
        )

    raw_classifications = []
    for item in grouped.values():
        raw_classifications.append(
            {
                "sector": item["sector"],
                "industry": item["industry"],
            }
        )
    raw_classifications.sort(
        key=lambda item: (
            item["sector"] or "",
            item["industry"] or "",
        )
    )

    if not grouped:
        return "미분류", "미분류", [], ["classificationMissing"]

    ranked = sorted(
        grouped.values(),
        key=lambda item: (
            item["priority"],
            item["count"],
            item["sector"] or "",
            item["industry"] or "",
        ),
        reverse=True,
    )
    chosen = ranked[0]
    flags: list[str] = []
    tied = [
        item
        for item in ranked
        if (item["priority"], item["count"])
        == (chosen["priority"], chosen["count"])
        and (item["sector"], item["industry"])
        != (chosen["sector"], chosen["industry"])
    ]
    if tied:
        flags.append("classificationConflict")

    raw_sector = chosen["sector"]
    raw_industry = chosen["industry"]
    # The first-plan pre-allocation CSV uses legacy industry groups in the
    # column labelled `부문`.  Map only those explicit legacy labels to the
    # broad dashboard sectors; never infer a modern detailed industry.
    sector = standardize_sector(raw_sector, raw_industry)
    if raw_industry:
        industry = industry_aliases.get(
            raw_industry, standardize_industry_text(raw_industry)
        )
    elif compact_key(raw_sector) in LEGACY_INDUSTRY_SECTORS:
        industry = standardize_industry_text(raw_sector or "")
    else:
        industry = "미분류"

    if sector == "미분류" or industry == "미분류":
        flags.append("classificationMissing")
    return sector, industry, raw_classifications, flags


def aggregate_metric(
    contributions: list[Observation], metric: str
) -> tuple[int | None, str | None, int]:
    if not contributions:
        if metric in SPARSE_ZERO_METRICS:
            return 0, "absent-zero", 0
        return None, "absent-null", 0
    if any(item.value is None for item in contributions):
        return None, "blank", len(contributions)
    return sum(int(item.value) for item in contributions), None, len(contributions)


def dependency_sum(
    values: dict[str, int | None], names: tuple[str, ...], signs: tuple[int, ...]
) -> tuple[int | None, str | None]:
    missing = [name for name in names if values[name] is None]
    if missing:
        return None, "dependency:" + ",".join(missing)
    return sum(int(values[name]) * sign for name, sign in zip(names, signs)), None


def build_rows(
    observations: list[Observation],
    name_to_entity: dict[str, str],
    entities: list[dict[str, Any]],
    industry_aliases: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    entity_lookup = {item["id"]: item for item in entities}
    grouped: dict[tuple[str, int], list[Observation]] = defaultdict(list)
    for observation in observations:
        grouped[(name_to_entity[observation.raw_name], observation.year)].append(
            observation
        )

    rows: list[dict[str, Any]] = []
    duplicate_contributions = 0
    blank_counts: Counter[str] = Counter()
    absent_counts: Counter[str] = Counter()
    allocation_conflicts = 0
    classification_conflicts = 0
    missing_classifications = 0
    missing_final = 0
    yearly_counts: Counter[int] = Counter()

    for (entity_id, year), items in grouped.items():
        metric_items: dict[str, list[Observation]] = defaultdict(list)
        for item in items:
            metric_items[item.metric].append(item)

        values: dict[str, int | None] = {}
        missing_reason: dict[str, str] = {}
        source_counts: dict[str, int] = {}
        for metric in BASE_METRICS:
            value, reason, contribution_count = aggregate_metric(
                metric_items.get(metric, []), metric
            )
            values[metric] = value
            source_counts[metric] = contribution_count
            duplicate_contributions += max(0, contribution_count - 1)
            if reason:
                missing_reason[metric] = reason
                if reason == "blank":
                    blank_counts[metric] += 1
                elif reason.startswith("absent-"):
                    absent_counts[metric] += 1

        adjusted, adjusted_reason = dependency_sum(
            values,
            ("preAllocation", "additionalAllocation", "cancellation"),
            (1, 1, -1),
        )
        values["adjustedAllocation"] = adjusted
        if adjusted_reason:
            missing_reason["adjustedAllocation"] = adjusted_reason

        carryover, carryover_reason = dependency_sum(
            values,
            ("carryoverAllowance", "carryoverOffset"),
            (1, 1),
        )
        values["carryover"] = carryover
        if carryover_reason:
            missing_reason["carryover"] = carryover_reason

        final_balance, final_reason = dependency_sum(
            values,
            ("adjustedAllocation", "borrow", "verifiedEmissions", "carryover"),
            (1, 1, -1, -1),
        )
        values["finalBalance"] = final_balance
        if final_reason:
            missing_reason["finalBalance"] = final_reason
            missing_final += 1

        pre_types = {
            item.allocation_type
            for item in metric_items.get("preAllocation", [])
            if item.allocation_type in {"Y", "N"}
        }
        quality_flags: list[str] = []
        if len(pre_types) == 1:
            row_allocation_type: str | None = next(iter(pre_types))
        else:
            row_allocation_type = None
            if len(pre_types) > 1:
                quality_flags.append("allocationTypeConflict")
                allocation_conflicts += 1

        sector, industry, raw_classifications, classification_flags = (
            choose_classification(items, industry_aliases)
        )
        quality_flags.extend(classification_flags)
        if "classificationConflict" in classification_flags:
            classification_conflicts += 1
        if "classificationMissing" in classification_flags:
            missing_classifications += 1
        if any(count > 1 for count in source_counts.values()):
            quality_flags.append("duplicateRowsAggregated")
        if any(reason == "blank" for reason in missing_reason.values()):
            quality_flags.append("blankSourceCell")

        entity = entity_lookup[entity_id]
        row = {
            "year": year,
            "sector": sector,
            "industry": industry,
            "companyId": entity_id,
            "companyName": entity["name"],
            "aliases": entity["aliases"],
            "rawClassifications": raw_classifications,
            "allocationType": row_allocation_type,
            "metrics": {name: values[name] for name in ALL_METRICS},
            "missing": {name: values[name] is None for name in ALL_METRICS},
            "missingReason": dict(sorted(missing_reason.items())),
            "qualityFlags": sorted(set(quality_flags)),
            "sources": sorted({item.source_file for item in items}),
        }
        rows.append(row)
        yearly_counts[year] += 1

    sector_position = {sector: index for index, sector in enumerate(SECTOR_ORDER)}
    rows.sort(
        key=lambda row: (
            row["year"],
            sector_position.get(row["sector"], len(SECTOR_ORDER)),
            row["industry"],
            row["companyName"],
            row["companyId"],
        )
    )
    stats = {
        "rowCount": len(rows),
        "rowsByYear": {str(year): yearly_counts[year] for year in YEARS},
        "duplicateMetricContributionsAggregated": duplicate_contributions,
        "rowsWithBlankSourceCellByMetric": dict(sorted(blank_counts.items())),
        "absentCompanyMetricDefaults": dict(sorted(absent_counts.items())),
        "rowsWithMissingFinalBalance": missing_final,
        "rowsWithAllocationTypeConflict": allocation_conflicts,
        "rowsWithClassificationConflict": classification_conflicts,
        "rowsWithMissingClassification": missing_classifications,
    }
    return rows, stats


def metric_contract() -> dict[str, Any]:
    return {
        "unit": "tCO2eq",
        "fields": {
            "preAllocation": {
                "label": "사전할당",
                "sourceAbsence": "zero",
                "blankCell": "null",
            },
            "additionalAllocation": {
                "label": "추가할당",
                "sourceAbsence": "zero",
                "blankCell": "null",
            },
            "cancellation": {
                "label": "할당취소",
                "sourceAbsence": "zero",
                "blankCell": "null",
            },
            "adjustedAllocation": {
                "label": "조정할당",
                "formula": "preAllocation + additionalAllocation - cancellation",
                "nullRule": "null if any dependency is null",
            },
            "verifiedEmissions": {
                "label": "인증배출량",
                "sourceAbsence": "null",
                "blankCell": "null",
            },
            "carryover": {
                "label": "이월량",
                "formula": "carryoverAllowance + carryoverOffset",
                "nullRule": "null if any dependency is null",
            },
            "carryoverAllowance": {
                "label": "할당배출권 이월",
                "sourceAbsence": "zero",
                "blankCell": "null",
            },
            "carryoverOffset": {
                "label": "상쇄배출권 이월",
                "sourceAbsence": "zero",
                "blankCell": "null",
            },
            "borrow": {
                "label": "차입",
                "sourceAbsence": "zero",
                "blankCell": "null",
            },
            "offsetIssued": {
                "label": "상쇄배출권 발행",
                "sourceAbsence": "zero",
                "blankCell": "null",
                "note": "informational; not a finalBalance dependency",
            },
            "finalBalance": {
                "label": "과부족량",
                "formula": (
                    "adjustedAllocation + borrow - verifiedEmissions - carryover"
                ),
                "nullRule": "null if any dependency is null",
            },
        },
        "allocationType": {
            "values": {"Y": "유상", "N": "무상", "null": "미제공/충돌"},
            "conflictRule": "null when multiple pre-allocation rows disagree",
        },
    }


def classification_contract(
    rows: list[dict[str, Any]], industry_mappings: list[dict[str, Any]]
) -> dict[str, Any]:
    industries_by_sector: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        industries_by_sector[row["sector"]].add(row["industry"])
    return {
        "sectorOrder": list(SECTOR_ORDER),
        "sectors": [
            {
                "name": sector,
                "industries": sorted(industries_by_sector.get(sector, set())),
            }
            for sector in SECTOR_ORDER
            if sector in industries_by_sector
        ],
        "industryMappings": industry_mappings,
        "rules": [
            "원문 부문·업종은 rows.rawClassifications에 보존한다.",
            "공백·문장부호·명백한 철자 변형만 같은 표준 업종명으로 정리한다.",
            "2015~2017 공공 및 폐기물은 원문 업종에 따라 폐기물/공공·기타로 나눈다.",
            "구분할 근거가 없는 광범위한 구 업종은 임의로 세부 KSIC에 매핑하지 않는다.",
        ],
    }


def load_entity_relations(
    path: Path,
    name_to_entity: dict[str, str],
    entities: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"업체 승계 관계 파일을 찾을 수 없습니다: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    source_relations = raw.get("relations") if isinstance(raw, dict) else None
    if not isinstance(source_relations, list):
        raise ValueError("업체 승계 관계 파일의 relations는 배열이어야 합니다.")

    entity_lookup = {item["id"]: item for item in entities}
    per_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for source in source_relations:
        if not isinstance(source, dict):
            raise ValueError("업체 승계 관계 항목은 객체여야 합니다.")
        relation_id = normalize_text(source.get("id"))
        relation_type = normalize_text(source.get("relationType"))
        effective_date = normalize_text(source.get("effectiveDate"))
        from_name = normalize_text(source.get("fromName"))
        to_name = normalize_text(source.get("toName"))
        source_title = normalize_text(source.get("sourceTitle"))
        source_url = normalize_text(source.get("sourceUrl"))
        status = normalize_text(source.get("status"))
        if not all(
            (
                relation_id,
                relation_type,
                effective_date,
                from_name,
                to_name,
                source_title,
                source_url,
                status,
            )
        ):
            raise ValueError(f"업체 승계 관계 필수값이 비어 있습니다: {source!r}")
        if relation_id in seen_ids:
            raise ValueError(f"업체 승계 관계 ID가 중복됐습니다: {relation_id}")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", effective_date):
            raise ValueError(f"승계 관계 효력일 형식이 잘못됐습니다: {effective_date}")
        if not source_url.startswith("https://"):
            raise ValueError(f"승계 관계 근거 URL은 HTTPS여야 합니다: {source_url}")
        if from_name not in name_to_entity or to_name not in name_to_entity:
            raise ValueError(
                "승계 관계 업체명이 원문 별칭과 정확히 일치하지 않습니다: "
                f"{from_name!r} -> {to_name!r}"
            )
        from_entity_id = name_to_entity[from_name]
        to_entity_id = name_to_entity[to_name]
        if from_entity_id == to_entity_id:
            raise ValueError(f"승계 관계 양쪽 업체 ID가 같습니다: {relation_id}")
        seen_ids.add(relation_id)
        relation = {
            "id": relation_id,
            "relationType": relation_type,
            "effectiveDate": effective_date,
            "fromEntityId": from_entity_id,
            "fromName": from_name,
            "toEntityId": to_entity_id,
            "toName": to_name,
            "sourceTitle": source_title,
            "sourceUrl": source_url,
            "status": status,
            "aggregationRule": "relationship-only-no-metric-merge",
        }
        result.append(relation)
        common = {
            "relationId": relation_id,
            "relationType": relation_type,
            "effectiveDate": effective_date,
            "sourceTitle": source_title,
            "sourceUrl": source_url,
            "status": status,
        }
        per_entity[from_entity_id].append(
            {
                **common,
                "direction": "from",
                "counterpartEntityId": to_entity_id,
                "counterpartName": to_name,
            }
        )
        per_entity[to_entity_id].append(
            {
                **common,
                "direction": "to",
                "counterpartEntityId": from_entity_id,
                "counterpartName": from_name,
            }
        )

    result.sort(key=lambda item: (item["effectiveDate"], item["id"]))
    for entity_id, entity in entity_lookup.items():
        relations = sorted(
            per_entity.get(entity_id, []),
            key=lambda item: (item["effectiveDate"], item["relationId"], item["direction"]),
        )
        entity["relations"] = relations
        entity["relatedCompanyNames"] = sorted(
            {item["counterpartName"] for item in relations}
        )
    return result, {
        "officialEntityRelationCount": len(result),
        "entitiesWithOfficialRelation": len(per_entity),
        "entityRelationRegistrySha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def build_payload(input_path: Path, relations_path: Path) -> dict[str, Any]:
    reader = ArchiveReader(input_path)
    try:
        annual_caps, plan_caps = parse_total_caps(reader)
        observations: list[Observation] = []
        parse_preallocation(reader, observations)
        parse_wide_metric(
            reader,
            observations,
            filename_prefix="추가할당량",
            column_prefix="추가 할당량",
            metric="additionalAllocation",
        )
        parse_wide_metric(
            reader,
            observations,
            filename_prefix="할당 취소량",
            column_prefix="할당 취소량",
            metric="cancellation",
        )
        parse_annual_sources(reader, observations)

        name_to_entity, entities, entity_stats = build_entity_groups(observations)
        industry_aliases, industry_mappings = build_industry_aliases(observations)
        rows, row_stats = build_rows(
            observations,
            name_to_entity,
            entities,
            industry_aliases,
        )
        entity_relations, relation_stats = load_entity_relations(
            relations_path,
            name_to_entity,
            entities,
        )
        entity_lookup = {item["id"]: item for item in entities}
        for row in rows:
            entity = entity_lookup[row["companyId"]]
            row["relatedCompanyNames"] = entity["relatedCompanyNames"]
            row["entityRelations"] = entity["relations"]

        archive_sha256 = hashlib.sha256(input_path.read_bytes()).hexdigest()
        source_files = sorted(reader.source_stats.values(), key=lambda item: item["file"])
        source_row_count = sum(item["rows"] for item in source_files)
        blank_numeric_count = sum(item["blankNumericCells"] for item in source_files)
        quality = {
            **entity_stats,
            **row_stats,
            **relation_stats,
            "sourceFileCount": len(source_files),
            "sourceRowCount": source_row_count,
            "blankNumericSourceCellCount": blank_numeric_count,
            "sourceFiles": source_files,
            "notes": [
                "중복 원문행은 삭제하지 않고 업체-연도-지표 단위로 합산했다.",
                "하나의 합산 대상에 공란이 하나라도 있으면 해당 지표를 null로 두었다.",
                "합병·분할·인수 및 유사 업체명은 자동 통합하지 않았다.",
                "공식 근거가 확인된 합병·분할은 수치를 합치지 않고 승계 관계로만 연결했다.",
            ],
        }
        return {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": {
                "archive": "source-data/krx-annual/Policy.zip",
                "archiveSha256": archive_sha256,
                "builder": "scripts/build_krx_annual.py",
                "builderVersion": BUILDER_VERSION,
                "entityRelationRegistry": "source-data/krx-annual/entity-relations.json",
                "entityRelationRegistrySha256": relation_stats[
                    "entityRelationRegistrySha256"
                ],
                "encoding": "CP949",
                "yearRange": {"from": YEARS[0], "to": YEARS[-1]},
            },
            "meta": {
                "title": "KRX 업체현황(연간)",
                "years": list(YEARS),
                "annualCaps": annual_caps,
                "planCaps": plan_caps,
                "unit": "tCO2eq",
                "rowCount": len(rows),
                "companyCount": len(entities),
            },
            "metrics": metric_contract(),
            "classifications": classification_contract(rows, industry_mappings),
            "entities": entities,
            "entityRelations": entity_relations,
            "rows": rows,
            "quality": quality,
        }
    finally:
        reader.close()


def write_json(path: Path, payload: dict[str, Any], *, compact: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    else:
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    temporary.chmod(0o644)
    temporary.replace(path)


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    relations_path = args.relations.resolve()
    payload = build_payload(input_path, relations_path)
    write_json(output_path, payload, compact=args.compact)
    quality = payload["quality"]
    print(
        "KRX 연간 업체자료 생성 완료: "
        f"{output_path} / {quality['rowCount']:,}행 / "
        f"{quality['entityCount']:,}개 업체 / "
        f"과부족량 결측 {quality['rowsWithMissingFinalBalance']:,}행"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
