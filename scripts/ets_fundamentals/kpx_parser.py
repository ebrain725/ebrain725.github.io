from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from .core import CollectorError

KST = ZoneInfo("Asia/Seoul")


class KpxApiError(CollectorError):
    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class FetchResult:
    payload: bytes
    response_format: str
    endpoint_id: str
    content_type: str
    request_variant: str


@dataclass(frozen=True, slots=True)
class ParsedKpxSupply:
    observation_date: date
    records: tuple[Mapping[str, Any], ...]
    response_record_count: int
    warnings: tuple[str, ...]


METRIC_FIELDS: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    ("supply_capacity", "공급능력", ("suppAbility", "supplyAbility"), "MW"),
    ("system_demand", "현재수요", ("currPwrTot", "currentPowerTotal"), "MW"),
    (
        "forecast_peak_demand",
        "최대예측수요",
        ("forecastLoad", "forecastDemand", "forecastPeakDemand"),
        "MW",
    ),
    ("supply_reserve", "공급예비력", ("suppReservePwr", "supplyReservePower"), "MW"),
    ("supply_reserve_rate", "공급예비율", ("suppReserveRate", "supplyReserveRate"), "%"),
    ("operating_reserve", "운영예비력", ("operReservePwr", "operatingReservePower"), "MW"),
    (
        "operating_reserve_rate",
        "운영예비율",
        ("operReserveRate", "operatingReserveRate"),
        "%",
    ),
)
TIME_FIELDS = ("baseDatetime", "baseDateTime", "baseDttm", "baseDate", "baseDt")
SUCCESS_CODES = {"00", "0", "0000", "NORMAL_SERVICE", "NORMAL SERVICE", "OK"}
AUTH_ERROR_CODES = {"20", "30", "31"}
LIMIT_ERROR_CODES = {"22"}
RETRYABLE_API_CODES = {"01", "05", "23"}
RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}


def _strip_namespace(name: str) -> str:
    return name.rsplit("}", 1)[-1]


def _normalise_key(value: str) -> str:
    return _strip_namespace(value).replace("_", "").replace("-", "").lower()


def _lookup(record: Mapping[str, Any], aliases: Sequence[str]) -> Any | None:
    normalised = {_normalise_key(str(key)): value for key, value in record.items()}
    for alias in aliases:
        value = normalised.get(_normalise_key(alias))
        if value is not None:
            return value
    return None


def _parse_number(value: Any, *, field: str) -> float:
    if value is None:
        raise KpxApiError(f"Missing numeric field: {field}")
    text = str(value).strip().replace(",", "")
    if text.lower() in {"", "-", "null", "none", "nan"}:
        raise KpxApiError(f"Empty numeric field: {field}")
    try:
        return float(text)
    except ValueError as exc:
        raise KpxApiError(f"Invalid numeric field {field}: {value!r}") from exc


