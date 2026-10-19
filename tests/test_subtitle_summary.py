import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import add_rule as ar
from watcher_core.subtitle_summary import compact_episodes, subtitle_lines, variant_of


def release(ep, variant='', group='喵萌奶茶屋'):
    item = {'title': f'[{group}] 青之箱 第二季 [{ep}]' + (f'[{variant}]' if variant else ''),
            'episode': ep, 'author': group, 'pub_dt': None, 'magnet': f'magnet:{group}:{ep}'}
    return ep, item


class SubtitleSummaryTests(unittest.TestCase):
    def test_continuous(self):
        self.assertEqual(compact_episodes(map(str, range(1, 11))), '01~10')

    def test_non_continuous(self):
        self.assertEqual(compact_episodes(['22', '03', '01', '10', '05']), '01、03、05、10、22')

    def test_mixed_runs_and_duplicate_ranges(self):
        self.assertEqual(compact_episodes(['03', '01-02', '02', '05', '08-10', '09']), '01~03、05、08~10')
        self.assertEqual(compact_episodes(['001', '1000-9999']), '01、1000~9999')

    def test_unknown_and_special_episode(self):
        self.assertEqual(compact_episodes(['', 'a' * 40, 'SP01']), 'SP01、集数未识别')
        self.assertEqual(compact_episodes(['03-01']), '03-01')

    def test_different_variants_are_mapped_to_their_episodes(self):
        rows = [release('01', '简日双语'), release('02', '简繁内封')]
        self.assertEqual(subtitle_lines(rows), ['01 简日双语', '02 简繁内封'])

    def test_group_boundaries(self):
        rows = [release('01', '简日双语', '甲字幕组'), release('02', '简日双语', '乙字幕组')]
        lines = subtitle_lines(rows, lambda item: item['author'])
        self.assertEqual(lines, ['01 简日双语（甲字幕组）', '02 简日双语（乙字幕组）'])

    def test_one_episode_can_have_multiple_available_versions(self):
        self.assertEqual(subtitle_lines([release('01', '简日双语'), release('01', '简繁内封')]),
                         ['01 简日双语', '01 简繁内封'])

    def test_unknown_variant_not_inferred(self):
        ep, item = release('02')
        self.assertEqual(variant_of(item), '未标注，请查看发布说明')
        self.assertEqual(subtitle_lines([release('01', '简日双语'), (ep, item)]),
                         ['01 简日双语', '02 未标注，请查看发布说明'])

    def test_collected_variant_is_preferred(self):
        ep, item = release('03', '简体')
        item['_variant'] = '简日内嵌'
        self.assertEqual(subtitle_lines([(ep, item)]), ['03 简日内嵌'])

    def test_traditional_spellings_merge_but_formats_stay_distinct(self):
        rows = [release('01', '簡日雙語'), release('02', '简日双语'), release('03', '简日内嵌')]
        self.assertEqual(subtitle_lines(rows), ['01~02 簡日雙語', '03 简日内嵌'])


class SubtitleOutputTests(unittest.TestCase):
    def setUp(self):
        self.rule = {'id': 'test', 'name': '青之箱 第二季', 'group': ['喵萌奶茶屋']}
        self.rows = [release('01', '简日双语'), release('02', '简繁内封')]

    def format(self, mode='all'):
        with patch.object(ar.aw, 'load_config', return_value={'settings': {}}), patch.object(ar, '_group_of', side_effect=lambda i: i['author']):
            return ar.format_result(self.rule, self.rows, mode)[1]

    def test_history_summary_and_each_link_include_variant(self):
        body = self.format()
        self.assertIn('01 简日双语（喵萌奶茶屋）', body)
        self.assertIn('02 简繁内封（喵萌奶茶屋）', body)
        self.assertIn('[01] 喵萌奶茶屋 · 简日双语 magnet:喵萌奶茶屋:01', body)
        self.assertIn('[02] 喵萌奶茶屋 · 简繁内封 magnet:喵萌奶茶屋:02', body)

    def test_latest_only_describes_selected_resource(self):
        body = self.format('latest')
        self.assertIn('字幕形式：简繁内封', body)
        self.assertNotIn('简日双语', body)
        self.assertIn('magnet:喵萌奶茶屋:02', body)
        self.assertNotIn('magnet:喵萌奶茶屋:01', body)

    def test_none_remains_no_resources(self):
        self.assertNotIn('magnet:', self.format('none'))

    def test_survey_mapping_and_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            snapshot = Path(root) / 'survey.json'
            with patch.object(ar, 'search', return_value=[item for _, item in self.rows]):
                body = ar.survey_text('青之箱', season=2, snapshot_path=str(snapshot))
            self.assertIn('01 简日双语', body)
            self.assertIn('02 简繁内封', body)
            data = json.loads(snapshot.read_text(encoding='utf8'))
            self.assertEqual(data['groups'], ['喵萌奶茶屋'])
            self.assertEqual(ar.resolve_snapshot(data, '青之箱', [1], 2), ['喵萌奶茶屋'])

    def test_survey_lists_available_variants_without_claiming_all_episodes_share_them(self):
        items = [item for _, item in [*self.rows, release('01', '简繁内封')]]
        with patch.object(ar, 'search', return_value=items):
            body = ar.survey_text('青之箱', season=2)
        self.assertIn('01 简日双语', body)
        self.assertIn('01~02 简繁内封', body)
        self.assertIn('可选版本', body)
