import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import merge_official_policy_history as merger
import sync_climate_policy_expanded as climate
import sync_policies as regular
from policy_integrity import assert_preserved, official_key


def post(board_id, **changes):
    item = dict(title="정책 자료", publishedAt="2026-06-19", source="기후부 보도자료",
                section="press", sourceType="official", summary="기존 수집 본문",
                matchedKeywords=["탄소시장"],
                url=f"https://www.mcee.go.kr/home/web/board/read.do?boardId={board_id}&boardMasterId=939")
    item.update(changes)
    return item


class PolicyRetentionTests(unittest.TestCase):
    def test_search_url_and_domain_variants_are_one_post(self):
        a = post(1871870)
        b = post(1871870, url="http://me.go.kr/home/web/board/read.do;jsessionid=abc?searchValue=test&boardId=1871870&menuId=10598")
        self.assertEqual(official_key(a), official_key(b))
        self.assertEqual(len(regular._unique_non_news([{"items": [a, b]}])), 1)

    def test_equal_count_replacement_is_blocked(self):
        with self.assertRaisesRegex(RuntimeError, "1871870"):
            assert_preserved([post(1871870)], [post(1895690)])

    def test_growth_cannot_hide_a_missing_post(self):
        with self.assertRaises(RuntimeError):
            assert_preserved([post(1871870)], [post(1), post(2)])

    def test_deduplication_does_not_trigger_loss(self):
        assert_preserved([post(1), post(1)], [post(1)])

    def test_public_only_post_survives_keyword_change(self):
        result = climate.retained_items([post(1)], [post(1871870)], [], "2015-01-01")
        self.assertEqual({official_key(x) for x in result}, {"climate|1", "climate|1871870"})
        self.assertEqual(result[0]["matchedKeywords"], ["탄소시장"])

    def test_richer_body_and_keyword_evidence_survive(self):
        old = post(1, summary="보존되어야 하는 충분히 긴 본문", matchedKeywords=["탄소시장"])
        new = post(1, summary="짧음", matchedKeywords=["배출권"])
        result = climate.retained_items([old], [], [new], "2015-01-01")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["summary"], old["summary"])
        self.assertEqual(set(result[0]["matchedKeywords"]), {"탄소시장", "배출권"})

    def test_industry_board_ids_do_not_collide(self):
        a = post(1, url="https://www.motir.go.kr/kor/article/ATCL3f49a5a8c/123/view")
        b = post(1, url="https://www.motir.go.kr/kor/article/ATCL2826a2625/123/view")
        self.assertNotEqual(official_key(a), official_key(b))
        self.assertEqual(regular._section(a), "motie_press")

    def test_krx_fragment_ids_do_not_collide(self):
        a = post(1, section="krx_notice", url="https://ets.krx.co.kr/contents#view=123")
        b = post(1, section="krx_notice", url="https://ets.krx.co.kr/contents#view=124")
        self.assertNotEqual(official_key(a), official_key(b))

    def test_climate_merge_preserves_industry_and_news(self):
        industry = post(1, section="motie_press", source="산업부 보도자료",
                        url="https://www.motir.go.kr/kor/article/ATCL3f49a5a8c/123/view")
        misclassified = dict(industry, section="press", source="기후부 보도자료")
        news = post(2, section="news", sourceType="news")
        before = [post(1871870), industry, misclassified, news]
        with tempfile.TemporaryDirectory() as directory:
            policy_path, history_path = Path(directory) / "policy.json", Path(directory) / "history.json"
            policy_path.write_text(json.dumps({"items": before, "lastSync": "unchanged"}))
            history_path.write_text(json.dumps({"items": [post(1)]}))
            merger.merge_files(policy_path, history_path)
            result = json.loads(policy_path.read_text())
            history = json.loads(history_path.read_text())
            assert_preserved(before, result["items"])
            self.assertEqual(len(result["items"]), 4)
            self.assertEqual(len(history["items"]), 2)
            self.assertEqual(result["lastSync"], "unchanged")
            self.assertEqual(sum(x["section"] == "motie_press" for x in result["items"]), 1)
            merger.merge_files(policy_path, history_path)
            self.assertEqual(json.loads(policy_path.read_text())["items"], result["items"])


if __name__ == "__main__":
    unittest.main()
