import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import acgnx_watch as aw
import add_rule as ar
from watcher_core import fallback_wait as fw, state_store


class FallbackWaitTests(unittest.TestCase):
    def setUp(self):
        self.at = datetime(2026, 10, 18, 5, tzinfo=timezone.utc)
        self.rule = {'id': 'r', 'name': 'demo', 'group': ['Primary']}
        self.cfg = {'settings': {'push_min_interval_seconds': 0}, 'rules': [self.rule]}
        self.state = {'initialized': True, 'episodes': {}}

    def item(self, ep='03', fallback=True, rev=1):
        return {'_rid': 'r', '_rule': self.rule, 'episode': ep, 'btih': ep * 20,
                'title': '[Other] demo ' + ep, 'author': 'Other' if fallback else 'Primary',
                '_fallback': fallback, '_tier': 0, '_rev': rev, '_seen': f'0:{rev}',
                'pub_dt': self.at - timedelta(days=30), 'magnet': 'magnet:test'}

    def apply(self, items, days=0, state=None):
        state = self.state if state is None else state
        items = aw.suppress_fallbacks(items, state)
        return fw.apply_wait(items, state, [self.rule], self.at + timedelta(days=days))

    def test_waits_exactly_seven_days_from_observation_not_publication(self):
        self.assertEqual(self.apply([self.item()]), [])
        self.assertEqual(self.apply([], 7 - 1 / 86400), [])
        ready = self.apply([], 7)
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]['episode'], '03')
        self.assertEqual(ready[0]['pub_dt'], self.item()['pub_dt'])

    def test_independent_episode_clocks_do_not_restart_on_new_version(self):
        self.apply([self.item()])
        self.apply([self.item('04'), self.item(rev=2)], 5)
        ready = self.apply([], 7)
        self.assertEqual([(i['episode'], i['_rev']) for i in ready], [('03', 2)])
        self.assertEqual(len(self.apply([], 12)), 2)

    def test_same_episode_in_different_rules_has_independent_clock(self):
        second = dict(self.rule, id='second')
        self.apply([self.item()])
        item = dict(self.item(), _rid='second', _rule=second)
        fw.apply_wait([item], self.state, [self.rule, second], self.at + timedelta(days=4))
        ready = fw.apply_wait([], self.state, [self.rule, second], self.at + timedelta(days=7))
        self.assertEqual([i['_rid'] for i in ready], ['r'])

    def test_restart_retains_clock_and_candidate_without_credentials(self):
        self.rule['secret'] = 'do-not-persist'
        self.apply([self.item()])
        encoded = json.dumps(self.state)
        self.assertNotIn('do-not-persist', encoded)
        reloaded = state_store.migrate(json.loads(encoded))
        self.assertEqual(len(self.apply([], 7, reloaded)), 1)

    def test_primary_before_or_after_deadline_cancels_fallback(self):
        for days in [2, 8]:
            state = {'episodes': {}}
            self.apply([self.item()], state=state)
            result = self.apply([self.item(fallback=False)], days, state)
            self.assertEqual(len(result), 1)
            self.assertFalse(result[0]['_fallback'])
            self.assertEqual(state['fallback_waiting'], {})
            self.assertEqual(self.apply([self.item()], days + 10, state), [])

    def test_history_of_primary_also_cancels_wait(self):
        self.apply([self.item()])
        self.state['episodes']['r|03|Primary'] = '0:1'
        self.assertEqual(self.apply([self.item()], 8), [])
        self.assertEqual(self.state['fallback_waiting'], {})

    def test_one_best_fallback_per_episode_after_deadline(self):
        other = dict(self.item(), author='Other2', btih='b' * 40, _tier=1)
        self.apply([other, self.item()])
        ready = self.apply([other, self.item()], 7)
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]['author'], 'Other')

    def test_pause_delete_or_change_group_discards_obsolete_wait(self):
        for change in [{'enabled': False}, {'group': ['New']}, {'fallback_any_group': False}]:
            state = {'episodes': {}}
            self.apply([self.item()], state=state)
            fw.apply_wait([], state, [dict(self.rule, **change)], self.at)
            self.assertEqual(state['fallback_waiting'], {})
        self.apply([self.item()])
        fw.apply_wait([], self.state, [], self.at)
        self.assertEqual(self.state['fallback_waiting'], {})

    def test_primary_cancels_previously_failed_fallback_before_retry(self):
        self.apply([self.item()])
        key = aw.enqueue_notification(self.state, 'r', 'fallback', 'body')
        self.state['pending_notifications'][key]['delivery'] = {'state_key': 'r|03|Other', 'fallback': True}
        self.apply([self.item(fallback=False)], 8)
        self.assertEqual(self.state['pending_notifications'], {})

    def test_invalid_wait_timestamp_is_rejected(self):
        self.apply([self.item()])
        self.state['fallback_waiting']['r|03']['first_seen'] = 'yesterday'
        with self.assertRaises(state_store.StateError):
            state_store.migrate(self.state)

    def test_real_cycle_waits_without_advancing_baseline_then_uses_cached_resource(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp, patch.object(aw, 'STATE_PATH', str(Path(tmp) / 'state.json')):
            with patch.object(aw, 'collect', return_value=[self.item()]), patch.object(fw, 'now', return_value=self.at), \
                 patch.object(aw, 'send_notification') as send:
                self.assertEqual(aw.run_cycle(self.cfg, self.state), 0)
                send.assert_not_called()
            saved = aw.load_state()
            self.assertEqual(saved['episodes'], {})
            with patch.object(aw, 'collect', return_value=[]), patch.object(fw, 'now', return_value=self.at + timedelta(days=7)), \
                 patch.object(aw, 'send_notification', return_value=(True, '')) as send:
                self.assertEqual(aw.run_cycle(self.cfg, saved), 1)
                self.assertEqual(aw.run_cycle(self.cfg, aw.load_state()), 0)
            self.assertEqual(send.call_count, 1)
            self.assertIn('不代表指定组停更', send.call_args.args[1])

    def test_backfill_cannot_bypass_wait_and_dry_run_cannot_start_clock(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp, patch.object(aw, 'STATE_PATH', str(Path(tmp) / 'state.json')):
            aw.save_state(self.state)
            before = copy.deepcopy(self.state)
            with patch.object(aw, 'collect', return_value=[self.item()]), patch.object(fw, 'now', return_value=self.at):
                self.assertEqual(aw.run_cycle(self.cfg, self.state, dry_run=True), 0)
                self.assertEqual(self.state, before)
                with patch.object(aw, 'load_config', return_value=self.cfg):
                    self.assertEqual(ar.inspect_and_seed(self.rule, backfill_mode='all'), [])
            saved = aw.load_state()
            self.assertEqual(saved['episodes'], {})
            self.assertEqual(len(saved['fallback_waiting']), 1)
            self.assertTrue(all('magnet:test' not in m['body'] for m in saved['pending_notifications'].values()))

    def test_none_and_latest_do_not_turn_into_all_history_after_waiting(self):
        for mode, expected in [('none', []), ('latest', ['04'])]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp, \
                 patch.object(aw, 'STATE_PATH', str(Path(tmp) / 'state.json')):
                aw.save_state({'episodes': {}, 'initialized': True})
                with patch.object(aw, 'collect', return_value=[self.item('03'), self.item('04')]), \
                     patch.object(aw, 'load_config', return_value=self.cfg), patch.object(fw, 'now', return_value=self.at):
                    ar.inspect_and_seed(self.rule, backfill_mode=mode)
                state = aw.load_state()
                state['pending_notifications'] = {}  # The add confirmation contains no resources.
                titles = []
                with patch.object(aw, 'collect', return_value=[]), patch.object(fw, 'now', return_value=self.at + timedelta(days=7)), \
                     patch.object(aw, 'build_push_content', side_effect=lambda item, *args: (item['episode'], 'body')), \
                     patch.object(aw, 'send_notification', side_effect=lambda title, *args: (titles.append(title) is None, '')):
                    aw.run_cycle(self.cfg, state)
                self.assertEqual(titles, expected)


if __name__ == '__main__':
    unittest.main()
