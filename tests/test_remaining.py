import copy
import json
import logging
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

import acgnx_watch as aw
import add_rule as ar
import issue_commands as ic
from watcher_core import health, rules, state_store
from watcher_core import notifications
from watcher_core.configuration import ConfigError, validate_config


class RuleAndStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / 'config.json'
        self.path = Path(self.tmp.name) / 'state.json'
        self.rule = {'id': 'existing', 'name': 'demo', 'keywords': ['demo'], 'group': ['A'], 'season': 1}
        self.config.write_text(json.dumps({'settings': {}, 'rules': [self.rule]}), encoding='utf-8')

    def test_duplicate_add_preserves_original_id_and_bytes(self):
        before = self.config.read_bytes()
        duplicate = dict(self.rule, id='new')
        self.assertFalse(rules.append(self.config, duplicate))
        self.assertEqual(self.config.read_bytes(), before)

    def test_different_season_or_group_allowed_and_ids_unique(self):
        for change in [{'season': 2}, {'group': ['B']}]:
            rule = dict(self.rule, **change)
            rule.pop('id')
            self.assertTrue(rules.append(self.config, rule))
        ids = [r['id'] for r in json.loads(self.config.read_text())['rules']]
        self.assertEqual(len(set(ids)), 3)

    def test_rule_edit_pause_resume_preserve_identity(self):
        for enabled in [False, True]:
            rid, changed = rules.edit(self.config, 'existing', {'enabled': enabled})
            self.assertEqual(rid, 'existing')
            self.assertFalse(changed)
        rules.edit(self.config, 'existing', {'from_episode': 49, 'group': ['B']})
        rule = json.loads(self.config.read_text())['rules'][0]
        self.assertEqual((rule['id'], rule['from_episode'], rule['group']), ('existing', 49, ['B']))

    def test_cli_management_keeps_state_and_renders_paused_rule(self):
        state_store.write(self.path, {'episodes': {'existing|01|A': '0:1'}, 'initialized': True})
        with patch.object(aw, 'CONFIG_PATH', str(self.config)), patch.object(aw, 'STATE_PATH', str(self.path)):
            for args in [['--pause','existing'], ['--resume','existing'],
                         ['--edit','existing','--group','B','--from-episode','49']]:
                with patch.object(sys, 'argv', ['add_rule.py', *args, '--no-push']):
                    ar.main()
                self.assertIn('existing|01|A', aw.load_state()['episodes'])
            text = ar.render_watchlist()
            self.assertIn('existing', text)
            self.assertIn('最近检查', text)
        rule = json.loads(self.config.read_text())['rules'][0]
        self.assertEqual(rule['id'], 'existing')
        self.assertEqual(rule['from_episode'], 49)

    def test_invalid_config_has_precise_field_and_no_write(self):
        before = self.config.read_bytes()
        with self.assertRaisesRegex(ConfigError, r'rules\[0\].from_episode'):
            rules.edit(self.config, 'existing', {'from_episode': -1})
        self.assertEqual(self.config.read_bytes(), before)
        with self.assertRaisesRegex(ConfigError, 'request_timeout'):
            validate_config({'settings': {'request_timeout': '15'}, 'rules': []})

    def test_legacy_state_migration_and_group_alias_do_not_lose_seen(self):
        state = state_store.migrate({'episodes': {'r|01': '0:2', 'r|02|Group-A': '1:1'}, 'initialized': True})
        self.assertEqual(state['state_version'], 1)
        self.assertEqual(state_store.previous_episode(state, 'r', '01', 'new group', lambda a,b: a == b), '0:2')
        self.assertEqual(state_store.previous_episode(state, 'r', '02', 'group a', lambda a,b: rules.normalized(a) == rules.normalized(b)), '1:1')

    def test_corrupt_primary_recovers_valid_backup_without_read_write(self):
        state_store.write(self.path, {'episodes': {'r|01': '0:1'}, 'initialized': True})
        state_store.write(self.path, {'episodes': {'r|01': '0:1', 'r|02': '0:1'}, 'initialized': True})
        self.path.write_text('broken', encoding='utf-8')
        recovered = state_store.read(self.path, logging.getLogger('test'))
        self.assertTrue(recovered['recovered_from_backup'])
        self.assertIn('r|01', recovered['episodes'])
        self.assertEqual(self.path.read_text(), 'broken')

    def test_both_corrupt_stops_instead_of_resetting_history(self):
        self.path.write_text('broken', encoding='utf-8')
        with self.assertRaises(state_store.StateError):
            state_store.read(self.path, logging.getLogger('test'))


