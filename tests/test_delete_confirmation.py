import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import add_rule as ar
import issue_commands as ic
from watcher_core import delete_confirmation as dc, natural_language as nl, rules


def rule(rid='r-one', group='绿茶发布机'):
    return {'id': rid, 'name': f'青之箱（{group}）', 'season': 2,
            'group': [group], 'keywords': ['青之箱'], 'enabled': True}


def event(target, checked=True):
    tick = 'x' if checked else ' '
    return {'action': 'opened', 'sender': {'login': 'owner'},
            'repository': {'owner': {'login': 'owner'}},
            'issue': {'number': 1, 'title': '确认删除 ' + target['id'],
                      'body': f'### 删除对象\n{rules.target_label(target)}\n\n### 确认删除\n- [{tick}] {dc.CONFIRM}\n\n### 删除校验信息\n{dc.marker(target)}'}}


class DeleteConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.one = rule()
        self.two = rule('r-two', '绿茶字幕组')
        self.config = {'settings': {}, 'rules': [self.one, self.two]}

    def test_display_includes_season_once(self):
        self.assertEqual(rules.display_name(self.one), '青之箱 第二季')
        self.assertEqual(rules.display_name(dict(self.one, name='青之箱 第二季（绿茶发布机）')), '青之箱 第二季')

    def test_ambiguous_name_lists_targets_instead_of_deleting(self):
        with self.assertRaisesRegex(ValueError, 'r-one[\s\S]*r-two'):
            rules.select_rule(self.config['rules'], '青之箱', 2)
        self.assertEqual(rules.select_rule(self.config['rules'], '青之箱 第二季 绿茶字幕组')['id'], 'r-two')

    def test_colon_and_nested_delete_titles(self):
        self.assertEqual(ic.command('删除：青之箱'), ('del', '青之箱'))
        request = nl.parse('删除：帮我删除青之箱 第二季')
        self.assertEqual((request.mode, request.term, request.season), ('del', '青之箱', 2))

    def test_unsupported_variant_clause_rejected_before_mutation(self):
        for prefix in ('帮我追', '加番 ', '请帮我追'):
            with self.subTest(prefix=prefix), patch.object(ic, 'run_cli') as cli, self.assertRaisesRegex(ValueError, '字幕格式筛选'):
                ic.handle({'sender': {'login': 'owner'}, 'repository': {'owner': {'login': 'owner'}},
                           'issue': {'title': prefix + '青之箱第二季，从第一集开始，字幕格式为简日双语或简体内嵌', 'body': ''}}, 'issues')
            cli.assert_not_called()

    def test_confirmation_url_prefills_target_and_snapshot_not_checkbox(self):
        url = dc.delete_url(self.one, 'owner/repo')
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query['template'], ['confirm-delete.yml'])
        self.assertEqual(query['target'], [rules.target_label(self.one)])
        self.assertEqual(query['snapshot'], [dc.marker(self.one)])
        self.assertNotIn('confirm', query)
        self.assertEqual(dc.delete_url(self.one, 'https://wrong'), '')

    def test_checked_confirmation_routes_only_exact_id(self):
        with patch.object(ar.aw, 'load_config', return_value=self.config), patch.object(ic, 'run_cli', return_value={'ok': True, 'step': 'done', 'text': 'deleted'}) as cli:
            result = ic.handle(event(self.one), 'issues')
        cli.assert_called_once_with(['--del-id', 'r-one', '--no-push'])
        self.assertIn('青之箱 第二季（绿茶发布机）', result['text'])

    def test_unchecked_confirmation_does_not_delete(self):
        with patch.object(ar.aw, 'load_config', return_value=self.config), patch.object(ic, 'run_cli') as cli, self.assertRaisesRegex(ValueError, '尚未勾选'):
            ic.handle(event(self.one, False), 'issues')
        cli.assert_not_called()

    def test_changed_or_mismatched_target_rejected(self):
        for mutation in ('title', 'display', 'config', 'marker'):
            e = event(self.one)
            config = copy.deepcopy(self.config)
            if mutation == 'title':
                e['issue']['title'] = '确认删除 r-two'
            elif mutation == 'display':
                e['issue']['body'] = e['issue']['body'].replace('删除对象\n青之箱', '删除对象\n药屋少女')
            elif mutation == 'config':
                config['rules'][0]['enabled'] = False
            else:
                e['issue']['body'] = e['issue']['body'].replace(dc.marker(self.one), '')
            with self.subTest(mutation=mutation), patch.object(ar.aw, 'load_config', return_value=config), patch.object(ic, 'run_cli') as cli, self.assertRaises(ValueError):
                ic.handle(e, 'issues')
            cli.assert_not_called()

    def test_removed_id_never_matches_name_of_another_rule(self):
        other = dict(self.two, name='r-one')
        with patch.object(ar.aw, 'load_config', return_value={'rules': [other]}), patch.object(ic, 'run_cli') as cli, self.assertRaisesRegex(ValueError, '已不存在'):
            ic.handle(event(self.one), 'issues')
        cli.assert_not_called()

    def test_non_owner_and_pr_rejected(self):
        for kind in ('sender', 'pr'):
            e = event(self.one)
            if kind == 'sender':
                e['sender']['login'] = 'outsider'
            else:
                e['issue']['pull_request'] = {}
            with patch.object(ic, 'run_cli') as cli, self.assertRaises(ValueError):
                ic.handle(e, 'issues')
            cli.assert_not_called()

    def test_comment_or_edit_does_not_repeat_delete(self):
        for event_name, action in [('issue_comment', 'created'), ('issues', 'edited')]:
            e = event(self.one)
            e['action'] = action
            with patch.object(ic, 'run_cli') as cli:
                self.assertTrue(ic.handle(e, event_name)['ok'])
            cli.assert_not_called()

    def test_cli_exact_delete_preserves_other_rule(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / 'config.json'
            config_path.write_text(json.dumps(self.config, ensure_ascii=False), encoding='utf8')
            with patch.object(ar.aw, 'CONFIG_PATH', str(config_path)), patch.object(ar, 'purge_baseline', return_value=0) as purge, patch.object(ar, 'write_watchlist'), patch.object(ar, 'maybe_push'), patch.object(sys, 'argv', ['add_rule.py', '--del-id', 'r-one', '--no-push']):
                ar.main()
            self.assertEqual(json.loads(config_path.read_text(encoding='utf8'))['rules'], [self.two])
            purge.assert_called_once_with(['r-one'])

    def test_watchlist_has_distinct_confirm_links(self):
        with patch.object(ar.aw, 'load_config', return_value=self.config), patch.object(ar.aw, 'load_state', return_value={}), patch.dict(os.environ, {'GITHUB_REPOSITORY': 'owner/repo'}):
            text = ar.render_watchlist()
        self.assertEqual(text.count('[删除]('), 2)
        self.assertIn('青之箱 第二季 / r-one', text)
        self.assertIn('青之箱 第二季 / r-two', text)

    def test_form_checkbox_is_required(self):
        import yaml
        form = yaml.safe_load(Path('.github/ISSUE_TEMPLATE/confirm-delete.yml').read_text(encoding='utf8'))
        fields = {field['id']: field for field in form['body'] if 'id' in field}
        self.assertTrue(fields['confirm']['attributes']['options'][0]['required'])
        self.assertEqual(fields['confirm']['attributes']['options'][0]['label'], dc.CONFIRM)
        self.assertTrue(fields['target']['validations']['required'])
