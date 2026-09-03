from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from .models import CollectorError, SCHEMA_VERSION


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
    )


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CollectorError(f"Unable to read JSON: {path}") from exc


def archive_raw_payload(
    *,
    archive_root: Path,
    source_id: str,
    observation_date: date,
    response_format: str,
    payload: bytes,
    metadata: Mapping[str, Any],
) -> tuple[Path, str]:
    extension = "json" if response_format == "json" else "xml"
    relative = (
        Path(source_id)
        / f"{observation_date:%Y}"
        / f"{observation_date:%m}"
        / f"{observation_date.isoformat()}.{extension}"
    )
    absolute = archive_root / relative
    for stale_extension in ("json", "xml"):
        stale = absolute.with_suffix(f".{stale_extension}")
        if stale != absolute:
            stale.unlink(missing_ok=True)
            stale.with_suffix(stale.suffix + ".meta.json").unlink(missing_ok=True)
    atomic_write_bytes(absolute, payload)
    digest = sha256_bytes(payload)
    safe_metadata = dict(metadata)
    safe_metadata.update(
        {
            "schema_version": SCHEMA_VERSION,
            "raw_file": relative.as_posix(),
            "raw_sha256": digest,
            "byte_count": len(payload),
        }
    )
    atomic_write_json(absolute.with_suffix(absolute.suffix + ".meta.json"), safe_metadata)
    return relative, digest
