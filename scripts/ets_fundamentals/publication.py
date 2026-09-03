from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

from .io_utils import atomic_write_json, load_json, sha256_file
from .models import (
    SCHEMA_VERSION,
    CollectorError,
    DatasetSpec,
    NormalizedDataset,
    iso_kst,
    parse_iso_date,
)
from .normalization import (
    _coverage,
    _merge_records,
    _record_key,
    _spec_from_document,
    _write_csv,
)


def publish_dataset(*, public_root: Path, dataset: NormalizedDataset) -> dict[str, Any]:
    if not dataset.records:
        raise CollectorError("Refusing to publish an empty dataset")
    if dataset.spec.category != "power_energy":
        raise CollectorError("This publisher only accepts the power_energy category")

    target_date = dataset.observation_date
    source_root = public_root / dataset.spec.source_id
    partition_dir = source_root / "daily" / f"{target_date:%Y}"
    json_path = partition_dir / f"{target_date.isoformat()}.json"
    csv_path = partition_dir / f"{target_date.isoformat()}.csv"

    existing_document = load_json(json_path, None)
    existing_records: list[Mapping[str, Any]] = []
    if existing_document is not None:
        if not isinstance(existing_document, Mapping):
            raise CollectorError(f"Published partition must be a JSON object: {json_path}")
        existing_spec = _spec_from_document(existing_document)
        if existing_spec.identity != dataset.spec.identity:
            raise CollectorError(
                f"Dataset schema changed for {dataset.spec.source_id}; publish a versioned source id"
            )
        if str(existing_document.get("date")) != target_date.isoformat():
            raise CollectorError(f"Partition date mismatch: {json_path}")
        values = existing_document.get("records", [])
        if not isinstance(values, list):
            raise CollectorError(f"records must be a list: {json_path}")
        existing_records = [value for value in values if isinstance(value, Mapping)]

    merged_records = _merge_records(
        existing_records,
        dataset.records,
        spec=dataset.spec,
        target_date=target_date,
    )
    document = {
        "schema_version": SCHEMA_VERSION,
        "date": target_date.isoformat(),
        "generated_at": dataset.source_run.collected_at,
        "source": dataset.spec.identity,
        "source_run": dataset.source_run.to_dict(),
        "coverage": _coverage(merged_records, dataset.spec),
        "records": merged_records,
    }
    atomic_write_json(json_path, document)
    _write_csv(csv_path, dataset.spec.csv_columns, merged_records)
    index = rebuild_public_indexes(public_root=public_root)
    return {
        "source_id": dataset.spec.source_id,
        "date": target_date.isoformat(),
        "json": json_path.as_posix(),
        "csv": csv_path.as_posix(),
        "record_count": len(merged_records),
        "index": index,
    }


def _iter_partitions(public_root: Path) -> Iterable[tuple[Path, Mapping[str, Any]]]:
    if not public_root.exists():
        return
    for path in sorted(public_root.glob("*/daily/*/*.json")):
        document = load_json(path, None)
        if not isinstance(document, Mapping):
            raise CollectorError(f"Partition must be a JSON object: {path}")
        yield path, document


