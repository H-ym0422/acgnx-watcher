"""Persist and deliver notifications using current rules and a shared budget."""
from __future__ import annotations

from .parsing import actual_group


def resource_receipt(item, state_key):
    return {'state_key': state_key, 'group': actual_group(item), 'fallback': bool(item.get('_fallback')),
            'episode_known': bool(item.get('episode')), 'variant': item.get('_variant') or ''}


def enqueue_notification(state: dict, rule_id: str, title: str, body: str, *, api) -> str:
    """Persist only message content, never credentials, until delivery succeeds."""
    key = api.hashlib.sha256((rule_id + '\x00' + title + '\x00' + body).encode()).hexdigest()
    state.setdefault('pending_notifications', {}).setdefault(key, {'rule_id': rule_id, 'title': title, 'body': body})
    return key


def deliver_pending(state: dict, settings: dict, only: str | None=None, rules: list[dict] | None=None, budget=None, *, api) -> int:
    pending = state.setdefault('pending_notifications', {})
    active = None if rules is None else {r['id']: r for r in rules}
    budget = budget or api.SendBudget()
    channel = settings.get('push_channel', 'auto')
    channels = api.available_channels(settings) if channel == 'auto' else [channel]
    request_seconds = sum(30 if ch == 'feishu_app' else 15 for ch in channels)
    required_seconds = 95 + max(15, request_seconds)
    sent = attempts = 0
    for (key, message) in list(pending.items()):
        if only is not None and key != only:
            continue
        if active is not None and message['rule_id'] != '__health__' and (not api.outbox_policy.allowed(message, active.get(message['rule_id']), state)):
            del pending[key]
            api.save_state(state)
            continue
        if api.outbox_policy.deferred(message, state):
            api.log.info('指定组资源季度待确认，暂缓该集兜底重试')
            continue
        if not budget.reserve(max(0, int(settings.get('push_min_interval_seconds', 13))), required_seconds):
            api.log.info('本轮发送预算已用完，剩余队列下轮继续')
            break
        attempts += 1
        api.save_state(state)
        budget.mark_attempt()
        try:
            (ok, _) = api.send_notification(message['title'], message['body'], settings)
        except Exception:
            api.log.exception('Queued notification failed; retained for next cycle')
            ok = False
        if ok:
            delivery = message.get('delivery') or message.get('summary_delivery')
            if delivery:
                if 'variant' not in delivery and message.get('source'):
                    delivery = resource_receipt(message['source'], delivery['state_key'])
                sent_at = api.health.now()
                state.setdefault('deliveries', {})[delivery['state_key']] = {
                    **{k: v for k, v in delivery.items() if k != 'state_key'}, 'time': sent_at}
                state.setdefault('health', {})['last_delivery'] = sent_at
            del pending[key]
            sent += 1
            api.save_state(state)
        else:
            api.log.warning('Queued notification retained for retry')
    return sent
