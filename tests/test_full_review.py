import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import acgnx_watch as aw
import add_rule as ar
import issue_commands as ic
from watcher_core import fallback_wait as fw, health, release_identity as ri, remote_checkpoint, network
from watcher_core.send_budget import SendBudget


class ReleaseTests(unittest.TestCase):
    def rule(self):
        return {'id': 'r', 'name': 'demo', 'group': ['Primary'], 'include_any': ['demo'], 'season': 3}

    def item(self, ep='03', title='[Other] demo S3 [03(51)]', fallback=True):
        return {'_rid': 'r', '_rule': self.rule(), 'episode': ep, 'title': title, 'author': 'Other' if fallback else 'Primary',
                'btih': 'a' * 40, '_tier': 0, '_rev': 1, '_seen': '0:1', '_fallback': fallback, 'pub_dt': None}

    def test_comic_actual_live_title_and_category_are_rejected(self):
        title = '生徒会にも穴はある！ 第01-05巻 [Seitokai nimo Ana wa Aru! vol 01-05]'
        for item in [self.item(title=title), dict(self.item(), category='漫画'), self.item(title='demo 小説 第1巻'),
                     self.item(title='demo [manga] [01]')]:
            self.assertFalse(ri.is_animation(item))
            self.assertFalse(aw.fallback_match(item, self.rule()))
        self.assertTrue(ri.is_animation(self.item(title='demo S3 Vol.1 [BDRip 1080p MKV]')))
        self.assertTrue(ri.is_animation(self.item(title='漫画女孩 / Comic Girls [01][1080p AVC MKV]')))

    def test_existing_comic_wait_cache_is_cleaned_without_resetting_other_clocks(self):
        rule = self.rule()
        at = datetime(2026, 10, 18, tzinfo=timezone.utc)
        state = {'episodes': {'old|01': '0:1'}, 'fallback_waiting': {}}
        for ep, title in [('03', 'demo S3 [03]'), ('bad', 'demo 第01-05巻')]:
            item = self.item(ep=ep, title=title)
            state['fallback_waiting']['r|' + ep] = {'first_seen': at.isoformat(), 'signature': fw.signature(rule), 'candidate': fw.freeze(item)}
        fw.apply_wait([], state, [rule], at + timedelta(days=1))
        self.assertEqual(set(state['fallback_waiting']), {'r|03'})
        self.assertEqual(state['fallback_waiting']['r|03']['first_seen'], at.isoformat())
        self.assertIn('old|01', state['episodes'])

    def test_dual_number_evidence_normalizes_and_preserves_history(self):
        rule = self.rule()
        local = self.item()
        cumulative = self.item(ep='51', title='demo 第三季 S01E51')
        state = {'episodes': {'r|51|Other': '0:2'}, 'primary_episodes': {'r|51': True}}
        ri.reconcile_state(state, [rule], [local, cumulative])
        self.assertEqual(local['episode'], cumulative['episode'])
        self.assertEqual(cumulative['episode'], '03')
        self.assertEqual(state['episodes']['r|03|Other'], '0:2')
        self.assertIn('r|51|Other', state['episodes'])
        self.assertTrue(ri.primary_covers(state, 'r', '03'))

    def test_unrelated_or_conflicting_numbering_is_not_guessed(self):
        rule = self.rule()
        self.assertEqual(ri.evidence_map([self.item(title='other S3 [03(51)]')], rule), {})
        pairs = ri.evidence_map([self.item(), self.item(title='demo S3 [04(51)]')], rule)
        self.assertEqual(pairs, {})

    def test_unmarked_season_can_use_observed_cumulative_evidence(self):
        rule = self.rule()
        mapping = ri.evidence_map([self.item()], rule)
        unmarked = self.item(ep='51', title='[Primary] demo [51]', fallback=False)
        unmarked['_season_evidence'] = ri.season_evidence(unmarked, rule, mapping)
        self.assertEqual(unmarked['_season_evidence'], 3)
        self.assertTrue(aw.match_rule(unmarked, rule))
        self.assertIsNone(ri.season_evidence(self.item(title='demo [03]'), rule, mapping))
        self.assertEqual(ri.canonical_episode(unmarked, rule, mapping), '03')

    def test_explicit_cumulative_start_is_configurable(self):
        rule = dict(self.rule(), season_start_episode=49)
        item = self.item(ep='51', title='[Primary] demo [51]')
        self.assertEqual(ri.season_evidence(item, rule, {}), 3)
        self.assertEqual(ri.canonical_episode(item, rule, {}), '03')
        self.assertEqual(ri.canonical_episode(self.item(ep='01', title='demo S3 [01]'), rule, {}), '01')
        self.assertEqual(ri.effective_floor(dict(rule, from_episode=49), {}), 1)
        self.assertEqual(ri.effective_floor(dict(self.rule(), from_episode=49), {'49': '01'}), 1)

    def test_primary_batch_or_adjacent_batches_cover_fallback_single(self):
        rule = self.rule()
        for primary in [{'r|01-12': True}, {'r|01-02': True, 'r|03-05': True}]:
            state = {'primary_episodes': primary, 'episodes': {}}
            result = aw.suppress_fallbacks([self.item()], state)
            self.assertEqual(result, [])
        self.assertFalse(ri.covers(['01-02'], '03'))

    def test_historical_primary_batch_cancels_waiting_and_queued_single(self):
        rule = self.rule()
        at = datetime(2026, 10, 18, tzinfo=timezone.utc)
        state = {'episodes': {}}
        fw.apply_wait([self.item()], state, [rule], at)
        key = aw.enqueue_notification(state, 'r', 'fallback', 'body')
        state['pending_notifications'][key]['delivery'] = {'state_key': 'r|03|Other', 'fallback': True}
        state['episodes']['r|01-12|Primary'] = '0:1'
        items = aw.suppress_fallbacks([self.item()], state)
        self.assertEqual(fw.apply_wait(items, state, [rule], at + timedelta(days=8)), [])
        self.assertEqual(state['fallback_waiting'], {})
        self.assertEqual(state['pending_notifications'], {})

    def test_ambiguous_primary_blocks_fallback_until_confirmation(self):
        at = datetime(2026, 10, 18, tzinfo=timezone.utc)
        state = {'episodes': {}, 'season_ambiguous': {'r': ['03']}}
        fw.apply_wait([self.item()], state, [self.rule()], at)
        self.assertEqual(fw.apply_wait([], state, [self.rule()], at + timedelta(days=8)), [])
        state['season_ambiguous'] = {}
        self.assertEqual(len(fw.apply_wait([], state, [self.rule()], at + timedelta(days=8))), 1)


