from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
SCHEMA_VERSION = "1.0"


class CollectorError(RuntimeError):
    """Raised when collection, normalization, or publication cannot be completed safely."""


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    id: str
    name_ko: str
    data_type: str
    unit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    source_id: str
    source_name: str
    organization: str
    category: str
    sub_category: str
    frequency: str
    geography: str
    key_fields: tuple[str, ...]
    dimensions: tuple[FieldDefinition, ...]
    metrics: tuple[FieldDefinition, ...]
    expected_records_per_day: int | None = None
    dataset_page: str | None = None

    @property
    def csv_columns(self) -> tuple[str, ...]:
        return (
            *self.key_fields,
            *(field.id for field in self.dimensions),
            *(field.id for field in self.metrics),
        )

    @property
    def identity(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "organization": self.organization,
            "category": self.category,
            "sub_category": self.sub_category,
            "frequency": self.frequency,
            "geography": self.geography,
            "key_fields": list(self.key_fields),
            "dimensions": [field.to_dict() for field in self.dimensions],
            "metrics": [field.to_dict() for field in self.metrics],
            "expected_records_per_day": self.expected_records_per_day,
            "dataset_page": self.dataset_page,
        }


@dataclass(frozen=True, slots=True)
class SourceRun:
    collected_at: str
    run_id: str
    endpoint_id: str
    request_variant: str
    response_format: str
    raw_archive_path: str
    raw_sha256: str
    response_record_count: int
    normalized_record_count: int
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["warnings"] = list(self.warnings)
        return data


@dataclass(frozen=True, slots=True)
class NormalizedDataset:
    spec: DatasetSpec
    observation_date: date
    records: tuple[Mapping[str, Any], ...]
    source_run: SourceRun


def run_id_for(collected_at: datetime) -> str:
    return collected_at.astimezone(KST).strftime("%Y%m%dT%H%M%S%z")


def iso_kst(value: datetime | None = None) -> str:
    dt = value or datetime.now(KST)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(KST).replace(microsecond=0).isoformat()


def parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CollectorError(f"Invalid date {value!r}; expected YYYY-MM-DD") from exc
