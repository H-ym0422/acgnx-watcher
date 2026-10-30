"""Fetch quality and bounded alert transitions; independent of push provider."""
from datetime import datetime, timezone, timedelta


class CollectionResult(list):
    def __init__(self, items=(), successful_sources=0, failed_sources=0, rule_sources=None, ambiguous=None,
                 global_feed_available=False):
        super().__init__(items)
        self.successful_sources = successful_sources
        self.failed_sources = failed_sources
        self.rule_sources = rule_sources or {}
        self.ambiguous = ambiguous or {}
        self.global_feed_available = global_feed_available

    @property
    def available(self):
        return self.successful_sources > 0


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')


def update(state, result, threshold):
    health = state.setdefault('health', {})
    health['last_check'] = now()
    health['matched'] = len(result)
    health['successful_sources'] = getattr(result, 'successful_sources', 1)
    health['failed_sources'] = getattr(result, 'failed_sources', 0)
    available = getattr(result, 'available', True)
    if not available:
        health['failures'] = health.get('failures', 0) + 1
        health['status'] = 'failed'
        if health['failures'] >= threshold and not health.get('alerted'):
            health['alerted'] = True
            return '抓取异常', f'连续 {health["failures"]} 次未能取得有效 RSS；本轮没有推进初始化或去重基线。'
    else:
        was_alerted = health.pop('alerted', False)
        health['failures'] = 0
        health['status'] = 'partial' if health['failed_sources'] else 'ok'
        health['last_successful_check'] = health['last_check']
        if was_alerted:
            return '抓取恢复', '已恢复有效 RSS 抓取，继续检查新资源。'
    return None


def update_rules(state, result, rules, threshold, notify=True):
    records = state.setdefault('rule_health', {})
    alerts = []
    active = {r['id']: r for r in rules if r.get('enabled', True)}
    for rid in list(records):
        if rid not in active:
            del records[rid]
    for rid, rule in active.items():
        source = getattr(result, 'rule_sources', {}).get(rid)
        if source is None:
            source = {'successful_sources': getattr(result, 'successful_sources', 1),
                      'failed_sources': getattr(result, 'failed_sources', 0)}
        record = records.setdefault(rid, {})
        record.update(source, last_check=now())
        ambiguous = getattr(result, 'ambiguous', {}).get(rid, [])
        record['ambiguous_episodes'] = sorted(set(ambiguous))
        if source['successful_sources'] == 0:
            record['failures'] = record.get('failures', 0) + 1
            record['status'] = 'failed'
            if notify and record['failures'] >= threshold and not record.get('alerted'):
                record['alerted'] = True
                alerts.append((rid, '单部番搜索异常', f"{rule.get('name', rid)}：连续 {record['failures']} 次未取得有效 RSS 搜索结果；查询、选组及历史补抓受影响，新集检索可能不完整。"))
        else:
            recovering = record.pop('alerted', False)
            record['failures'] = 0
            record['status'] = 'needs_confirmation' if ambiguous else ('partial' if source['failed_sources'] else 'ok')
            record['last_successful_check'] = record['last_check']
            if recovering:
                alerts.append((rid, '单部番搜索恢复', f"{rule.get('name', rid)}：RSS 搜索已恢复。"))
    return alerts


def update_alerts(state, result, rules, threshold):
    transition = update(state, result, threshold)
    available = getattr(result, 'available', True)
    changes = update_rules(state, result, rules, threshold, notify=available)
    active = [rule for rule in rules if rule.get('enabled', True)]
    if not available:
        if not transition:
            return []
        names = '、'.join(rule.get('name', rule['id']) for rule in active)
        body = transition[1]
        if names:
            body += f'\n受影响番剧：{names}。'
        return [(transition[0], body)]
    if transition:
        failed = [rule.get('name', rule['id']) for rule in active
                  if state['rule_health'][rule['id']]['status'] == 'failed']
        body = transition[1]
        if failed:
            body = '已恢复部分 RSS 抓取，继续检查可用资源。\n仍未恢复搜索的番剧：' + '、'.join(failed) + '。'
        return [(transition[0], body)]
    if len(changes) == 1:
        body = changes[0][2]
        if getattr(result, 'global_feed_available', False):
            body += '\n最新资源流仍可用；搜索失败不代表字幕组没有更新。'
        return [(changes[0][1], body)]
    if changes:
        body = '\n'.join(f'{title}：{body}' for _, title, body in changes)
        if getattr(result, 'global_feed_available', False):
            body += '\n最新资源流仍可用；以上为关键词搜索状态，不代表整个网站不可用。'
        return [('番剧搜索状态更新', body)]
    return []