class FetchAndPolicyTests(unittest.TestCase):
    def rule(self):
        return {'id': 'r', 'name': 'demo', 'keywords': [], 'group': ['Primary']}

    def item(self, fallback=False, ep='01', group='Primary'):
        return {'_rid': 'r', '_rule': self.rule(), 'episode': ep, 'btih': group * 4,
                'title': 'demo', 'author': group, '_fallback': fallback, '_tier': 0, '_rev': 1, 'pub_dt': None}

    def test_primary_blocks_fallback_even_when_already_sent(self):
        state = {'episodes': {'r|01|Primary': '0:1'}}
        fallback = self.item(True, group='Other')
        self.assertEqual(aw.suppress_fallbacks([fallback], state), [])

    def test_fallback_first_then_primary_and_multiple_primaries(self):
        state = {'episodes': {}}
        fallback = self.item(True, group='Other')
        self.assertEqual(aw.suppress_fallbacks([fallback], state), [fallback])
        a = self.item()
        b = self.item(False, group='Primary B')
        self.assertEqual(aw.suppress_fallbacks([fallback, a, b], state), [a, b])
        self.assertEqual(aw.suppress_fallbacks([fallback], state), [])

    def test_all_sources_failed_does_not_initialize(self):
        state = {'episodes': {}, 'initialized': False}
        cfg = {'settings': {'push_min_interval_seconds': 0}, 'rules': []}
        with patch.object(aw, 'collect', return_value=health.CollectionResult(failed_sources=3)), \
             patch.object(aw, 'save_state'):
            aw.run_cycle(cfg, state)
        self.assertFalse(state['initialized'])
        self.assertEqual(state['health']['status'], 'failed')

    def test_health_alerts_once_and_recovers_once(self):
        state = {}
        failed = health.CollectionResult(failed_sources=3)
        self.assertIsNone(health.update(state, failed, 2))
        self.assertEqual(health.update(state, failed, 2)[0], '抓取异常')
        self.assertIsNone(health.update(state, failed, 2))
        self.assertEqual(health.update(state, health.CollectionResult(successful_sources=1), 2)[0], '抓取恢复')
        self.assertIsNone(health.update(state, health.CollectionResult(successful_sources=1), 2))

    def test_valid_empty_and_partial_fetch_are_different_from_failure(self):
        cfg = {'settings': {'watch_global_feed': True}, 'rules': []}
        with patch.object(aw, 'pick_base', return_value=('https://example.invalid', '<rss><channel/></rss>')):
            result = aw.collect(cfg)
        self.assertTrue(result.available)
        self.assertEqual(result, [])
        cfg['rules'] = [dict(self.rule(), keywords=['demo'])]
        with patch.object(aw, 'pick_base', return_value=('https://example.invalid', '<rss><channel/></rss>')), \
             patch.object(aw, 'http_get', return_value=None), patch.object(aw.time, 'sleep'):
            result = aw.collect(cfg)
        self.assertTrue(result.available)
        self.assertGreater(result.failed_sources, 0)
        state = {}
        health.update(state, result, 3)
        self.assertEqual(state['health']['status'], 'partial')


class ParsingAndPresentationTests(unittest.TestCase):
    def test_notification_module_posts_without_missing_dependencies(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"code":0}'
        with patch.object(notifications.urllib.request, 'urlopen', return_value=response):
            result = notifications._post_json('https://example.invalid/hook', {'text': 'test'})
        self.assertEqual(json.loads(result)['code'], 0)

    def test_real_rss_fixture_episode_revision_season_and_groups(self):
        xml = (Path(__file__).parent / 'fixtures/releases.xml').read_text(encoding='utf-8')
        items = aw.parse_feed(xml)
        self.assertEqual([i['episode'] for i in items], ['01', '02', '01-12'])
        self.assertEqual(aw.get_revision(items[1]['title']), 2)
        self.assertTrue(aw.season_ok(items[0]['title'], 2))
        self.assertFalse(aw.season_ok(items[0]['title'], 1))
        groups = aw.extract_group_tokens(items[2]['title'], items[2]['author'])
        self.assertTrue(any('喵萌' in g for g in groups))
        self.assertFalse(aw.is_traditional_only(aw.extract_variant(items[0]['title'])))
        self.assertTrue(aw.is_traditional_only('繁日內嵌'))

    def test_collapse_does_not_remove_another_group(self):
        a = {'title': '[喵萌奶茶屋] 示例 [01-12]', 'author': '喵萌奶茶屋'}
        b = {'title': '[北宇治字幕组] 示例 [03]', 'author': '北宇治字幕组'}
        c = {'title': '[喵萌奶茶屋] 示例 [03]', 'author': '喵萌奶茶屋'}
        self.assertEqual(len(aw.collapse_overlaps([('01-12', a), ('03', b), ('03', c)])), 2)

    def test_unknown_episode_and_subtitles_are_not_guessed(self):
        item = {'title': 'demo', 'author': '', 'episode': '', '_rev': 1}
        _, body = aw.build_push_content(item, {'group': []}, '')
        self.assertIn('暂未识别', body)
        self.assertNotIn('这条磁力是整季', body)
        self.assertNotIn('生肉', body)

    def test_backfill_does_not_invent_total_episode_count(self):
        rule = {'id': 'r', 'name': 'demo', 'group': []}
        episodes = [('03', {'title': 'demo', 'author': '', 'magnet': 'magnet:test'})]
        with patch.object(aw, 'load_config', return_value={'settings': {}}), \
             patch.object(ar, 'queue_result', return_value=True) as queue:
            ar.notify_result(rule, episodes)
        body = queue.call_args.args[2]
        self.assertIn('目前检索到第 3 集', body)
        self.assertNotIn('全季为', body)

    def test_issue_management_commands_are_unambiguous(self):
        for text, expected in [('暂停 r', ('pause','r')), ('恢复 r', ('resume','r')), ('修改 r', ('edit','r'))]:
            self.assertEqual(ic.command(text), expected)


if __name__ == '__main__':
    unittest.main()
