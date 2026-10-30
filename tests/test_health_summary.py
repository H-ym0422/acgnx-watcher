import unittest
from unittest.mock import patch

import acgnx_watch as aw
from watcher_core import health


class HealthSummaryTests(unittest.TestCase):
    def setUp(self):
        self.rules = [{'id': rid, 'name': name} for rid, name in
                      [('a', '番剧甲'), ('b', '番剧乙'), ('c', '番剧丙')]]
        self.state = {'episodes': {'a|01': '0:1'}, 'initialized': True}

    def result(self, failed=(), available=True):
        return health.CollectionResult(
            successful_sources=int(available), failed_sources=len(failed),
            rule_sources={rule['id']: {'successful_sources': int(rule['id'] not in failed),
                                      'failed_sources': int(rule['id'] in failed)}
                          for rule in self.rules})

    def update(self, result):
        return health.update_alerts(self.state, result, self.rules, 3)

    def outage(self):
        return self.result(('a', 'b', 'c'), available=False)

    def test_global_outage_and_recovery_each_notify_once(self):
        self.assertEqual(self.update(self.outage()), [])
        self.assertEqual(self.update(self.outage()), [])
        alerts = self.update(self.outage())
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0][0], '抓取异常')
        for rule in self.rules:
            self.assertIn(rule['name'], alerts[0][1])
            self.assertEqual(self.state['rule_health'][rule['id']]['failures'], 3)
        self.assertEqual(self.update(self.outage()), [])
        recovered = self.update(self.result())
        self.assertEqual([title for title, _ in recovered], ['抓取恢复'])
        self.assertEqual(self.update(self.result()), [])
        self.assertEqual(self.state['episodes'], {'a|01': '0:1'})

    def test_one_rule_still_alerts_when_other_sources_work(self):
        for _ in range(2):
            self.assertEqual(self.update(self.result(('a',))), [])
        alerts = self.update(self.result(('a',)))
        self.assertEqual(alerts[0][0], '单部番搜索异常')
        self.assertIn('番剧甲', alerts[0][1])
        self.assertEqual(self.update(self.result(('a',))), [])
        self.assertEqual(self.update(self.result())[0][0], '单部番搜索恢复')

    def test_multiple_rule_failures_and_recoveries_are_merged(self):
        for _ in range(2):
            self.update(self.result(('a', 'b')))
        alerts = self.update(self.result(('a', 'b')))
        self.assertEqual(len(alerts), 1)
        self.assertIn('番剧甲', alerts[0][1])
        self.assertIn('番剧乙', alerts[0][1])
        self.assertEqual(len(self.update(self.result())), 1)
        self.assertEqual(self.update(self.result()), [])

    def test_partial_global_recovery_names_remaining_failure(self):
        for _ in range(3):
            self.update(self.outage())
        alerts = self.update(self.result(('a',)))
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0][0], '抓取恢复')
        self.assertIn('仍未恢复搜索的番剧：番剧甲', alerts[0][1])
        self.assertEqual(self.state['rule_health']['a']['status'], 'failed')
        self.assertEqual(self.update(self.result(('a',))), [])
        self.assertEqual(self.update(self.result())[0][0], '单部番搜索恢复')

    def test_brief_global_outage_does_not_hide_independent_failure(self):
        for _ in range(2):
            self.update(self.result(('a',)))
        self.assertEqual(self.update(self.outage()), [])
        alerts = self.update(self.result(('a',)))
        self.assertEqual(alerts[0][0], '单部番搜索异常')

    def test_legacy_alerted_rules_do_not_fan_out_on_global_recovery(self):
        for _ in range(3):
            health.update(self.state, self.outage(), 3)
            health.update_rules(self.state, self.outage(), self.rules, 3)
        self.assertEqual(len(self.update(self.result())), 1)
        self.assertEqual(self.update(self.result()), [])

    def test_cycle_sends_one_failure_and_one_recovery_without_real_notifications(self):
        cfg = {'settings': {'push_min_interval_seconds': 0}, 'rules': self.rules}
        with patch.object(aw, 'collect', side_effect=[self.outage()] * 4 + [self.result()] * 2), \
             patch.object(aw, 'save_state'), \
             patch.object(aw, 'send_notification', return_value=(True, '')) as send:
            for _ in range(6):
                aw.run_cycle(cfg, self.state)
        self.assertEqual([call.args[0] for call in send.call_args_list],
                         ['【运行提醒】抓取异常', '【运行提醒】抓取恢复'])
        self.assertEqual(self.state['pending_notifications'], {})
        self.assertTrue(self.state['initialized'])

    def test_dry_run_does_not_change_or_send_health_alerts(self):
        cfg = {'settings': {}, 'rules': self.rules}
        with patch.object(aw, 'collect', return_value=self.outage()), \
             patch.object(aw, 'send_notification') as send:
            aw.run_cycle(cfg, self.state, dry_run=True)
        send.assert_not_called()
        self.assertNotIn('health', self.state)
        self.assertNotIn('rule_health', self.state)
