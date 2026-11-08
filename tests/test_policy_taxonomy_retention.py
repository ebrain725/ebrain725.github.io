"""Keep source identities without inventing current taxonomy matches."""
from collections import Counter
import copy
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import sync_policy_event_taxonomy as sync


class TaxonomyRetentionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.taxonomy = sync.load_taxonomy(sync.TAXONOMY_PATH)

    def item(self, section, source_id, matched=True):
        hosts = {'press': 'www.mcee.go.kr', 'notice': 'www.mcee.go.kr',
                 'krx_notice': 'ets.krx.co.kr', 'motie_press': 'www.motir.go.kr',
                 'motie_notice': 'www.motir.go.kr'}
        return dict(section=section, sourceId=source_id, publishedAt='2026-11-01',
                    url='https://'+hosts[section]+'/article/'+source_id,
                    title='배출권 유상경매 물량 공고' if matched else '일반 주택 화재 예방대책',
                    summary='공식 원문 보존용 본문', eventTypes=['이전 분류'],
                    eventPrimary='이전 분류', keywordScope='related', taxonomyVersion='old')

    def data(self, unmatched=4):
        original = [self.item(section, section) for section in sorted(sync.ALL_OFFICIAL_SECTIONS)]
        original += [self.item('notice', 'old-'+str(i), False) for i in range(unmatched)]
        enriched = sync.enrich_existing(original, self.taxonomy, sections=sync.ALL_OFFICIAL_SECTIONS)
        official = dict(items=[x for x in enriched if x['section'] in sync.OFFICIAL_SECTIONS])
        industry = dict(items=[x for x in enriched if x['section'] in sync.MOTIE_SECTIONS])
        keys = {sync.record_key(x) for x in original}
        counts = dict(Counter(x['section'] for x in original))
        return original, enriched, official, industry, keys, counts

    def test_reclassification_preserves_unmatched_raw_document_and_previous_labels(self):
        raw = self.item('notice', 'old', False)
        snapshot = copy.deepcopy(raw)
        result = sync.enrich_existing([raw], self.taxonomy, sections={'notice'})[0]
        self.assertEqual(raw, snapshot)
        for field in ('sourceId', 'url', 'title', 'summary', 'publishedAt'):
            self.assertEqual(result[field], raw[field])
        self.assertFalse(sync.is_classified(result))
        self.assertEqual(result['previousTaxonomyClassification']['eventTypes'], ['이전 분류'])
        self.assertTrue(sync.retained_unmatched(result, self.taxonomy, {sync.record_key(raw)}))

    def test_four_unmatched_documents_are_retained_and_reported_separately(self):
        old, all_items, official, industry, keys, counts = self.data()
        validation = sync.validate_all(self.taxonomy, official, industry,
                                       dict(items=all_items), counts, keys)
        self.assertEqual(validation['officialItemCount'], len(old))
        self.assertEqual(validation['retainedUnmatchedItemCount'], 4)
        self.assertEqual(validation['taxonomyMatchedItemCount'], 5)
        self.assertEqual(validation['unclassified'], 0)
        self.assertEqual({sync.record_key(x) for x in all_items}, keys)

    def test_same_count_replacement_cannot_hide_a_missing_prior_document(self):
        old, all_items, official, industry, keys, counts = self.data(0)
        official['items'][0] = dict(official['items'][0], sourceId='replacement')
        with self.assertRaisesRegex(RuntimeError, '기존 원문 ID'):
            sync.validate_all(self.taxonomy, official, industry,
                              dict(items=official['items']+industry['items']), counts, keys)

    def test_new_unmatched_document_cannot_claim_prior_admission(self):
        old, all_items, official, industry, keys, counts = self.data(0)
        fake = sync.enrich_existing([self.item('notice', 'new-fake', False)],
                                    self.taxonomy, sections={'notice'})[0]
        official['items'].append(fake)
        with self.assertRaisesRegex(RuntimeError, '미분류=1'):
            sync.validate_all(self.taxonomy, official, industry,
                              dict(items=all_items+[fake]), counts, keys)

    def test_current_match_cannot_be_silently_marked_unmatched(self):
        raw = self.item('notice', 'old', False)
        result = sync.enrich_existing([raw], self.taxonomy, sections={'notice'})[0]
        result['title'] = '배출권 유상경매 물량 공고'
        self.assertFalse(sync.retained_unmatched(result, self.taxonomy, {sync.record_key(raw)}))

    def test_richer_new_evidence_can_restore_a_real_classification(self):
        raw = self.item('notice', 'old', False)
        retained = sync.enrich_existing([raw], self.taxonomy, sections={'notice'})[0]
        new = dict(raw, summary='배출권 유상경매 물량 및 일정 발표를 설명하는 새로운 공식 본문')
        result = sync.merge_record(retained, new, self.taxonomy)
        self.assertTrue(sync.is_classified(result))
        self.assertNotIn('taxonomyRetention', result)

    def test_full_refresh_does_not_erase_documents_missing_from_new_listing(self):
        original = [self.item(section, section) for section in sorted(sync.OFFICIAL_SECTIONS)]
        fake_collector = types.SimpleNamespace(load_climate_sources=lambda: [])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'history.json'
            path.write_text(json.dumps(dict(items=original)))
            with patch.dict(sys.modules, sync_climate_policy_expanded=fake_collector), \
                 patch.object(sync, 'OFFICIAL_HISTORY_PATH', path):
                result, _ = sync.run_climate(self.taxonomy, start_date='2015-01-01',
                                             full=True, lookback_days=60)
        self.assertEqual({sync.record_key(x) for x in result['items']},
                         {sync.record_key(x) for x in original})


if __name__ == '__main__':
    unittest.main()
