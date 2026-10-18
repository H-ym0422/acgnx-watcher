"""A persisted, per-episode grace period for releases from other groups."""
from datetime import datetime, timedelta, timezone
import json

WAIT_DAYS = 7


def now():
    return datetime.now(timezone.utc)


def signature(rule):
    fields = ('keywords', 'group', 'season', 'include_any', 'exclude_any', 'from_episode',
              'allow_unmarked_season', 'variant_prefer', 'fallback_any_group')
    return json.dumps({k: rule.get(k) for k in fields}, sort_keys=True, ensure_ascii=False)


def rank(item):
    date = item.get('pub_dt')
    return (item.get('_tier', 0), -item.get('_rev', 1), -(date.timestamp() if date else 0))


def freeze(item):
    fields = ('title', 'author', 'episode', 'btih', 'magnet', 'link', 'size',
              '_tier', '_rev', '_seen', '_variant', '_rid', '_fallback')
    value = {k: item[k] for k in fields if k in item}
    value['pub_dt'] = item['pub_dt'].isoformat() if item.get('pub_dt') else None
    return value


def thaw(value, rule):
    item = dict(value, _rule=rule)
    item['pub_dt'] = datetime.fromisoformat(value['pub_dt']) if value.get('pub_dt') else None
    return item


def apply_wait(items, state, rules, at=None):
    """Call after primary suppression; waiting releases never enter seen baselines."""
    at = at or now()
    waiting = state.setdefault('fallback_waiting', {})
    active = {r['id']: r for r in rules if r.get('enabled', True) and r.get('group')
              and r.get('fallback_any_group', True)}
    primary = state.get('primary_episodes', {})
    # Configuration changes invalidate the old choice and its old clock.
    for key, entry in list(waiting.items()):
        rule = active.get(key.split('|', 1)[0])
        if not rule or entry['signature'] != signature(rule) or primary.get(key):
            del waiting[key]

    result = [i for i in items if not i.get('_fallback')]
    for item in items:
        if not item.get('_fallback') or item['_rid'] not in active:
            continue
        key = f"{item['_rid']}|{item.get('episode') or item['btih']}"
        if primary.get(key):
            continue
        rule = active[item['_rid']]
        entry = waiting.setdefault(key, {'first_seen': at.isoformat(), 'signature': signature(rule),
                                         'candidate': freeze(item)})
        if rank(item) < rank(thaw(entry['candidate'], rule)):
            entry['candidate'] = freeze(item)

    # Cached candidates survive even if they have fallen out of the RSS window.
    for key, entry in waiting.items():
        rule = active[key.split('|', 1)[0]]
        entry['ready'] = at >= datetime.fromisoformat(entry['first_seen']) + timedelta(days=WAIT_DAYS)
        if entry['ready']:
            item = thaw(entry['candidate'], rule)
            item['_fallback_first_seen'] = entry['first_seen']
            result.append(item)

    # A previously failed fallback must not beat a newly observed primary.
    pending = state.get('pending_notifications', {})
    for key, message in list(pending.items()):
        delivery = message.get('delivery') or {}
        if delivery.get('fallback'):
            episode_key = '|'.join(delivery['state_key'].split('|')[:2])
            if primary.get(episode_key):
                del pending[key]
    return result
