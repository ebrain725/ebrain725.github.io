import hashlib
import io
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import krx_market_receipt as market


class KRMarketReceiptTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.public = Path(temp.name)
        self.today = date(2026, 11, 20)
        (self.public / market.DAILY_LATEST).parent.mkdir(parents=True)
        self.write_quotes("2026-11-19", 29550, 169916)
        self.write_representative()

    def write_quotes(self, trade_date, close, volume):
        path = self.public / market.PRICES
        path.write_text(
            "date,symbol,close,change,change_rate,open,high,low,volume,trade_value\n"
            f"{trade_date},KAU26,{close},0,0,29500,29550,29450,{volume},1\n",
            encoding="utf-8",
        )

    def write_representative(self):
        day = self.today.isoformat()
        pdf_url = f"data/krx-daily/pdfs/{day[:7]}/{day}.pdf"
        pdf_path = self.public / pdf_url
        pdf_path.parent.mkdir(parents=True)
        pdf_bytes = b"%PDF- mock archive for source metadata validation\n"
        pdf_path.write_bytes(pdf_bytes)
        digest = hashlib.sha256(pdf_bytes).hexdigest()
        report = {
            "tradeDate": day, "publishedDate": day, "bbsSeq": "710", "attachFileSeq": "1",
            "sourcePageUrl": "https://ets.krx.co.kr/contents/ETS/97/97010000/ETS97010000S2.jsp?bbstype=5&bbs_seq=710",
            "sourceKey": "krx:710:1", "sourceRevisionId": f"krx:710:1:{digest[:12]}",
            "pdfUrl": pdf_url, "fileSize": len(pdf_bytes), "sha256": digest,
            "market": {"representativeInstrument": {"symbol": "KAU26", "close": 29550,
                                                     "change": 0, "volume": 118410},
                       "totalVolume": 138410},
            "participantScope": "all_instruments", "taxonomyVersion": "financial_institutions_and_others",
            "participantFlows": [
                {"categoryKey": key, "label": label, "netByMethod": {"competitive": 0, "negotiated": 0,
                                                                     "auction": 0, "total": 0}}
                for key, label in (("liable_entities", "할당대상업체"), ("market_makers", "시장조성자"),
                                   ("financial_institutions", "금융기관"), ("others", "기타"),
                                   ("koc_specialists", "KOC전문회원"))
            ],
            "validation": {"netTotal": 0, "balanced": True},
        }
        market.write_json(self.public / market.DAILY_LATEST, {"item": report})
        return pdf_path

    def test_pdf_adds_only_representative_close_and_preserves_full_quotes(self):
        quotes = (self.public / market.PRICES).read_bytes()
        payload = market.build(self.public, self.today, {})
        self.assertEqual(payload["trade_date"], "2026-11-20")
        self.assertEqual(payload["coverage"], "representative_close_volume")
        self.assertEqual(payload["full_quotes"]["trade_date"], "2026-11-19")
        self.assertEqual(payload["representative_report"]["volume"], 118410)
        self.assertEqual(payload["representative_report"]["market_volume"], 138410)
        self.assertNotIn("open", payload["representative_report"])
        self.assertNotIn("trade_value", payload["representative_report"])
        self.assertEqual((self.public / market.PRICES).read_bytes(), quotes)

    def test_authorized_full_quote_replaces_pdf_for_same_symbol_date(self):
        self.write_quotes("2026-11-20", 29550, 118410)
        for source_type in ("authorized_open_api", "official_site"):
            attempt = {"status": "collected", "observed_trade_date": "2026-11-20",
                       "source_type": source_type, "workflow_run_id": "123"}
            payload = market.build(self.public, self.today, attempt)
            self.assertEqual(payload["coverage"], "full_quotes")
            self.assertEqual(payload["source_type"], source_type)
            self.assertEqual(payload["representative_report"]["full_quote_comparison"], "match")
        saved = (self.public / market.RECEIPT).read_bytes()
        market.build(self.public, self.today, {})
        self.assertEqual((self.public / market.RECEIPT).read_bytes(), saved)

    def test_disagreement_keeps_full_quote_and_surfaces_conflict(self):
        self.write_quotes("2026-11-20", 29700, 118410)
        payload = market.build(self.public, self.today, {})
        self.assertEqual(payload["coverage"], "full_quotes")
        self.assertEqual(payload["representative_report"]["full_quote_comparison"], "mismatch")

    def test_modified_pdf_cannot_enter_public_observation(self):
        report = market.read_json(self.public / market.DAILY_LATEST)["item"]
        (self.public / report["pdfUrl"]).write_bytes(b"%PDF- wrong revision")
        with self.assertRaisesRegex(ValueError, "archive metadata"):
            market.build(self.public, self.today, {})
        self.assertFalse((self.public / market.RECEIPT).exists())

    def test_auction_transport_stops_on_access_denial(self):
        opener = MagicMock()
        url = market.krx.KRX_SITE_PRICE_PAGE
        opener.open.side_effect = HTTPError(url, 403, "Forbidden", None, None)
        with patch.object(market.krx.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "403"):
                market.krx.site_open_text(opener, url)
        opener.open.assert_called_once()
        sleep.assert_not_called()

    def test_public_verification_checks_all_hashes(self):
        market.build(self.public, self.today, {})
        (self.public / market.DAILY_INDEX).write_text("{}\n")
        fetched = []

        def open_public(request, **_options):
            relative = urlsplit(request.full_url).path.lstrip("/")
            fetched.append(relative)
            return io.BytesIO((self.public / relative).read_bytes())

        with patch.object(market, "PUBLIC", self.public), patch.object(market.urllib.request, "urlopen", open_public):
            market.verify_public("https://example.github.io/", include_daily_index=True)
        report = market.read_json(self.public / market.DAILY_LATEST)["item"]
        self.assertCountEqual(fetched, [market.RECEIPT, market.PRICES, market.DAILY_LATEST, market.DAILY_INDEX, report["pdfUrl"]])


if __name__ == "__main__":
    unittest.main()
