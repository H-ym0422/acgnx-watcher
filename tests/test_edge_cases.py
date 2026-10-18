import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import acgnx_watch as aw
import add_rule as ar
from watcher_core import rules
from watcher_core.configuration import ConfigError, validate_config
from watcher_core.matching import match_rule, fallback_match


class RuleEdges(unittest.TestCase):
    def test_edit_updates_generated_group_suffix_without_changing_anime_title(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            path = Path(tmp) / 'config.json'
            rule = {'id': 'r', 'name': '动画（特别篇）（A/B等）', 'group': ['A', 'B', 'C']}
            path.write_text(json.dumps({'rules': [rule]}), encoding='utf8')
            for groups, suffix in [(['D'], 'D'), ([], '任意字幕组')]:
                rules.edit(path, 'r', {'group': groups})
                current = json.loads(path.read_text(encoding='utf8'))['rules'][0]
                self.assertEqual(current['name'], f'动画（特别篇）（{suffix}）')
                self.assertEqual(current['id'], 'r')

    def test_custom_name_is_preserved(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            path = Path(tmp) / 'config.json'
            path.write_text(json.dumps({'rules': [{'id': 'r', 'name': 'custom', 'group': ['A']}]}), encoding='utf8')
            rules.edit(path, 'r', {'group': ['B']})
            self.assertEqual(json.loads(path.read_text())['rules'][0]['name'], 'custom')

    def test_season_requires_evidence_in_primary_and_fallback_matching(self):
        rule = {'name': 'demo', 'include_any': ['demo'], 'season': 2, 'group': ['A']}
        item = {'title': '[A] demo - 01', 'author': 'A'}
        self.assertFalse(match_rule(item, rule))
        self.assertFalse(fallback_match(item, rule))
        self.assertTrue(match_rule(dict(item, title='[A] demo S02 - 01'), rule))
        self.assertTrue(match_rule(item, dict(rule, allow_unmarked_season=True)))
        self.assertFalse(match_rule(dict(item, title='[A] demo S01 - 01'), dict(rule, allow_unmarked_season=True)))
        self.assertTrue(match_rule(item, dict(rule, season=0)))

    def test_season_opt_in_must_be_boolean(self):
        with self.assertRaisesRegex(ConfigError, 'allow_unmarked_season'):
            validate_config({'rules': [{'id': 'r', 'allow_unmarked_season': 'false'}]})


class OutboxEdges(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state.json'
        p = patch.object(aw, 'STATE_PATH', str(self.path))
        p.start()
        self.addCleanup(p.stop)
        self.rule = {'id': 'r', 'name': 'demo', 'group': []}
        self.cfg = {'settings': {'push_min_interval_seconds': 0}, 'rules': [self.rule]}
        self.state = {'initialized': True, 'episodes': {}}

    def item(self, ep):
        return {'_rid': 'r', '_rule': self.rule, 'episode': ep, 'btih': ep * 20,
                'title': 'demo ' + ep, '_tier': 0, '_rev': 1, '_seen': '0:1', 'pub_dt': None}

    def test_manual_pause_and_delete_cancel_pending_but_health_still_sends(self):
        aw.enqueue_notification(self.state, 'r', 'paused', 'body')
        aw.enqueue_notification(self.state, 'deleted', 'removed', 'body')
        aw.enqueue_notification(self.state, '__health__', 'health', 'body')
        self.rule['enabled'] = False
        with patch.object(aw, 'collect', return_value=[]), patch.object(aw, 'send_notification', return_value=(True, '')) as send:
            self.assertEqual(aw.run_cycle(self.cfg, self.state), 1)
        self.assertEqual([c.args[0] for c in send.call_args_list], ['health'])
        self.assertEqual(aw.load_state()['pending_notifications'], {})

    def test_batch_crash_keeps_all_unsent_messages_and_checkpoints_receipts(self):
        items = [self.item('01'), self.item('02')]
        with patch.object(aw, 'collect', return_value=items), patch.object(aw, 'group_key', return_value=''), \
             patch.object(aw, 'build_push_content', side_effect=lambda i, *args: (i['title'], 'body')), \
             patch.object(aw, 'send_notification', side_effect=[(True, ''), SystemExit('interrupted')]):
            with self.assertRaises(SystemExit):
                aw.run_cycle(self.cfg, self.state)
        saved = aw.load_state()
        self.assertEqual(set(saved['episodes']), {'r|01', 'r|02'})
        self.assertEqual([m['title'] for m in saved['pending_notifications'].values()], ['demo 02'])
        with patch.object(aw, 'collect', return_value=[]), patch.object(aw, 'send_notification', return_value=(True, '')) as send:
            self.assertEqual(aw.run_cycle(self.cfg, saved), 1)
            self.assertEqual(aw.run_cycle(self.cfg, aw.load_state()), 0)
        self.assertEqual(send.call_args.args[0], 'demo 02')
        self.assertEqual(send.call_count, 1)
        self.assertEqual(set(aw.load_state()['deliveries']), {'r|01', 'r|02'})

    def test_send_failure_retries_after_resource_leaves_rss(self):
        with patch.object(aw, 'collect', return_value=[self.item('01')]), patch.object(aw, 'group_key', return_value=''), \
             patch.object(aw, 'build_push_content', return_value=('title', 'body')), \
             patch.object(aw, 'send_notification', return_value=(False, '')):
            self.assertEqual(aw.run_cycle(self.cfg, self.state), 0)
        saved = aw.load_state()
        self.assertEqual(len(saved['pending_notifications']), 1)
        with patch.object(aw, 'collect', return_value=[]), patch.object(aw, 'send_notification', return_value=(True, '')):
            self.assertEqual(aw.run_cycle(self.cfg, saved), 1)
        self.assertEqual(aw.load_state()['pending_notifications'], {})

    def test_state_write_failure_prevents_external_send(self):
        aw.enqueue_notification(self.state, 'r', 'title', 'body')
        with patch.object(aw, 'save_state', side_effect=OSError('disk full')), patch.object(aw, 'send_notification') as send:
            with self.assertRaises(OSError):
                aw.deliver_pending(self.state, self.cfg['settings'], rules=[self.rule])
        send.assert_not_called()

    def test_backfill_outbox_and_baseline_are_saved_together_before_notification(self):
        aw.save_state(self.state)
        item = dict(self.item('01'), magnet='magnet:test', author='')
        with patch.object(aw, 'load_config', return_value=self.cfg), patch.object(aw, 'collect', return_value=[item]), \
             patch.object(aw, 'group_key', return_value=''), patch.object(aw, 'send_notification') as send:
            ar.inspect_and_seed(self.rule, backfill_mode='all')
        # Simulate termination before notify_result is ever reached.
        saved = aw.load_state()
        self.assertIn('r|01', saved['episodes'])
        self.assertEqual(len(saved['pending_notifications']), 1)
        send.assert_not_called()
        with patch.object(aw, 'collect', return_value=[]), patch.object(aw, 'send_notification', return_value=(True, '')):
            self.assertEqual(aw.run_cycle(self.cfg, saved), 1)


if __name__ == '__main__':
    unittest.main()
