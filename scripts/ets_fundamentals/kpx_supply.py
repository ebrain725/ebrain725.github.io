from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping

from .core import DatasetSpec, FieldDefinition
from .kpx_parser import (
    AUTH_ERROR_CODES,
    LIMIT_ERROR_CODES,
    METRIC_FIELDS,
    RETRYABLE_API_CODES,
    KpxApiError,
    ParsedKpxSupply,
    _parse_payload_envelope,
    _raise_api_error,
    detect_response_format,
    parse_kpx_supply_payload,
)

RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}


@dataclass(frozen=True, slots=True)
class FetchResult:
    payload: bytes
    response_format: str
    endpoint_id: str
    content_type: str
    request_variant: str


def build_spec(source_config: Mapping[str, Any]) -> DatasetSpec:
    return DatasetSpec(
        source_id=str(source_config["id"]),
        source_name=str(source_config["name_ko"]),
        organization=str(source_config["organization"]),
        category="power_energy",
        sub_category="power_supply",
        frequency="5min",
        geography="KR",
        key_fields=("observed_at",),
        dimensions=(),
        metrics=tuple(
            FieldDefinition(id=metric_id, name_ko=name_ko, data_type="number", unit=unit)
            for metric_id, name_ko, _, unit in METRIC_FIELDS
        ),
        expected_records_per_day=288,
        dataset_page=(
            None
            if source_config.get("dataset_page") is None
            else str(source_config.get("dataset_page"))
        ),
    )


def _decoded_service_key(value: str) -> str:
    # The portal exposes encoded and decoded variants. Decode once, then let
    # urllib encode the query string exactly once.
    return urllib.parse.unquote(value.strip())


def _request_bytes(url: str, *, timeout_seconds: int, retries: int) -> tuple[bytes, str]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/xml, text/xml;q=0.9, application/json;q=0.8",
            "User-Agent": "ets-fundamentals-collector/0.1 (+https://ebrain725.github.io)",
        },
        method="GET",
    )
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return response.read(), response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRYABLE_HTTP_CODES or attempt >= retries:
                raise KpxApiError(f"KPX HTTP error {exc.code}") from exc
            last_error = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt >= retries:
                raise KpxApiError(f"KPX network error: {exc}") from exc
            last_error = exc
        time.sleep(2**attempt)
    raise KpxApiError(f"KPX request failed: {last_error}")


class KpxSupplyTodayAdapter:
    """Adapter for the KPX GW API returning today's 5-minute supply history."""

    def __init__(self, source_config: Mapping[str, Any]) -> None:
        self.config = source_config
        self.spec = build_spec(source_config)
        self.endpoint_candidates = tuple(str(value) for value in source_config["endpoint_candidates"])
        raw_variants = source_config.get("query_variants", [{"dataType": "xml"}, {}])
        if not isinstance(raw_variants, list):
            raise KpxApiError("query_variants must be a list")
        self.query_variants = tuple(
            {str(key): str(value) for key, value in variant.items()}
            for variant in raw_variants
            if isinstance(variant, Mapping)
        )
        self.timeout_seconds = int(source_config.get("timeout_seconds", 30))
        self.retries = int(source_config.get("retries", 2))

    def fetch(self, *, service_key: str) -> FetchResult:
        if not service_key.strip():
            raise KpxApiError("KPX_OPENAPI_SERVICE_KEY is empty", code="MISSING_SECRET")
        decoded_key = _decoded_service_key(service_key)
        attempts: list[str] = []

        for endpoint_index, endpoint in enumerate(self.endpoint_candidates, start=1):
            for variant_index, extra_params in enumerate(self.query_variants, start=1):
                params = {**extra_params, "serviceKey": decoded_key}
                query = urllib.parse.urlencode(params)
                url = f"{endpoint}{'&' if '?' in endpoint else '?'}{query}"
                variant_name = f"endpoint_{endpoint_index}:query_{variant_index}"
                for api_attempt in range(self.retries + 1):
                    try:
                        payload, content_type = _request_bytes(
                            url,
                            timeout_seconds=self.timeout_seconds,
                            retries=self.retries,
                        )
                        records, code, message, response_format = _parse_payload_envelope(
                            payload,
                            content_type,
                        )
                        _raise_api_error(code, message)
                        if not records:
                            raise KpxApiError("KPX response contains no power-supply records")
                        parse_kpx_supply_payload(payload, content_type=content_type)
                        return FetchResult(
                            payload=payload,
                            response_format=response_format,
                            endpoint_id=f"kpx_today_gw_{endpoint_index}",
                            content_type=content_type,
                            request_variant=variant_name,
                        )
                    except KpxApiError as exc:
                        attempts.append(f"{variant_name}:attempt_{api_attempt + 1}={exc}")
                        if exc.code in AUTH_ERROR_CODES or exc.code in LIMIT_ERROR_CODES:
                            raise
                        if exc.code in RETRYABLE_API_CODES and api_attempt < self.retries:
                            time.sleep(2**api_attempt)
                            continue
                        break

        raise KpxApiError("All KPX request variants failed: " + " | ".join(attempts))

    def parse(
        self,
        fetch_result: FetchResult,
        *,
        expected_date: date | None = None,
    ) -> ParsedKpxSupply:
        return parse_kpx_supply_payload(
            fetch_result.payload,
            content_type=fetch_result.content_type,
            expected_date=expected_date,
        )
