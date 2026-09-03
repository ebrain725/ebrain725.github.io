from __future__ import annotations

import csv
import math
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .models import KST, CollectorError, DatasetSpec, FieldDefinition


def _validate_numeric(value: Any, *, field_id: str) -> float:
    if isinstance(value, bool):
        raise CollectorError(f"Boolean is not a valid numeric value for {field_id}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise CollectorError(f"Non-numeric value for {field_id}: {value!r}") from exc
    if not math.isfinite(number):
        raise CollectorError(f"Non-finite value for {field_id}: {value!r}")
    return number


def _normalise_record(record: Mapping[str, Any], spec: DatasetSpec, target_date: date) -> dict[str, Any]:
    required = set(spec.csv_columns)
    missing = sorted(required - set(record))
    if missing:
        raise CollectorError(f"Record is missing required fields: {', '.join(missing)}")

    normalised: dict[str, Any] = {}
    metric_ids = {field.id for field in spec.metrics}
    for column in spec.csv_columns:
        value = record[column]
        normalised[column] = (
            _validate_numeric(value, field_id=column) if column in metric_ids else value
        )

    if "observed_at" in spec.key_fields:
        try:
            observed_at = datetime.fromisoformat(str(normalised["observed_at"]))
        except ValueError as exc:
            raise CollectorError(f"Invalid observed_at: {normalised['observed_at']!r}") from exc
        if observed_at.tzinfo is None:
            raise CollectorError("observed_at must include a timezone offset")
        if observed_at.astimezone(KST).date() != target_date:
            raise CollectorError(
                f"Record date mismatch: {normalised['observed_at']} is not {target_date.isoformat()}"
            )
        normalised["observed_at"] = observed_at.astimezone(KST).replace(microsecond=0).isoformat()
    return normalised


def _record_key(record: Mapping[str, Any], key_fields: Sequence[str]) -> tuple[str, ...]:
    return tuple(str(record[field]) for field in key_fields)


def _merge_records(
    existing: Iterable[Mapping[str, Any]],
    incoming: Iterable[Mapping[str, Any]],
    *,
    spec: DatasetSpec,
    target_date: date,
) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, ...], dict[str, Any]] = {}
    for record in existing:
        normalised = _normalise_record(record, spec, target_date)
        by_key[_record_key(normalised, spec.key_fields)] = normalised
    for record in incoming:
        normalised = _normalise_record(record, spec, target_date)
        by_key[_record_key(normalised, spec.key_fields)] = normalised
    return [by_key[key] for key in sorted(by_key)]


def _write_csv(path: Path, columns: Sequence[str], records: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(columns), extrasaction="ignore")
            writer.writeheader()
            writer.writerows(records)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def _coverage(records: Sequence[Mapping[str, Any]], spec: DatasetSpec) -> dict[str, Any]:
    coverage: dict[str, Any] = {
        "record_count": len(records),
        "expected_records_per_day": spec.expected_records_per_day,
        "completion_ratio": None,
        "first_observed_at": None,
        "last_observed_at": None,
    }
    if spec.expected_records_per_day:
        coverage["completion_ratio"] = round(
            min(len(records) / spec.expected_records_per_day, 1.0),
            4,
        )
    if records and "observed_at" in spec.key_fields:
        observed = sorted(str(record["observed_at"]) for record in records)
        coverage["first_observed_at"] = observed[0]
        coverage["last_observed_at"] = observed[-1]
    return coverage


def _spec_from_document(document: Mapping[str, Any]) -> DatasetSpec:
    source = document.get("source")
    if not isinstance(source, Mapping):
        raise CollectorError("Published dataset is missing source metadata")

    def fields(name: str) -> tuple[FieldDefinition, ...]:
        values = source.get(name, [])
        if not isinstance(values, list):
            raise CollectorError(f"source.{name} must be a list")
        return tuple(
            FieldDefinition(
                id=str(value["id"]),
                name_ko=str(value["name_ko"]),
                data_type=str(value["data_type"]),
                unit=(None if value.get("unit") is None else str(value.get("unit"))),
            )
            for value in values
            if isinstance(value, Mapping)
        )

    key_fields = source.get("key_fields", [])
    if not isinstance(key_fields, list):
        raise CollectorError("source.key_fields must be a list")
    expected = source.get("expected_records_per_day")
    return DatasetSpec(
        source_id=str(source["source_id"]),
        source_name=str(source["source_name"]),
        organization=str(source["organization"]),
        category=str(source["category"]),
        sub_category=str(source["sub_category"]),
        frequency=str(source["frequency"]),
        geography=str(source["geography"]),
        key_fields=tuple(str(value) for value in key_fields),
        dimensions=fields("dimensions"),
        metrics=fields("metrics"),
        expected_records_per_day=(None if expected is None else int(expected)),
        dataset_page=(None if source.get("dataset_page") is None else str(source.get("dataset_page"))),
    )
