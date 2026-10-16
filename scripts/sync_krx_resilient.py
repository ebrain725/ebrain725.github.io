#!/usr/bin/env python3
"""Collect today's quotes without equating access/schema errors with no data."""
from __future__ import annotations
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
import sync_krx as krx


class APIResponseError(RuntimeError):
    """Contains only a safe diagnostic, never authentication values."""
    def __init__(self, diagnostic):
        self.diagnostic = diagnostic
        super().__init__('KRX_API_' + diagnostic['result'])


def decode_api_response(status, body, requested_day):
    info = {'requested_date': requested_day.isoformat(), 'http_status': status,
            'response_bytes': len(body), 'response_sha256': hashlib.sha256(body).hexdigest()}
    if status != 200:
        info['result'] = 'HTTP_' + str(status)
        raise APIResponseError(info)
    try:
        payload = json.loads(body.decode('utf-8-sig'))
    except (ValueError, UnicodeError):
        info['result'] = 'NON_JSON_RESPONSE'
        raise APIResponseError(info) from None
    if not isinstance(payload, dict) or 'OutBlock_1' not in payload:
        info['result'] = 'EXPECTED_PRICE_FIELD_MISSING'
        raise APIResponseError(info)
    rows = payload['OutBlock_1']
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        info['result'] = 'INVALID_PRICE_LIST'
        raise APIResponseError(info)
    info.update(result='PRICE_LIST_RECEIVED' if rows else 'HTTP_200_EMPTY_PRICE_LIST',
                response_rows=len(rows))
    return rows, info


def api_rows_exact(day, auth_key):
    # Use the existing approved API host only; never forward AUTH_KEY to redirects.
    base = urllib.parse.urlsplit(krx.KRX_URL)
    if (base.scheme != 'https' or base.hostname != 'data-dbg.krx.co.kr'
            or base.path != '/svc/apis/gen/ets_bydd_trd' or base.query or base.fragment
            or base.username or base.password):
        raise RuntimeError('KRX_API_UNEXPECTED_ENDPOINT')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    request = urllib.request.Request(krx.KRX_URL + '?' + urllib.parse.urlencode({'basDd': day.strftime('%Y%m%d')}),
        headers={'AUTH_KEY': auth_key, 'Accept': 'application/json', 'User-Agent': 'ETS-SIGNAL/1.0'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            status, body = response.status, response.read(2_000_001)
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read(100_000)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise APIResponseError({'requested_date': day.isoformat(), 'http_status': None,
                                'result': 'TRANSPORT_ERROR', 'error_class': type(exc).__name__}) from None
    if len(body) > 2_000_000:
        raise APIResponseError({'requested_date': day.isoformat(), 'http_status': status, 'result': 'RESPONSE_TOO_LARGE'})
    try:
        rows, info = decode_api_response(status, body, day)
    except APIResponseError as exc:
        print('KRX_API_RESPONSE ' + json.dumps(exc.diagnostic, ensure_ascii=False))
        raise
    print('KRX_API_RESPONSE ' + json.dumps(info, ensure_ascii=False))
    return rows, info


def valid_dated_rows(raw, day, symbol):
    rows = krx.kau_rows(raw, symbol)
    seen = set()
    for row in rows:
        name = str(row.get('ISU_NM', '')).strip().upper()
        observed = str(row.get('BAS_DD') or row.get('basDd') or '').replace('-', '').replace('/', '')
        if observed != day.strftime('%Y%m%d'):
            raise RuntimeError('KRX_API_RESPONSE_DATE_MISSING_OR_MISMATCH')
        if name in seen:
            raise RuntimeError('KRX_API_DUPLICATE_DATE_SYMBOL')
        seen.add(name)
    return rows


def sync_today() -> int:
    today = datetime.now(krx.KST).date()
    symbol = os.getenv('KAU_SYMBOL', '').strip()
    site_error = None
    # Only one normal page attempt in this run; no access-control bypass or retry storm.
    opener = krx.site_opener()
    try:
        krx.site_open_text(opener, krx.KRX_SITE_PRICE_PAGE, attempts=1)
        market_day = krx.site_market_day(opener)
        if market_day == today:
            raw = krx.site_kau_rows(krx.fetch_site_rows(opener, market_day), symbol)
            if not raw:
                raise RuntimeError('KRX_SITE_NO_MATCHING_PRICE_ROWS')
            existing = krx.read_existing()
            updates = [krx.site_csv_row(market_day, row, existing) for row in raw]
            krx.write_updates(existing, updates)
            print('KRX_SOURCE=OFFICIAL_SITE; REQUEST_DATE=' + today.isoformat())
            print('KRX_VERIFIED_SAME_DAY_ROWS ' + json.dumps(updates, ensure_ascii=False))
            return 0
        site_error = 'SITE_MARKET_DATE_' + market_day.isoformat()
    except RuntimeError as exc:
        site_error = 'HTTP_403' if re.search(r'\b403\b', str(exc)) else 'SITE_FETCH_ERROR'
    print('KRX_SITE_RESULT=' + site_error)
    auth_key = os.getenv('KRX_AUTH_KEY', '').strip()
    if not auth_key:
        raise RuntimeError('KRX_API_CREDENTIAL_MISSING')
    raw, info = api_rows_exact(today, auth_key)
    rows = valid_dated_rows(raw, today, symbol)
    if not rows:
        # A single past-day control identifies working credentials/schema, without saving
        # or relabeling those past-day rows as today's observations.
        previous = sorted({r['date'] for r in krx.read_existing()
                           if r.get('symbol', '').startswith('KAU') and r['date'] < today.isoformat()})
        if previous:
            control_day = date.fromisoformat(previous[-1])
            control_raw, control = api_rows_exact(control_day, auth_key)
            control_rows = valid_dated_rows(control_raw, control_day, symbol)
            print('KRX_PREVIOUS_DAY_CONTROL ' + json.dumps({'date': control_day.isoformat(),
                'http_status': control['http_status'], 'response_rows': control['response_rows'],
                'valid_kau_rows': len(control_rows), 'prices_not_modified': True}, ensure_ascii=False))
        print('::warning::KRX_TODAY_NOT_COLLECTED: ' + today.isoformat()
              + '; API=' + info['result'] + '; website=' + site_error
              + '. This is not a claim that the KRX website has no price.')
        return 0
    existing = krx.read_existing()
    updates = [krx.csv_row(today, row, existing) for row in rows]
    krx.write_updates(existing, updates)
    print('KRX_SOURCE=AUTHORIZED_OPEN_API; REQUEST_DATE=' + today.strftime('%Y%m%d'))
    print('KRX_VERIFIED_SAME_DAY_ROWS ' + json.dumps(updates, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    mode = os.getenv('KRX_SYNC_MODE', 'openapi').strip().lower()
    raise SystemExit(sync_today() if mode in {'site-today', 'site', 'today'} else krx.main())
