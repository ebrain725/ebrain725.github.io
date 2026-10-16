#!/usr/bin/env python3
"""Use the approved KRX API, not access-control workarounds, if site collection fails."""
from __future__ import annotations
import os
from datetime import datetime
import sync_krx as krx


def sync_today() -> int:
    today = datetime.now(krx.KST).date()
    symbol = os.getenv('KAU_SYMBOL', '').strip()
    site_error = None
    try:
        result = krx.sync_site_today(symbol)
        if any(r['date'] == today.isoformat() for r in krx.read_existing()):
            return result
    except RuntimeError as exc:
        # Do not print URLs containing credentials or attempt alternate IPs/headers.
        text = str(exc)
        site_error = 'HTTP_403' if '403' in text else 'SITE_FETCH_ERROR'
        print(f'KRX_SITE_RESULT={site_error}; checking the existing authorized KRX API for the exact requested date.')
    auth_key = os.getenv('KRX_AUTH_KEY', '').strip()
    if not auth_key:
        raise RuntimeError('KRX_API_CREDENTIAL_MISSING')
    raw = krx.fetch_rows(today, auth_key)
    rows = krx.kau_rows(raw, symbol)
    # BAS_DD is checked when supplied. No previous-day search or date substitution.
    expected = today.strftime('%Y%m%d')
    for row in rows:
        reported = str(row.get('BAS_DD') or row.get('basDd') or '').replace('-', '').replace('/', '')
        if reported and reported != expected:
            raise RuntimeError('KRX_API_REQUEST_RESPONSE_DATE_MISMATCH')
    if not rows:
        print(f'::warning::KRX_API_NO_SAME_DAY_DATA: {today.isoformat()}. 원장 가격·날짜를 변경하지 않습니다. 웹 결과={site_error or "SOURCE_NOT_CURRENT"}')
        return 0
    existing = krx.read_existing()
    updates = [krx.csv_row(today, row, existing) for row in rows]
    krx.write_updates(existing, updates)
    print('KRX_SOURCE=AUTHORIZED_OPEN_API; REQUEST_DATE=' + expected)
    for row in updates:
        print(f"KRX_SAME_DAY {row['date']} {row['symbol']} close={row['close']} volume={row['volume']}")
    return 0


if __name__ == '__main__':
    mode = os.getenv('KRX_SYNC_MODE', 'openapi').strip().lower()
    raise SystemExit(sync_today() if mode in {'site-today', 'site', 'today'} else krx.main())
