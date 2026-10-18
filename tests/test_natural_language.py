import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import issue_commands as commands
import add_rule as ar
from watcher_core.natural_language import parse, number
from watcher_core import resource_query, rules
from watcher_core.health import CollectionResult


EXAMPLE = '给我找药屋少女的呢喃第三季第三集之后的资源，字幕组为喵萌奶茶屋'


def event(title, body=''):
    return {'sender': {'login': 'owner'}, 'repository': {'owner': {'login': 'owner'}},
            'issue': {'number': 1, 'title': title, 'body': body}}


class Parsing(unittest.TestCase):
    def test_user_sentence(self):
        r = parse(EXAMPLE)
        self.assertEqual((r.mode, r.term, r.season, r.floor, r.end, r.groups),
                         ('search', '药屋少女的呢喃', 3, 3, None, ['喵萌奶茶屋']))
        self.assertIn('含该集', r.summary())

    def test_subscription_is_explicit(self):
        r = parse('帮我追药屋少女的呢喃第三季，从第三集起，字幕组为喵萌奶茶屋')
        self.assertEqual((r.mode, r.season, r.floor, r.end), ('add', 3, 3, None))

    def test_chinese_numbers(self):
        for text, value in [('三', 3), ('十二', 12), ('二十三', 23), ('一百零三', 103), ('两百', 200), ('51', 51)]:
            with self.subTest(text=text):
                self.assertEqual(number(text), value)

    def test_bad_numbers(self):
        for value in ['零', '0', '一二', '十十', '10000']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                number(value)

    def test_single_and_range(self):
        self.assertEqual(parse('给我找demo第三集').end, 3)
        r = parse('给我找demo第三至第五集的资源')
        self.assertEqual((r.floor, r.end, r.term), (3, 5, 'demo'))

    def test_invalid_scope(self):
        for text in ['给我找demo第五至第三集', '给我找demo第三季第四季', '给我找demo第三集第五集', '帮我追demo第三集', '给我找demo第-3集']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse(text)

    def test_legacy_remains_legacy(self):
        for text in ['加番 demo', '删番 r-123', '暂停 r-123', '列表', '修改 r-123']:
            self.assertIsNone(parse(text))

    def test_backfill(self):
        r = parse('帮我追demo，只推最新一集')
        self.assertEqual((r.backfill, r.floor, r.term), ('latest', None, 'demo'))
        self.assertEqual(parse('帮我追demo，不补旧集').backfill, 'none')

    def test_groups(self):
        r = parse('帮我追demo，字幕组为甲组和乙组，不兜底')
        self.assertEqual(r.groups, ['甲组', '乙组'])
        self.assertTrue(r.strict_group)
        self.assertTrue(parse('帮我追demo，字幕组为任意').any_group)

    def test_management(self):
        for title, mode in [('帮我暂停demo第三季', 'pause'), ('帮我恢复demo', 'resume'), ('取消订阅demo', 'del'), ('我的追番列表', 'list')]:
            self.assertEqual(parse(title).mode, mode)
        r = parse('帮我把demo第三季的字幕组改为喵萌奶茶屋')
        self.assertEqual((r.mode, r.term, r.season, r.groups), ('edit', 'demo', 3, ['喵萌奶茶屋']))

    def test_no_multi_action_or_partial_delete(self):
        for title in ['帮我追demo然后删除foo', '帮我删除demo第三集之后']:
            with self.assertRaises(ValueError):
                parse(title)


