"""Prevent policy archive aliases and stale files from distorting publication."""
import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import publish_policy_snapshot as publication


class PolicySnapshotTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        self.official = {
            "section": "notice", "id": "notice", "sourceType": "official", "source": "기후부 공지사항",
            "title": "배출권 유상경매 물량 공고", "publishedAt": "2026-11-19",
            "url": "https://www.mcee.go.kr/board/read.do?boardId=1906260",
        }
        self.policy = {"items": [self.official], "lastSync": "2026-11-20T13:23:52+09:00"}
        archive_alias = dict(self.official, url="https://mcee.go.kr/board/read.do?searchValue=ETS&boardId=1906260")
        self.write(publication.POLICIES, self.policy)
        self.write("policy-official-history.json", {"items": [archive_alias, self.official]})
        for name in ("motie-policy-history.json", "bills.json", "assembly_seminars.json"):
            self.write(name, {"items": []})

    def write(self, name, payload):
        (self.data / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def read(self, name):
        return json.loads((self.data / name).read_bytes())

    def publish(self):
        return publication.publish_snapshot(self.data, publication.TAXONOMY_PATH, publication.TEMPLATE_B64)

    def test_summary_and_excel_use_exact_policy_snapshot(self):
        source = self.publish()
        policy_file = self.data / publication.POLICIES
        self.assertEqual(source["sha256"], hashlib.sha256(policy_file.read_bytes()).hexdigest())
        self.assertEqual(source["latestOfficialPublishedAt"], "2026-11-19")
        self.assertEqual(source["lastSync"], "2026-11-20T13:23:52+09:00")
        self.assertEqual(source["officialItemCount"], 1)
        manifest = self.read(publication.MANIFEST)
        summary = self.read(publication.SUMMARY)
        self.assertEqual(summary["policySource"], manifest["policySource"])
        self.assertEqual(manifest["counts"]["기후부 공지사항"], summary["sectionCounts"]["notice"])
        actual = self.read(publication.POLICIES)["items"][0]
        with zipfile.ZipFile(self.data / publication.WORKBOOK) as workbook:
            guide = workbook.read("xl/worksheets/sheet1.xml").decode()
            notice = workbook.read("xl/worksheets/sheet4.xml").decode()
        self.assertIn(source["sha256"], guide)
        self.assertIn(actual["eventPrimary"], notice)

    def test_verification_rejects_mixed_policy_snapshot_and_modified_workbook(self):
        self.publish()
        policy_path = self.data / publication.POLICIES
        original = policy_path.read_bytes()
        policy_path.write_bytes(original + b" ")
        with self.assertRaisesRegex(RuntimeError, "원본 SHA-256"):
            publication.verify_snapshot(self.data)
        policy_path.write_bytes(original)
        (self.data / publication.FIXED_WORKBOOK).write_bytes(b"stale")
        with self.assertRaisesRegex(RuntimeError, "엑셀 SHA-256"):
            publication.verify_snapshot(self.data)

    def test_check_rejects_archive_identity_missing_from_policy_list(self):
        self.write(publication.POLICIES, {"items": []})
        with self.assertRaisesRegex(RuntimeError, "원문 ID 1건"):
            self.publish()


if __name__ == "__main__":
    unittest.main()
