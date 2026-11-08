"""Public-page negotiation, bounded retries and honest access failures."""
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import sync_krx_daily_trends as daily


class DailyTransportTests(unittest.TestCase):
    def test_bootstrap_keeps_official_url_cookies_and_html_negotiation(self):
        opener = MagicMock()
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'<html>public page</html>'
        opener.open.return_value = response
        with patch.object(daily.urllib.request, 'build_opener', return_value=opener) as build:
            daily.KrxSession()
        self.assertIsInstance(build.call_args.args[0], daily.urllib.request.HTTPCookieProcessor)
        headers = dict(opener.addheaders)
        self.assertIn('Chrome/', headers['User-Agent'])
        self.assertIn('text/html', headers['Accept'])
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, daily.KRX_LIST_PAGE)
        self.assertTrue(request.full_url.startswith('https://ets.krx.co.kr/'))

    def session_without_bootstrap(self):
        session = daily.KrxSession.__new__(daily.KrxSession)
        session.opener = MagicMock()
        return session

    def test_access_denial_is_not_retried_or_returned_as_empty_data(self):
        session = self.session_without_bootstrap()
        session.opener.open.side_effect = urllib.error.HTTPError(
            daily.KRX_LIST_PAGE, 403, 'Forbidden', {}, io.BytesIO(b'access denied'))
        with patch.object(daily.time, 'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, 'KRX HTTP 403'):
                session.open_bytes(daily.KRX_LIST_PAGE)
        self.assertEqual(session.opener.open.call_count, 1)
        sleep.assert_not_called()

    def test_transient_errors_use_existing_bounded_retry(self):
        session = self.session_without_bootstrap()
        session.opener.open.side_effect = urllib.error.HTTPError(
            daily.KRX_LIST_PAGE, 503, 'Unavailable', {}, io.BytesIO(b''))
        with patch.object(daily.time, 'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, 'KRX 연결 실패'):
                session.open_bytes(daily.KRX_LIST_PAGE)
        self.assertEqual(session.opener.open.call_count, 4)
        self.assertEqual(sleep.call_count, 3)


if __name__ == '__main__':
    unittest.main()