class Routing(unittest.TestCase):
    def test_search_never_calls_cli(self):
        with patch('watcher_core.issue_natural.query', return_value={'ok': True, 'step': 'query', 'text': 'result'}) as query, patch.object(commands, 'run_cli') as cli:
            result = commands.handle(event(EXAMPLE), 'issues')
        query.assert_called_once()
        cli.assert_not_called()
        self.assertIn('识别结果：仅查询', result['text'])

    def test_template_optional_notes(self):
        with patch('watcher_core.issue_natural.query', return_value={'ok': True, 'step': 'query', 'text': 'result'}) as query:
            commands.handle(event(EXAMPLE, '### 补充说明（可留空）\n仅查询验证'), 'issues')
        query.assert_called_once()

    def test_add_arguments(self):
        with patch.object(commands, 'run_cli', return_value={'ok': True, 'text': 'saved'}) as cli:
            commands.handle(event('帮我追demo第三季，从第三集起，字幕组为喵萌奶茶屋，不兜底'), 'issues')
        args = cli.call_args.args[0]
        self.assertEqual(args[0], 'demo')
        for flag, value in [('--season', '3'), ('--from-episode', '3'), ('--group', '喵萌奶茶屋')]:
            self.assertEqual(args[args.index(flag) + 1], value)
        self.assertIn('--no-fallback', args)

    def test_conflict_no_mutation(self):
        with patch.object(commands, 'run_cli') as cli, self.assertRaises(ValueError):
            commands.handle(event('帮我追demo第三季，字幕组为甲组', '### 第几季\n2'), 'issues')
        cli.assert_not_called()

    def test_owner_required(self):
        e = event(EXAMPLE)
        e['sender']['login'] = 'stranger'
        with patch('watcher_core.issue_natural.query') as query, self.assertRaises(ValueError):
            commands.handle(e, 'issues')
        query.assert_not_called()

    def test_unique_management_target(self):
        cfg = {'rules': [{'id': 's2', 'name': 'demo', 'season': 2}, {'id': 's3', 'name': 'demo', 'season': 3}]}
        with patch.object(ar.aw, 'load_config', return_value=cfg), patch.object(commands, 'run_cli', return_value={'ok': True, 'text': 'done'}) as cli:
            commands.handle(event('帮我删除demo第三季'), 'issues')
            self.assertEqual(cli.call_args.args[0], ['--del-name', 's3', '--no-push'])
            with self.assertRaises(ValueError):
                commands.handle(event('帮我删除demo'), 'issues')

    def test_comments_do_not_repeat_management(self):
        with patch.object(commands, 'run_cli') as cli:
            commands.handle(event('帮我删除demo'), 'issue_comment')
        cli.assert_not_called()

    def test_survey_comment_preserves_fields(self):
        title = '帮我追demo第三季，从第三集起'
        snapshot = {'version': 1, 'term': 'demo', 'season': 3, 'groups': ['甲组']}
        comments = [{'user': {'login': 'github-actions[bot]'}, 'body': commands.encode_snapshot(snapshot)}]
        e = event(title)
        e['comment'] = {'body': '1'}
        with patch.object(commands, 'comments_for_issue', return_value=comments), patch.object(commands, 'run_cli', return_value={'ok': True, 'text': 'done'}) as cli:
            commands.handle(e, 'issue_comment')
        args = cli.call_args.args[0]
        self.assertEqual(args[args.index('--season') + 1], '3')
        self.assertEqual(args[args.index('--from-episode') + 1], '3')
        self.assertEqual(args[args.index('--group') + 1], '甲组')


class Query(unittest.TestCase):
    def setUp(self):
        self.cfg = {'settings': {}, 'rules': []}
        self.state = {'episodes': {'old': '0:1'}}
        self.items = [{'title': f'[甲组] demo S3 - {ep} 1080p', 'author': '甲组', 'btih': ep,
                       'episode': ep, 'magnet': 'magnet:' + ep} for ep in ['02', '03', '04', '01-05', '']]

    def run_query(self, title, items=None):
        cfg_before, state_before = copy.deepcopy(self.cfg), copy.deepcopy(self.state)
        with patch.object(ar.aw, 'load_config', return_value=self.cfg), patch.object(ar.aw, 'load_state', return_value=self.state), patch.object(ar.aw, 'collect', return_value=CollectionResult(self.items if items is None else items, 1)) as collect, patch.object(ar.aw, 'save_state') as save, patch.object(ar, 'notify_result') as send, patch.object(ar, 'append_rule') as add:
            result = resource_query.query(parse(title), ar)
        save.assert_not_called()
        send.assert_not_called()
        add.assert_not_called()
        self.assertEqual(self.cfg, cfg_before)
        self.assertEqual(self.state, state_before)
        probe = collect.call_args.args[0]
        self.assertFalse(probe['rules'][0]['fallback_any_group'])
        return result

    def test_query_is_readonly_with_inclusive_floor(self):
        result = self.run_query('给我找demo第三季第三集之后的资源，字幕组为甲组')
        self.assertIn('magnet:03', result['text'])
        self.assertIn('magnet:04', result['text'])
        self.assertNotIn('magnet:02', result['text'])
        self.assertIn('magnet:01-05', result['text'])

    def test_exact_and_range(self):
        exact = self.run_query('给我找demo第三集')['text']
        self.assertIn('magnet:03', exact)
        self.assertNotIn('magnet:04', exact)
        bounded = self.run_query('给我找demo第二至第三集')['text']
        self.assertIn('magnet:02', bounded)
        self.assertNotIn('magnet:04', bounded)

    def test_zero_not_stopped(self):
        result = self.run_query('给我找demo', [])
        self.assertIn('不代表字幕组停更', result['text'])

    def test_unavailable_not_empty_success(self):
        with patch.object(ar.aw, 'load_config', return_value=self.cfg), patch.object(ar.aw, 'load_state', return_value=self.state), patch.object(ar.aw, 'collect', return_value=CollectionResult(failed_sources=1)):
            result = resource_query.query(parse('给我找demo'), ar)
        self.assertFalse(result['ok'])

    def test_legacy_season_duplicate_identity(self):
        old = {'id': 'old', 'keywords': ['药屋少女的呢喃 第三季'], 'season': 3, 'group': ['喵萌奶茶屋']}
        new = dict(old, keywords=['药屋少女的呢喃'])
        self.assertEqual(rules.identity(old), rules.identity(new))
        self.assertNotEqual(rules.identity(old), rules.identity(dict(new, season=2)))
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'config.json'
            path.write_text(json.dumps({'rules': [old]}), encoding='utf8')
            before = path.read_bytes()
            self.assertFalse(rules.append(path, new))
            self.assertEqual(path.read_bytes(), before)
