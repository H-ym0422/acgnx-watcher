import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import acgnx_watch as aw
import add_rule as ar
import issue_commands as ic


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.tmp.cleanup)
        self.state_path = Path(self.tmp.name) / 'state.json'
        self.path_patch = patch.object(aw, 'STATE_PATH', str(self.state_path))
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.cfg = {'settings': {'push_min_interval_seconds': 0}, 'rules': []}
        aw.save_state({'initialized': True, 'episodes': {'old|01': '0:1'}})
        self.rule = {'id': 'new-rule', 'name': 'demo', 'group': []}
        self.cfg['rules'] = [self.rule]
        self.item = {'btih': 'a' * 40, 'episode': '01', 'title': 'demo', 'author': '', 'size': '',
                     'pub_dt': None, 'magnet': 'magnet:test', '_seen': '0:1',
                     '_tier': 0, '_rev': 1, '_fallback': False, '_rule': self.rule,
                     '_rid': self.rule['id']}

    def test_failed_backfill_survives_reload_then_retries_once(self):
        with patch.object(aw, 'collect', return_value=[self.item]), \
             patch.object(aw, 'group_key', return_value=''), \
             patch.object(aw, 'load_config', return_value=self.cfg):
            episodes = ar.inspect_and_seed(self.rule)
        self.assertIn('new-rule|01', aw.load_state()['episodes'])
        with patch.object(aw, 'load_config', return_value=self.cfg), \
             patch.object(aw, 'send_notification', return_value=(False, '')):
            self.assertFalse(ar.notify_result(self.rule, episodes))
        state = aw.load_state()
        self.assertEqual(len(state['pending_notifications']), 1)
        self.assertEqual(state['episodes']['old|01'], '0:1')
        with patch.object(aw, 'collect', return_value=[]), \
             patch.object(aw, 'send_notification', return_value=(True, '')) as send:
            self.assertEqual(aw.run_cycle(self.cfg, state), 1)
            self.assertEqual(aw.run_cycle(self.cfg, aw.load_state()), 0)
            self.assertEqual(send.call_count, 1)
        self.assertEqual(aw.load_state()['pending_notifications'], {})

    def test_backfill_none_sends_confirmation_without_resource(self):
        with patch.object(aw, 'load_config', return_value=self.cfg), \
             patch.object(aw, 'send_notification', return_value=(True, '')) as send:
            self.assertTrue(ar.notify_result(self.rule, [('01', self.item)], mode='none'))
        self.assertNotIn('magnet:test', send.call_args.args[1])
        self.assertEqual(aw.load_state()['pending_notifications'], {})

    def test_successful_notification_does_not_remain_pending(self):
        with patch.object(aw, 'load_config', return_value=self.cfg), \
             patch.object(aw, 'send_notification', return_value=(True, '')):
            self.assertTrue(ar.notify_result(self.rule, [('01', self.item)], mode='latest'))
        self.assertEqual(aw.load_state()['pending_notifications'], {})

    def test_deleted_rule_cancels_only_its_pending_notifications(self):
        state = aw.load_state()
        aw.enqueue_notification(state, 'deleted', 'old', 'body')
        keep = aw.enqueue_notification(state, 'retained', 'keep', 'body')
        aw.save_state(state)
        ar.purge_baseline(['deleted'])
        self.assertEqual(list(aw.load_state()['pending_notifications']), [keep])

    def test_collect_and_list_leave_state_bytes_unchanged(self):
        before = self.state_path.read_bytes()
        item = dict(self.item, episode='')
        cfg = {'settings': {'watch_global_feed': True}, 'rules': [self.rule]}
        with patch.object(aw, 'pick_base', return_value=('https://example.invalid', 'feed')), \
             patch.object(aw, 'parse_feed', return_value=[item]), \
             patch.object(aw, 'match_rule', return_value=True), \
             patch.object(aw, 'get_variant_tier', return_value=0), \
             patch.object(aw, 'setup_logging'), \
             patch.object(aw, 'load_config', return_value=cfg), \
             patch.object(sys, 'argv', ['acgnx_watch.py', '--list']):
            result = aw.collect(cfg)
            self.assertEqual(len(result), 1)
            aw.main()
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_dry_run_never_sends_or_saves_pending_or_diagnostics(self):
        state = aw.load_state()
        aw.enqueue_notification(state, 'r', 'title', 'body')
        before = copy.deepcopy(state)
        with patch.object(aw, 'collect', return_value=[]), \
             patch.object(aw, 'send_notification') as send, \
             patch.object(aw, 'save_state') as save:
            aw.run_cycle(self.cfg, state, dry_run=True)
        send.assert_not_called()
        save.assert_not_called()
        self.assertEqual(state, before)

    def test_diagnostics_are_merged_into_existing_state(self):
        item = dict(self.item, episode='')
        state = aw.load_state()
        with patch.object(aw, 'collect', return_value=[item]), \
             patch.object(aw, 'group_key', return_value=''), \
             patch.object(aw, 'build_push_content', return_value=('title', 'body')), \
             patch.object(aw, 'send_notification', return_value=(True, '')):
            aw.run_cycle(self.cfg, state)
        saved = aw.load_state()
        self.assertIn(item['btih'], saved['unparsed'])
        self.assertIn('old|01', saved['episodes'])
        self.assertIn('new-rule|' + item['btih'], saved['episodes'])


class IssueTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {'version': 1, 'term': 'demo', 'season': 2, 'groups': ['Group A', 'Group B']}
        self.comments = [{'user': {'login': 'github-actions[bot]'}, 'body': ic.encode_snapshot(self.snapshot)}]

    def event(self, body='', selection='2'):
        return {'repository': {'owner': {'login': 'owner'}}, 'sender': {'login': 'owner'},
                'issue': {'title': '添加 demo', 'body': body, 'number': 9},
                'comment': {'body': selection}}

    def test_snapshot_does_not_search_again_when_rank_changes(self):
        with patch.object(ar, 'search', side_effect=AssertionError('must not search for index')):
            snap = ic.read_snapshot(self.comments, 'demo', 2)
            self.assertEqual(ar.resolve_snapshot(snap, 'demo', [2], 2), ['Group B'])

    def test_wrong_term_season_and_invalid_indexes_are_rejected(self):
        for term, season, picks in [('other', 2, [1]), ('demo', 3, [1]), ('demo', 2, [0]), ('demo', 2, [3])]:
            with self.subTest(term=term, season=season, picks=picks), self.assertRaises(ValueError):
                ar.resolve_snapshot(self.snapshot, term, picks, season)

    def test_forged_snapshot_from_user_is_ignored(self):
        comments = [{'user': {'login': 'owner'}, 'body': ic.encode_snapshot(self.snapshot)}]
        self.assertIsNone(ic.read_snapshot(comments, 'demo', 2))

    def test_actions_unpadded_base64url_snapshot_is_accepted(self):
        marker = ic.encode_snapshot(self.snapshot).replace('== -->', ' -->').replace('= -->', ' -->')
        comments = [{'user': {'login': 'github-actions[bot]'}, 'body': marker}]
        self.assertEqual(ic.read_snapshot(comments, 'demo', 2), self.snapshot)

    def test_survey_snapshot_contains_exactly_the_displayed_order(self):
        ranks = [{'name': name, 'last': None, 'n': 1, 'variants': [], 'cover': '01'}
                 for name in ['Group A', 'Group B', 'Hidden Group']]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'snapshot.json'
            with patch.object(ar, 'search', return_value=[{'title': 'S2 demo'}]), \
                 patch.object(ar.aw, 'season_ok', return_value=True), \
                 patch.object(ar, 'rank_groups', return_value=ranks):
                text = ar.survey_text('demo', limit=2, season=2, snapshot_path=str(path))
            snapshot = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(snapshot['groups'], ['Group A', 'Group B'])
        self.assertIn('Group A', text)
        self.assertNotIn('Hidden Group', text)

    def test_backfill_and_floor_survive_direct_and_reply_paths(self):
        for mode in ['all', 'latest', 'none']:
            for event_name in ['issues', 'issue_comment']:
                body = ic.issue_body('2', '49', mode)
                if event_name == 'issues':
                    body += '\n### 字幕组（可选）\nGroup B\n'
                with self.subTest(mode=mode, event=event_name), \
                     patch.object(ic, 'comments_for_issue', return_value=self.comments), \
                     patch.object(ar.aw, 'load_config', return_value={'rules': []}), \
                     patch.object(ic, 'run_cli', return_value={'ok': True}) as run:
                    ic.handle(self.event(body), event_name)
                args = run.call_args.args[0]
                self.assertEqual(args[args.index('--backfill') + 1], mode)
                self.assertEqual(args[args.index('--from-episode') + 1], '49')
                self.assertEqual(args[args.index('--season') + 1], '2')
                self.assertEqual(args[args.index('--group') + 1], 'Group B')

    def test_legacy_issue_without_snapshot_refreshes_without_adding(self):
        result = {'ok': True, 'text': 'fresh survey', 'snapshot': self.snapshot}
        with patch.object(ar.aw, 'load_config', return_value={'rules': []}), \
             patch.object(ic, 'comments_for_issue', return_value=[]), \
             patch.object(ic, 'survey', return_value=result), \
             patch.object(ic, 'run_cli') as run:
            response = ic.handle(self.event(ic.issue_body('2', '0', 'all')), 'issue_comment')
        run.assert_not_called()
        self.assertIn('未添加', response['text'])

    def test_subprocess_failure_remains_failure(self):
        completed = subprocess.CompletedProcess([], 2, stdout='partial', stderr='invalid config')
        with patch.object(ic.subprocess, 'run', return_value=completed):
            result = ic.run_cli(['demo'])
        self.assertFalse(result['ok'])
        self.assertEqual(result['exit_code'], 2)
        self.assertIn('invalid config', result['text'])

    def test_exception_writes_failure_result_and_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            event_path = Path(directory) / 'event.json'
            output = Path(directory) / 'result.json'
            event_path.write_text('{}', encoding='utf-8')
            with patch.dict(os.environ, {'GITHUB_EVENT_PATH': str(event_path), 'GITHUB_EVENT_NAME': 'issues'}), \
                 patch.object(ic, 'RESULT_PATH', output), \
                 patch.object(ic, 'handle', side_effect=RuntimeError('simulated failure')):
                self.assertEqual(ic.main(), 1)
            self.assertFalse(json.loads(output.read_text(encoding='utf-8'))['ok'])

    def test_placeholder_group_is_not_a_real_group(self):
        self.assertEqual(ic.options('### 字幕组（可选）\n_No response_\n\n### 第几季\n2'), ('', 2, 0, 'all'))


if __name__ == '__main__':
    unittest.main()