def rebuild_public_indexes(*, public_root: Path) -> dict[str, Any]:
    partition_entries: list[dict[str, Any]] = []
    source_catalog: dict[str, dict[str, Any]] = {}
    latest_by_source: dict[str, tuple[str, Mapping[str, Any], DatasetSpec, Mapping[str, Any]]] = {}
    total_records = 0
    generated_at_values: list[str] = []

    for path, document in _iter_partitions(public_root):
        spec = _spec_from_document(document)
        date_text = str(document.get("date", ""))
        parse_iso_date(date_text)
        records = document.get("records", [])
        if not isinstance(records, list):
            raise CollectorError(f"records must be a list: {path}")
        normalised_records = _merge_records(
            [],
            [record for record in records if isinstance(record, Mapping)],
            spec=spec,
            target_date=date.fromisoformat(date_text),
        )
        if len(normalised_records) != len(records):
            raise CollectorError(f"Duplicate or invalid keys in partition: {path}")
        csv_path = path.with_suffix(".csv")
        if not csv_path.exists():
            raise CollectorError(f"Missing partition CSV: {csv_path}")

        total_records += len(normalised_records)
        generated_at_values.append(str(document.get("generated_at", "")))
        relative_json = path.relative_to(public_root).as_posix()
        relative_csv = csv_path.relative_to(public_root).as_posix()
        entry = {
            "source_id": spec.source_id,
            "date": date_text,
            "json": relative_json,
            "csv": relative_csv,
            "record_count": len(normalised_records),
            "coverage": document.get("coverage", {}),
            "json_sha256": sha256_file(path),
            "csv_sha256": sha256_file(csv_path),
        }
        partition_entries.append(entry)

        catalog = source_catalog.setdefault(
            spec.source_id,
            {
                **spec.identity,
                "first_date": date_text,
                "last_date": date_text,
                "date_count": 0,
                "record_count": 0,
            },
        )
        catalog["first_date"] = min(str(catalog["first_date"]), date_text)
        catalog["last_date"] = max(str(catalog["last_date"]), date_text)
        catalog["date_count"] = int(catalog["date_count"]) + 1
        catalog["record_count"] = int(catalog["record_count"]) + len(normalised_records)

        if normalised_records:
            latest_record = normalised_records[-1]
            latest_key = "|".join(_record_key(latest_record, spec.key_fields))
            current = latest_by_source.get(spec.source_id)
            if current is None or latest_key > current[0]:
                latest_by_source[spec.source_id] = (
                    latest_key,
                    latest_record,
                    spec,
                    document.get("coverage", {}),
                )

    partition_entries.sort(key=lambda value: (value["date"], value["source_id"]), reverse=True)
    dates = [entry["date"] for entry in partition_entries]
    generated_at = max((value for value in generated_at_values if value), default=iso_kst())
    index_document = {
        "schema_version": SCHEMA_VERSION,
        "category": "power_energy",
        "generated_at": generated_at,
        "coverage": {
            "first_date": min(dates) if dates else None,
            "last_date": max(dates) if dates else None,
            "date_count": len(set(dates)),
        },
        "source_count": len(source_catalog),
        "total_record_count": total_records,
        "sources": sorted(source_catalog.values(), key=lambda value: value["source_id"]),
        "partitions": partition_entries,
    }
    latest_document = {
        "schema_version": SCHEMA_VERSION,
        "category": "power_energy",
        "generated_at": generated_at,
        "sources": [
            {
                "source": spec.identity,
                "coverage": coverage,
                "record": dict(record),
            }
            for _, record, spec, coverage in (
                latest_by_source[source_id] for source_id in sorted(latest_by_source)
            )
        ],
    }
    atomic_write_json(public_root / "index.json", index_document)
    atomic_write_json(public_root / "latest.json", latest_document)
    return index_document


def validate_public_dataset(*, public_root: Path) -> dict[str, Any]:
    index = load_json(public_root / "index.json", None)
    latest = load_json(public_root / "latest.json", None)
    if not isinstance(index, Mapping) or not isinstance(latest, Mapping):
        raise CollectorError("index.json and latest.json must be JSON objects")

    total_records = 0
    seen_partitions: set[tuple[str, str]] = set()
    for entry in index.get("partitions", []):
        if not isinstance(entry, Mapping):
            raise CollectorError("Each index partition entry must be an object")
        identity = (str(entry.get("source_id")), str(entry.get("date")))
        if identity in seen_partitions:
            raise CollectorError(f"Duplicate index partition: {identity}")
        seen_partitions.add(identity)
        json_path = public_root / str(entry["json"])
        csv_path = public_root / str(entry["csv"])
        if not json_path.exists() or not csv_path.exists():
            raise CollectorError(f"Missing indexed partition files: {identity}")
        if sha256_file(json_path) != entry.get("json_sha256"):
            raise CollectorError(f"JSON checksum mismatch: {json_path}")
        if sha256_file(csv_path) != entry.get("csv_sha256"):
            raise CollectorError(f"CSV checksum mismatch: {csv_path}")
        document = load_json(json_path, None)
        if not isinstance(document, Mapping):
            raise CollectorError(f"Partition must be a JSON object: {json_path}")
        records = document.get("records", [])
        if not isinstance(records, list):
            raise CollectorError(f"records must be a list: {json_path}")
        with csv_path.open("r", encoding="utf-8", newline="") as stream:
            csv_count = sum(1 for _ in csv.DictReader(stream))
        if csv_count != len(records) or int(entry.get("record_count", -1)) != len(records):
            raise CollectorError(f"Record count mismatch: {identity}")
        total_records += len(records)

    if int(index.get("total_record_count", -1)) != total_records:
        raise CollectorError("Index total_record_count does not match partitions")
    source_count = len(index.get("sources", []))
    if int(index.get("source_count", -1)) != source_count:
        raise CollectorError("Index source_count does not match sources")
    return {
        "source_count": source_count,
        "partition_count": len(seen_partitions),
        "total_record_count": total_records,
        "latest_source_count": len(latest.get("sources", [])),
    }