def _parse_observed_at(value: Any) -> datetime:
    text = str(value or "").strip()
    compact_formats = {
        14: "%Y%m%d%H%M%S",
        12: "%Y%m%d%H%M",
        10: "%Y%m%d%H",
        8: "%Y%m%d",
    }
    if text.isdigit() and len(text) in compact_formats:
        try:
            return datetime.strptime(text, compact_formats[len(text)]).replace(tzinfo=KST)
        except ValueError:
            pass
    for candidate in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text, candidate).replace(tzinfo=KST)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise KpxApiError(f"Unsupported baseDatetime value: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def _walk_json_records(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        time_value = _lookup(value, TIME_FIELDS)
        metric_hits = sum(
            _lookup(value, aliases) is not None for _, _, aliases, _ in METRIC_FIELDS
        )
        if time_value is not None and metric_hits >= 2:
            yield value
            return
        for child in value.values():
            yield from _walk_json_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json_records(child)


def _json_result_code(value: Any) -> tuple[str | None, str | None]:
    if isinstance(value, Mapping):
        code = _lookup(value, ("resultCode", "returnReasonCode"))
        message = _lookup(value, ("resultMsg", "resultMessage", "returnAuthMsg"))
        if code is not None:
            return str(code).strip(), str(message or "").strip()
        for child in value.values():
            found_code, found_message = _json_result_code(child)
            if found_code is not None:
                return found_code, found_message
    elif isinstance(value, list):
        for child in value:
            found_code, found_message = _json_result_code(child)
            if found_code is not None:
                return found_code, found_message
    return None, None


def _parse_json(payload: bytes) -> tuple[list[Mapping[str, Any]], str | None, str | None]:
    try:
        document = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KpxApiError("Response is not valid JSON") from exc
    code, message = _json_result_code(document)
    return list(_walk_json_records(document)), code, message


def _parse_xml(payload: bytes) -> tuple[list[Mapping[str, Any]], str | None, str | None]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise KpxApiError("Response is not valid XML") from exc

    code: str | None = None
    message: str | None = None
    for element in root.iter():
        tag = _normalise_key(element.tag)
        if tag in {_normalise_key("resultCode"), _normalise_key("returnReasonCode")} and code is None:
            code = (element.text or "").strip()
        if tag in {
            _normalise_key("resultMsg"),
            _normalise_key("resultMessage"),
            _normalise_key("returnAuthMsg"),
        } and message is None:
            message = (element.text or "").strip()

    records: list[Mapping[str, Any]] = []
    for element in root.iter():
        if _normalise_key(element.tag) != "item":
            continue
        record = {_strip_namespace(child.tag): (child.text or "").strip() for child in element}
        if _lookup(record, TIME_FIELDS) is not None:
            records.append(record)

    if not records:
        flat_record = {
            _strip_namespace(element.tag): (element.text or "").strip()
            for element in root.iter()
            if len(element) == 0
        }
        if _lookup(flat_record, TIME_FIELDS) is not None:
            records.append(flat_record)
    return records, code, message


def detect_response_format(payload: bytes, content_type: str = "") -> str:
    stripped = payload.lstrip()
    lowered = content_type.lower()
    if stripped.startswith((b"{", b"[")):
        return "json"
    if stripped.startswith(b"<"):
        return "xml"
    if "json" in lowered:
        return "json"
    if "xml" in lowered:
        return "xml"
    raise KpxApiError("Unable to detect API response format")


def _parse_payload_envelope(
    payload: bytes,
    content_type: str,
) -> tuple[list[Mapping[str, Any]], str | None, str | None, str]:
    response_format = detect_response_format(payload, content_type)
    if response_format == "json":
        records, code, message = _parse_json(payload)
    else:
        records, code, message = _parse_xml(payload)
    return records, code, message, response_format


def _raise_api_error(code: str | None, message: str | None) -> None:
    if code is not None and code.upper() not in SUCCESS_CODES:
        raise KpxApiError(
            f"KPX API error {code}: {message or 'No message'}",
            code=code,
        )


def parse_kpx_supply_payload(
    payload: bytes,
    *,
    content_type: str,
    expected_date: date | None = None,
) -> ParsedKpxSupply:
    records, code, message, _ = _parse_payload_envelope(payload, content_type)
    _raise_api_error(code, message)
    if not records:
        raise KpxApiError("KPX response contains no power-supply records")

    parsed_records: list[tuple[datetime, Mapping[str, Any]]] = []
    warnings: list[str] = []
    for raw_record in records:
        try:
            observed_at = _parse_observed_at(_lookup(raw_record, TIME_FIELDS))
        except KpxApiError as exc:
            warnings.append(f"discarded_invalid_timestamp:{exc}")
            continue
        parsed_records.append((observed_at, raw_record))
    if not parsed_records:
        raise KpxApiError("KPX response contains no record with a valid timestamp")

    selected_date = expected_date or max(observed_at.date() for observed_at, _ in parsed_records)
    selected = [
        (observed_at, raw_record)
        for observed_at, raw_record in parsed_records
        if observed_at.date() == selected_date
    ]
    discarded_dates = sorted(
        {observed_at.date().isoformat() for observed_at, _ in parsed_records if observed_at.date() != selected_date}
    )
    if discarded_dates:
        warnings.append("discarded_dates:" + ",".join(discarded_dates))
    if not selected:
        available_dates = sorted({observed_at.date().isoformat() for observed_at, _ in parsed_records})
        raise KpxApiError(
            "KPX response did not contain the expected date; "
            f"expected={selected_date.isoformat()}, available={','.join(available_dates)}"
        )

    normalised_by_time: dict[str, dict[str, Any]] = {}
    for observed_at, raw_record in selected:
        normalised: dict[str, Any] = {
            "observed_at": observed_at.astimezone(KST).replace(microsecond=0).isoformat()
        }
        try:
            for metric_id, _, aliases, _ in METRIC_FIELDS:
                normalised[metric_id] = _parse_number(
                    _lookup(raw_record, aliases),
                    field=metric_id,
                )
        except KpxApiError as exc:
            warnings.append(f"discarded_incomplete_record:{normalised['observed_at']}:{exc}")
            continue
        normalised_by_time[normalised["observed_at"]] = normalised

    if not normalised_by_time:
        raise KpxApiError("KPX records did not contain a complete supported row")
    normalised_records = tuple(normalised_by_time[key] for key in sorted(normalised_by_time))
    return ParsedKpxSupply(
        observation_date=selected_date,
        records=normalised_records,
        response_record_count=len(selected),
        warnings=tuple(sorted(set(warnings))),
    )
