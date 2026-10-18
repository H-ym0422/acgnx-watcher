"""Revalidate queued messages against current user choices before every send."""
from .release_identity import is_animation, numbers, primary_covers, canonical_episode, effective_floor, covers
from .fallback_wait import signature
from .release_identity import season_evidence
from .matching import match_rule, fallback_match


def deferred(message, state):
    delivery = message.get('delivery') or {}
    if not delivery.get('fallback'):
        return False
    rid, ep = delivery['state_key'].split('|')[:2]
    return covers(state.get('season_ambiguous', {}).get(rid, []), ep)


def allowed(message, rule, state):
    if not rule or not rule.get('enabled', True):
        return False
    source = message.get('source')
    if source and not is_animation(source):
        return False
    if source:
        source = dict(source)
        source.setdefault('author', '')
        source['_season_evidence'] = season_evidence(source, rule, state.get('episode_maps', {}).get(rule['id'], {}))
        primary = match_rule(source, rule)
        if not primary and not (rule.get('fallback_any_group', True) and fallback_match(source, rule)):
            return False
    elif message.get('rule_signature') and message['rule_signature'] != signature(rule):
        # An aggregate legacy/backfill notification cannot safely be split here.
        return False
    delivery = message.get('delivery') or {}
    if delivery:
        parts = delivery['state_key'].split('|')
        ep = canonical_episode({'episode': parts[1]}, rule, state.get('episode_maps', {}).get(rule['id'], {}))
        parts[1] = ep
        delivery['state_key'] = '|'.join(parts)
        covered = numbers(ep)
        if covered and max(covered) < effective_floor(rule, state.get('episode_maps', {}).get(rule['id'], {})):
            return False
        if delivery.get('fallback') and (not rule.get('fallback_any_group', True)
                                         or primary_covers(state, rule['id'], ep)):
            return False
    return True
