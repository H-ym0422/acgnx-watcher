"""A persisted, per-episode grace period for releases from other groups."""
from datetime import datetime, timedelta, timezone
import json
from .release_identity import is_animation, primary_covers, covers

WAIT_DAYS = 7


def now():
    return datetime.now(timezone.utc)


def signature(rule):
    fields = ('keywords', 'group', 'season', 'include_any', 'exclude_any', 'from_episode',
              'allow_unmarked_season', 'variant_prefer', 'fallback_any_group')
    value = {k: rule.get(k) for k in fields}
    if rule.get('season_start_episode'):
        value['season_start_episode'] = rule['season_start_episode']
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def rank(item):
    date = item.get('pub_dt')
    return (item.get('_tier', 0), -item.get('_rev', 1), -(date.timestamp() if date else 0))


def freeze(item):
    fields = ('title', 'author', 'category', 'episode', 'btih', 'magnet', 'link', 'size',
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
        rid, ep = key.split('|', 1)
        if not rule or entry['signature'] != signature(rule) or primary_covers(state, rid, ep) or not is_animation(entry['candidate']):
            del waiting[key]

    result = [i for i in items if not i.get('_fallback') and is_animation(i)]
    for item in items:
        if not item.get('_fallback') or item['_rid'] not in active or not is_animation(item):
            continue
        key = f"{item['_rid']}|{item.get('episode') or item['btih']}"
        if primary_covers(state, item['_rid'], item.get('episode') or item['btih']):
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
        ambiguous = state.get('season_ambiguous', {}).get(key.split('|', 1)[0], [])
        if entry['ready'] and not covers(ambiguous, key.split('|', 1)[1]):
            item = thaw(entry['candidate'], rule)
            item['_fallback_first_seen'] = entry['first_seen']
            result.append(item)

    # A previously failed fallback must not beat a newly observed primary.
    pending = state.get('pending_notifications', {})
    for key, message in list(pending.items()):
        delivery = message.get('delivery') or {}
        if delivery.get('fallback'):
            episode_key = '|'.join(delivery['state_key'].split('|')[:2])
            rid, ep = episode_key.split('|', 1)
            if primary_covers(state, rid, ep) or rid not in active:
                del pending[key]
    return result
