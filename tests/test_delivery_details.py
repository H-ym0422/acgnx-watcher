import copy
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

import acgnx_watch as aw
import add_rule as ar
from watcher_core.health import CollectionResult


class DeliveryDetailsTests(unittest.TestCase):
    rule = {'id': 'anbu', 'name': '某暗部的少女共栖', 'group': ['NEST 发布机']}
    old_rule = dict(rule, group=['喵萌奶茶屋'])
    now = datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc)

    def release(self, episode='06', group='NEST', fallback=False, tier=1, rev=2):
        return {'title': f'[{group}] 某暗部的少女共栖 - {episode} [简繁日内封]',
                'author': 'NEST 发布机' if group == 'NEST' else group,
                'episode': episode, 'btih': 'a' * 40, 'magnet': 'magnet:test',
                '_rid': 'anbu', '_rule': self.rule, 'pub_dt': None,
                '_fallback': fallback, '_tier': tier, '_rev': rev,
                '_variant': '简繁日内封', '_seen': f'{tier}:{rev}'}

    def run_cycle(self, state, matched, sent=True, dry_run=False, rules=None):
        cfg = {'settings': {'push_min_interval_seconds': 0}, 'rules': rules or [self.rule]}
        with patch.object(aw, 'collect', return_value=matched), patch.object(aw, 'save_state'), \
             patch.object(aw.fallback_wait, 'now', return_value=self.now), \
             patch.object(aw, 'send_notification', return_value=(sent, '')) as send:
            aw.run_cycle(cfg, state, dry_run=dry_run)
        return send

    def test_switch_resets_old_group_before_outage_retries_and_restarts_wait(self):
        pending = {'rule_id': 'anbu', 'title': '旧推送', 'body': '',
                   'source': aw.fallback_wait.freeze(self.release('05', '喵萌奶茶屋')),
                   'rule_signature': aw.fallback_wait.signature(self.old_rule)}
        fallback = self.release('05', '其他字幕组', fallback=True)
        old_wait = {'first_seen': (self.now - timedelta(days=8)).isoformat(), 'ready': True,
                    'candidate': aw.fallback_wait.freeze(fallback),
                    'signature': aw.fallback_wait.signature(self.old_rule)}
        other_rule = {'id': 'other', 'name': '其他番', 'group': ['喵萌奶茶屋']}
        state = {'initialized': True, 'rule_group_selections': {'anbu': ['喵萌奶茶屋'], 'other': ['喵萌奶茶屋']},
                 'episodes': {'anbu|05|喵萌奶茶屋': '1:2'},
                 'deliveries': {'anbu|05|喵萌奶茶屋': {'time': '2026-11-14T10:00:00+08:00'}},
                 'primary_episodes': {'anbu|05': True, 'other|01': True},
                 'fallback_waiting': {'anbu|05': old_wait},
                 'pending_notifications': {'old': pending}}
        before = copy.deepcopy(state)
        with patch('builtins.print'):
            self.run_cycle(state, [fallback], dry_run=True, rules=[self.rule, other_rule]).assert_not_called()
        self.assertEqual(state, before)
        self.run_cycle(state, CollectionResult([], 0), rules=[self.rule, other_rule]).assert_not_called()
        self.assertEqual(state['pending_notifications'], {})
        self.assertEqual(state['primary_episodes'], {'other|01': True})
        self.assertEqual(state['fallback_waiting'], {})
        self.assertEqual(state['episodes'], before['episodes'])
        self.assertEqual(state['deliveries'], before['deliveries'])

        self.run_cycle(state, [fallback], rules=[self.rule, other_rule]).assert_not_called()
        self.assertEqual(state['fallback_waiting']['anbu|05']['first_seen'], self.now.isoformat())
        self.assertFalse(state['fallback_waiting']['anbu|05']['ready'])

    def test_legacy_primary_marks_require_evidence_for_current_group(self):
        state = {'initialized': True, 'primary_episodes': {'anbu|05': True, 'anbu|06': True},
                 'episodes': {'anbu|05|喵萌奶茶屋': '1:1', 'anbu|06|NEST 发布机': '1:2'}}
        fallback = self.release('05', '其他字幕组', fallback=True)
        self.run_cycle(state, [fallback]).assert_not_called()
        self.assertEqual(state['primary_episodes'], {'anbu|06': True})
        self.assertIn('anbu|05', state['fallback_waiting'])

    def test_notification_label_distinguishes_two_kinds_of_update(self):
        cases = [
            ('2:2', '【资源更新】（更优字幕）'),
            ('1:1', '【资源更新】（修正版）'),
            ('2:1', '【资源更新】（修正版·更优字幕）'),
            (None, '【新番速递】'),
        ]
        for previous, prefix in cases:
            with self.subTest(previous=previous):
                state = {'initialized': True, 'episodes': {'anbu|06|NEST 发布机': previous} if previous else {}}
                send = self.run_cycle(state, [self.release()])
                send.assert_called_once()
                title, body, _ = send.call_args.args
                self.assertEqual(title, prefix + '某暗部的少女共栖 第06集（NEST）')
                self.assertIn('版本：v2\n', body)
                self.assertNotIn('修正版', body)

    def test_watchlist_uses_successful_receipt_and_actual_publisher(self):
        state = {'initialized': True, 'episodes': {},
                 'deliveries': {'anbu|05|喵萌奶茶屋': {'group': '喵萌奶茶屋', 'time': '2026-11-14T10:00:00+08:00',
                                                     'episode_known': True, 'fallback': False}}}
        self.run_cycle(state, [self.release()], sent=False).assert_called_once()
        with patch.object(aw, 'load_config', return_value={'rules': [self.rule]}), \
             patch.object(aw, 'load_state', return_value=state):
            self.assertIn('第 05 集 · 喵萌奶茶屋 · 字幕形式未记录', ar.render_watchlist())
            self.assertNotIn('第 06 集 · NEST', ar.render_watchlist())
            self.run_cycle(state, []).assert_called_once()
            self.assertIn('第 06 集 · NEST · 简繁日内封', ar.render_watchlist())