class QueueAndBudgetTests(unittest.TestCase):
    def setUp(self):
        self.rule = {'id': 'r', 'name': 'demo', 'group': ['Primary']}
        self.settings = {'push_min_interval_seconds': 0}

    def queued(self, episodes=('03', '52'), fallback=False):
        state = {'episodes': {}}
        for ep in episodes:
            key = aw.enqueue_notification(state, 'r', ep, 'body')
            state['pending_notifications'][key]['delivery'] = {'state_key': f'r|{ep}|Primary', 'fallback': fallback}
        return state

    def test_floor_change_cancels_old_episode_but_keeps_valid_new_episode(self):
        state = self.queued()
        with patch.object(aw, 'save_state'), patch.object(aw, 'send_notification', return_value=(True, '')) as send:
            self.assertEqual(aw.deliver_pending(state, self.settings, rules=[dict(self.rule, from_episode=49)]), 1)
        self.assertEqual(send.call_args.args[0], '52')

    def test_disabling_fallback_cancels_already_queued_fallback(self):
        state = self.queued(fallback=True)
        with patch.object(aw, 'save_state'), patch.object(aw, 'send_notification') as send:
            self.assertEqual(aw.deliver_pending(state, self.settings, rules=[dict(self.rule, fallback_any_group=False)]), 0)
        send.assert_not_called()
        self.assertEqual(state['pending_notifications'], {})

    def test_unresolved_primary_defers_instead_of_deleting_fallback_retry(self):
        state = self.queued(('03',), fallback=True)
        state['season_ambiguous'] = {'r': ['01-12']}
        with patch.object(aw, 'save_state'), patch.object(aw, 'send_notification', return_value=(True, '')) as send:
            self.assertEqual(aw.deliver_pending(state, self.settings, rules=[self.rule]), 0)
            self.assertEqual(len(state['pending_notifications']), 1)
            state['season_ambiguous'] = {}
            self.assertEqual(aw.deliver_pending(state, self.settings, rules=[self.rule]), 1)
        self.assertEqual(send.call_count, 1)

    def test_actual_cli_floor_edit_keeps_valid_newer_queued_episode(self):
        import sys
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            config = Path(tmp) / 'config.json'
            state_path = Path(tmp) / 'state.json'
            config.write_text(json.dumps({'settings': {}, 'rules': [self.rule]}), encoding='utf8')
            with patch.object(aw, 'CONFIG_PATH', str(config)), patch.object(aw, 'STATE_PATH', str(state_path)):
                aw.save_state(self.queued())
                with patch.object(sys, 'argv', ['add_rule.py', '--edit', 'r', '--from-episode', '49', '--no-push']), \
                     patch.object(ar, 'write_watchlist'), patch.object(ar, 'maybe_push'):
                    ar.main()
                self.assertEqual([m['title'] for m in aw.load_state()['pending_notifications'].values()], ['52'])

    def test_one_shared_budget_leaves_large_batch_for_next_cycle(self):
        state = self.queued(tuple(f'{n:02d}' for n in range(1, 41)))
        budget = SendBudget(max_messages=2)
        with patch.object(aw, 'save_state'), patch.object(aw, 'send_notification', return_value=(True, '')) as send:
            aw.deliver_pending(state, self.settings, rules=[self.rule], budget=budget)
            aw.deliver_pending(state, self.settings, rules=[self.rule], budget=budget)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(len(state['pending_notifications']), 38)

    def test_budget_reserves_time_for_checkpoints_and_network(self):
        with patch('watcher_core.send_budget.time.monotonic', return_value=0):
            budget = SendBudget(seconds=240)
        with patch('watcher_core.send_budget.time.monotonic', return_value=131):
            self.assertFalse(budget.reserve(13))

    def test_push_gap_is_measured_from_actual_send_after_slow_checkpoint(self):
        clock = [0]
        sleeps = []
        def sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds
        with patch('watcher_core.send_budget.time.monotonic', side_effect=lambda: clock[0]), \
             patch('watcher_core.send_budget.time.sleep', side_effect=sleep):
            budget = SendBudget()
            self.assertTrue(budget.reserve(13))
            clock[0] = 45
            budget.mark_attempt()
            clock[0] = 46
            self.assertTrue(budget.reserve(13))
        self.assertEqual(sleeps, [12])

    def test_checkpoint_failure_prevents_external_notification(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp, patch.object(aw, 'STATE_PATH', str(Path(tmp) / 'state.json')):
            state = self.queued(('03',))
            with patch.object(remote_checkpoint, 'publish', side_effect=RuntimeError('remote failure')), \
                 patch.object(aw, 'send_notification') as send:
                with self.assertRaises(RuntimeError):
                    aw.deliver_pending(state, self.settings, rules=[self.rule])
            send.assert_not_called()
            self.assertEqual(len(aw.load_state()['pending_notifications']), 1)

    def test_checkpoint_order_is_outbox_send_receipt(self):
        state = self.queued(('03',))
        sequence = []
        def save(value):
            sequence.append('outbox' if value['pending_notifications'] else 'receipt')
        def send(*args):
            sequence.append('send')
            return True, ''
        with patch.object(aw, 'save_state', side_effect=save), patch.object(aw, 'send_notification', side_effect=send):
            aw.deliver_pending(state, self.settings, rules=[self.rule])
        self.assertEqual(sequence, ['outbox', 'send', 'receipt'])


class MonitoringTests(unittest.TestCase):
    def test_real_collection_tracks_failed_rule_independently(self):
        cfg = {'settings': {}, 'rules': [
            {'id': 'a', 'keywords': ['demoA'], 'include_any': ['demoA']},
            {'id': 'b', 'keywords': ['demoB'], 'include_any': ['demoB']}]}
        valid = '<rss><channel/></rss>'
        def get(url, **kwargs):
            return valid if 'demoB' in url else None
        with patch.object(aw, 'pick_base', return_value=('https://example.invalid', valid)), \
             patch.object(aw, 'http_get', side_effect=get), patch.object(aw.time, 'sleep'):
            result = aw.collect(cfg)
        self.assertEqual(result.rule_sources['a']['successful_sources'], 0)
        self.assertEqual(result.rule_sources['a']['failed_sources'], 1)
        self.assertEqual(result.rule_sources['b']['successful_sources'], 1)
        self.assertTrue(result.available)

    def test_real_collection_uses_persisted_mapping_for_unmarked_primary(self):
        rule = {'id': 'r', 'name': 'demo', 'include_any': ['demo'], 'group': ['Primary'], 'season': 3}
        raw = {'title': '[Primary] demo [51]', 'author': 'Primary', 'episode': '51', 'btih': 'a' * 40, 'pub_dt': None}
        cfg = {'settings': {}, 'rules': [rule], '_episode_maps': {'r': {'51': '03'}}}
        with patch.object(aw, 'pick_base', return_value=('https://example.invalid', '<rss><channel/></rss>')), \
             patch.object(aw, 'parse_feed', return_value=[raw]):
            result = aw.collect(cfg)
        self.assertEqual([(x['episode'], x['_fallback']) for x in result], [('03', False)])
        self.assertEqual(result.ambiguous, {})
        state = {'episodes': {}, 'initialized': True, 'episode_maps': {'r': {'51': '03'}}}
        with patch.object(aw, 'collect', return_value=result), patch.object(aw, 'save_state'), \
             patch.object(aw, 'send_notification', return_value=(True, '')) as send:
            self.assertEqual(aw.run_cycle(cfg, state), 1)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(state['pending_notifications'], {})

    def test_real_collection_exposes_unresolved_primary_season(self):
        rule = {'id': 'r', 'name': 'demo', 'include_any': ['demo'], 'group': ['Primary'], 'season': 3}
        raw = {'title': '[Primary] demo [03]', 'author': 'Primary', 'episode': '03', 'btih': 'a' * 40, 'pub_dt': None}
        with patch.object(aw, 'pick_base', return_value=('https://example.invalid', '<rss><channel/></rss>')), \
             patch.object(aw, 'parse_feed', return_value=[raw]):
            result = aw.collect({'settings': {}, 'rules': [rule]})
        self.assertEqual(list(result), [])
        self.assertEqual(result.ambiguous, {'r': ['03']})

    def test_one_broken_rule_alerts_even_when_global_and_other_rule_succeed(self):
        rules = [{'id': 'a', 'name': 'A'}, {'id': 'b', 'name': 'B'}]
        result = health.CollectionResult(successful_sources=1, failed_sources=1, rule_sources={
            'a': {'successful_sources': 0, 'failed_sources': 1}, 'b': {'successful_sources': 1, 'failed_sources': 0}})
        state = {}
        self.assertEqual(health.update_rules(state, result, rules, 2), [])
        self.assertEqual(health.update_rules(state, result, rules, 2)[0][0], 'a')
        self.assertEqual(health.update_rules(state, result, rules, 2), [])
        recovered = health.CollectionResult(successful_sources=2, rule_sources={
            'a': {'successful_sources': 1, 'failed_sources': 0}, 'b': {'successful_sources': 1, 'failed_sources': 0}})
        self.assertEqual(health.update_rules(state, recovered, rules, 2)[0][1], '单部番搜索恢复')
        self.assertEqual(health.update_rules(state, recovered, rules, 2), [])

    def test_ambiguous_quarter_is_visible_in_rule_health(self):
        result = health.CollectionResult(successful_sources=1, ambiguous={'r': ['03']})
        state = {}
        health.update_rules(state, result, [{'id': 'r'}], 3)
        self.assertEqual(state['rule_health']['r']['status'], 'needs_confirmation')

    def test_issue_can_configure_season_evidence_and_opt_in(self):
        self.assertEqual(ic.season_controls('### 季内起始总集号\n49\n### 未标季度\n允许'),
                         ['--season-start-episode', '49', '--allow-unmarked-season'])
        with self.assertRaises(ValueError):
            ic.season_controls('### 季内起始总集号\n-1')

    def test_request_deadline_stops_network_attempts(self):
        with patch.object(network, '_REQUEST_DEADLINE', 1), patch.object(network.time, 'monotonic', return_value=2), \
             patch.object(network.urllib.request, 'build_opener') as opener:
            self.assertIsNone(network.http_get('https://example.invalid'))
        opener.assert_not_called()

    def test_remote_checkpoints_are_opt_in_and_never_run_from_feature_branch(self):
        with patch.dict('os.environ', {'ACGNX_REMOTE_CHECKPOINT': '0'}), patch.object(remote_checkpoint, 'git') as git:
            remote_checkpoint.publish()
            git.assert_not_called()
        with patch.dict('os.environ', {'ACGNX_REMOTE_CHECKPOINT': '1', 'GITHUB_ACTIONS': 'true'}), \
             patch.object(remote_checkpoint, 'git', return_value='feature'):
            with self.assertRaises(RuntimeError):
                remote_checkpoint.publish()

    def test_remote_checkpoint_publishes_committed_queue_before_sending(self):
        commands = []
        def git(args, deadline=None):
            commands.append(args)
            if args == ['branch', '--show-current']:
                return 'main'
            if args[:2] == ['status', '--porcelain']:
                return ' M state.json'
            if args == ['rev-parse', 'HEAD']:
                return 'new'
            if args == ['rev-parse', 'origin/main']:
                return 'old'
            if args == ['rev-list', '--count', 'HEAD..origin/main']:
                return '0'
            return ''
        with patch.dict('os.environ', {'ACGNX_REMOTE_CHECKPOINT': '1', 'GITHUB_ACTIONS': 'true'}), \
             patch.object(remote_checkpoint, 'git', side_effect=git):
            remote_checkpoint.publish()
        self.assertTrue(any(c[0] == 'commit' for c in commands))
        self.assertEqual(commands[-1], ['push', 'origin', 'HEAD:main'])
        self.assertFalse(any(c[0] == 'pull' for c in commands))


if __name__ == '__main__':
    unittest.main()
