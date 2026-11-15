from __future__ import annotations

import json

from .parsing import group_eq, norm
from .release_identity import covers


def group_selection(groups):
    return sorted({norm(group) for group in (groups or []) if norm(group)})


def signature_groups(signature):
    if not signature:
        return None
    try:
        groups = json.loads(signature).get('group')
    except (AttributeError, TypeError, ValueError):
        return None
    if groups is not None and (not isinstance(groups, list) or any(not isinstance(group, str) for group in groups)):
        return None
    return group_selection(groups)


def sync_group_selections(state, rules):
    previous = state.get('rule_group_selections', {})
    current = {rule['id']: group_selection(rule.get('group')) for rule in rules if rule.get('id')}
    changed = {rid for rid in previous.keys() & current.keys() if previous[rid] != current[rid]}
    legacy = current.keys() - previous.keys()

    primary = state.get('primary_episodes', {})
    history = {}
    if primary and legacy:
        for key in state.get('episodes', {}):
            parts = key.split('|', 2)
            if len(parts) == 3 and parts[0] in legacy:
                rid, episode, group = parts
                if any(group_eq(group, wanted) for wanted in current[rid]):
                    history.setdefault(rid, []).append(episode)

    for key in list(primary):
        rid, _, episode = key.partition('|')
        if rid in changed or (rid in legacy and current[rid] and not covers(history.get(rid, []), episode)):
            del primary[key]

    for key, entry in list(state.get('fallback_waiting', {}).items()):
        rid = key.split('|', 1)[0]
        if rid in changed or (rid in legacy and signature_groups(entry['signature']) != current[rid]):
            del state['fallback_waiting'][key]

    for key, message in list(state.get('pending_notifications', {}).items()):
        rid = message['rule_id']
        groups = signature_groups(message.get('rule_signature')) if rid in legacy else None
        if rid in changed or (groups is not None and groups != current[rid]):
            del state['pending_notifications'][key]

    state['rule_group_selections'] = current
