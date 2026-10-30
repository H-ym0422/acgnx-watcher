from pathlib import Path
import unittest
from unittest.mock import patch

import acgnx_watch as aw
import add_rule as ar
from watcher_core.rss_search import fetch_search_feed


class RssSearchTests(unittest.TestCase):
    base = 'https://primary.invalid'
    alternate = 'https://alternate.invalid'
    empty = '<rss><channel><title>empty</title></channel></rss>'

    def setUp(self):
        self.fixture = (Path(__file__).parent / 'fixtures/releases.xml').read_text(encoding='utf-8')
        self.rule = {'id': 'demo', 'name': '示例动画', 'keywords': ['示例动画'],
                     'include_any': ['示例动画'], 'group': [], 'enabled': True}

    def test_new_endpoint_encodes_keyword_and_stops_after_valid_response(self):
        with patch.object(aw, 'http_get', return_value=self.fixture) as get:
            body = fetch_search_feed(self.base, '学生会 & S2', api=aw)
        self.assertEqual(body, self.fixture)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.args[0], self.base + '/rss.xml?keyword=%E5%AD%A6%E7%94%9F%E4%BC%9A%20%26%20S2')

    def test_html_response_falls_back_to_valid_mirror(self):
        with patch.object(aw, 'BASES', [self.base, self.alternate]), \
             patch.object(aw, 'http_get', side_effect=['<html>challenge</html>', self.fixture]) as get:
            self.assertEqual(fetch_search_feed(self.base, 'demo', api=aw), self.fixture)
        self.assertEqual(get.call_count, 2)
        self.assertTrue(get.call_args.args[0].startswith(self.alternate + '/rss.xml?keyword='))

    def test_legacy_endpoint_is_used_only_after_new_mirrors_fail(self):
        with patch.object(aw, 'BASES', [self.base, self.alternate]), \
             patch.object(aw, 'http_get', side_effect=[None, '<html/>', self.fixture]) as get:
            self.assertEqual(fetch_search_feed(self.base, 'demo', api=aw), self.fixture)
        self.assertEqual(get.call_count, 3)
        self.assertEqual(get.call_args.args[0], self.base + '/topics/rss/rss.xml?keyword=demo')

    def test_valid_empty_feed_is_not_a_request_failure(self):
        with patch.object(aw, 'http_get', return_value=self.empty) as get:
            self.assertEqual(fetch_search_feed(self.base, 'none', api=aw), self.empty)
        self.assertEqual(get.call_count, 1)

    def test_collection_restores_matching_and_query_health(self):
        with patch.object(aw, 'pick_base', return_value=(self.base, self.empty)), \
             patch.object(aw, 'http_get', return_value=self.fixture), patch.object(aw.time, 'sleep'):
            result = aw.collect({'settings': {}, 'rules': [self.rule]})
        self.assertTrue(result.global_feed_available)
        self.assertEqual(result.rule_sources['demo'], {'successful_sources': 1, 'failed_sources': 0})
        self.assertEqual(len(result), 3)

    def test_collection_distinguishes_failed_search_from_valid_empty_search(self):
        for response, expected in [(None, 0), (self.empty, 1)]:
            with self.subTest(response=response), \
                 patch.object(aw, 'pick_base', return_value=(self.base, self.empty)), \
                 patch.object(aw, 'http_get', return_value=response), patch.object(aw.time, 'sleep'):
                result = aw.collect({'settings': {}, 'rules': [self.rule]})
            self.assertTrue(result.available)
            self.assertTrue(result.global_feed_available)
            self.assertEqual(result.rule_sources['demo']['successful_sources'], expected)

    def test_group_discovery_uses_shared_search_and_rejects_total_failure(self):
        with patch.object(aw, 'pick_base', return_value=(self.base, self.empty)), \
             patch.object(aw, 'http_get', return_value=self.fixture) as get:
            self.assertEqual(len(ar.search('示例动画')), 3)
        self.assertTrue(all('/rss.xml?keyword=' in call.args[0] and '/topics/' not in call.args[0]
                            for call in get.call_args_list))
        with patch.object(aw, 'pick_base', return_value=(self.base, self.empty)), \
             patch.object(aw, 'http_get', return_value='<html>error</html>'):
            with self.assertRaisesRegex(RuntimeError, 'RSS 搜索接口不可用'):
                ar.search('示例动画')

    def test_search_warning_explains_latest_feed_is_still_available(self):
        state = {}
        with patch.object(aw, 'pick_base', return_value=(self.base, self.empty)), \
             patch.object(aw, 'http_get', return_value=None), patch.object(aw.time, 'sleep'):
            result = aw.collect({'settings': {}, 'rules': [self.rule]})
        for _ in range(2):
            self.assertEqual(aw.health.update_alerts(state, result, [self.rule], 3), [])
        title, body = aw.health.update_alerts(state, result, [self.rule], 3)[0]
        self.assertIn('搜索异常', title)
        self.assertIn('最新资源流仍可用', body)
        self.assertIn('查询、选组及历史补抓受影响', body)
