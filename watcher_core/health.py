"""Fetch quality and bounded alert transitions; independent of push provider."""
from datetime import datetime, timezone, timedelta


class CollectionResult(list):
    def __init__(self, items=(), successful_sources=0, failed_sources=0):
        super().__init__(items)
        self.successful_sources = successful_sources
        self.failed_sources = failed_sources

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
