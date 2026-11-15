import unittest

import acgnx_watch as aw


class ReleaseNotificationTests(unittest.TestCase):
    def test_nest_release_uses_the_group_in_the_rss_title(self):
        release = {
            'title': '[NEST] 某暗部的少女共栖 / A Certain Dark Item - 06 [简繁日内封]',
            'author': '转发账号', 'episode': '06', '_variant': '简繁日内封', 'size': '1.4GB',
        }
        rule = {'name': '某暗部的少女共栖', 'group': ['NEST']}
        title, body = aw.build_push_content(release, rule, '【新番速递】')
        self.assertEqual(title, '【新番速递】某暗部的少女共栖 第06集（NEST）')
        self.assertTrue(body.startswith('字幕形式：简繁日内封\n集数：第 06 集\n大小：1.4GB'))
        self.assertIn('发布标题：[NEST]', body)

    def test_fallback_revision_keeps_release_group_and_explains_configured_group(self):
        release = {
            'title': '[NEST] 示例动画 - 06v2', 'author': 'NEST', 'episode': '06',
            '_rev': 2, '_fallback': True,
        }
        rule = {'name': '示例动画（特别篇）（喵萌奶茶屋）', 'group': ['喵萌奶茶屋']}
        title, body = aw.build_push_content(release, rule, '【新番速递】', True)
        self.assertEqual(title, '【资源更新】（修正版）示例动画（特别篇） 第06集（NEST）')
        self.assertIn('已等满 7 天，仍未检测到「喵萌奶茶屋」', body)

    def test_publisher_label_is_not_replaced_by_a_matching_author_alias(self):
        release = {'title': '[绿茶字幕组] 示例动画 - 01', 'author': '绿茶发布机', 'episode': '01'}
        rule = {'name': '示例动画（绿茶发布机）', 'group': ['绿茶发布机']}
        title, _ = aw.build_push_content(release, rule, '【新番速递】')
        self.assertEqual(title, '【新番速递】示例动画 第01集（绿茶字幕组）')
