"""Conservative media classification and evidence-based episode identities."""
import re
from .parsing import norm, extract_episode, extract_season, RE_NOT_EPISODE

NON_VIDEO = re.compile(r'漫画|漫畫|コミック|\bmanga\b|\bcomic\b|小[说説說]|\bnovel\b|电子书|電子書|\bebook\b|\.(?:epub|cbz|cbr|pdf)\b', re.I)
BOOK_TAG = re.compile(r'[\[【(（]\s*(?:漫画|漫畫|コミック|manga|comic|小[说説說]|novel|ebook)\s*[\]】)）]|电子书|電子書|\.(?:epub|cbz|cbr|pdf)\b', re.I)
BOOK_VOLUME = re.compile(r'第?\s*\d+(?:\s*[-~～]\s*\d+)?\s*[巻卷]|\bvol(?:ume)?\.?\s*\d+', re.I)
VIDEO = re.compile(r'1080|720|2160|\b(?:WEB|WebRip|BD|BDRip|BluRay|HEVC|AVC|x26[45]|MP4|MKV)\b', re.I)
DUAL = re.compile(r'[\[【]\s*(\d{1,3})\s*[(（]\s*(\d{1,4})\s*[)）]')


def is_animation(item):
    title = item.get('title', '')
    if RE_NOT_EPISODE.search(title) or NON_VIDEO.search(item.get('category', '')) or BOOK_TAG.search(title):
        return False
    return not (BOOK_VOLUME.search(title) and not VIDEO.search(title))


def numbers(episode):
    match = re.fullmatch(r'(\d{1,4})(?:-(\d{1,4}))?', str(episode))
    if not match:
        return None
    lo, hi = int(match[1]), int(match[2] or match[1])
    return set(range(lo, hi + 1)) if lo <= hi and hi - lo <= 1000 else None


def covers(existing, target):
    wanted = numbers(target)
    if wanted is None:
        return target in existing
    covered = set()
    for ep in existing:
        covered.update(numbers(ep) or ())
    return wanted <= covered


def primary_covers(state, rid, ep):
    return covers([k.split('|', 1)[1] for k, v in state.get('primary_episodes', {}).items()
                   if v and k.startswith(rid + '|')], ep)


def evidence_map(items, rule):
    pairs = {}
    conflicts = set()
    for item in items:
        title = item.get('title', '')
        terms = rule.get('include_any') or rule.get('keywords') or []
        if terms and not any(norm(term) in norm(title) for term in terms):
            continue
        if not is_animation(item):
            continue
        if rule.get('season') and extract_season(title) != rule['season']:
            continue
        match = DUAL.search(title)
        if match:
            local, absolute = f'{int(match[1]):02d}', f'{int(match[2]):02d}'
            if absolute in pairs and pairs[absolute] != local:
                conflicts.add(absolute)
            pairs[absolute] = local
    return {k: v for k, v in pairs.items() if k not in conflicts}


def canonical_episode(item, rule, mapping):
    ep = item.get('episode') or extract_episode(item.get('title', ''))
    if DUAL.search(item.get('title', '')):
        return ep
    # Explicitly configured cumulative start; never guess a season has 12/24 episodes.
    start = rule.get('season_start_episode', 0)
    parts = ep.split('-')
    if start and all(p.isdigit() and int(p) >= start for p in parts):
        return '-'.join(f'{int(p) - start + 1:02d}' for p in parts)
    if all(p in mapping for p in parts):
        return '-'.join(mapping[p] for p in parts)
    return ep


def season_evidence(item, rule, mapping):
    """A season number, or None when it cannot safely be established."""
    explicit = extract_season(item.get('title', ''))
    if explicit is not None:
        return explicit
    # The stored episode may already be canonical; title retains the source number.
    ep = extract_episode(item.get('title', '')) or item.get('episode') or ''
    if ep in mapping or (rule.get('season_start_episode') and ep.isdigit()
                         and int(ep) >= rule['season_start_episode']):
        return rule.get('season')
    return None


def effective_floor(rule, mapping):
    minimum = rule.get('from_episode', 0)
    if not minimum:
        return 0
    normalized = canonical_episode({'episode': f'{minimum:02d}'}, rule, mapping)
    return int(normalized) if normalized.isdigit() else minimum


def merge_seen(left, right):
    def pair(value):
        return tuple(map(int, str(value).split(':')))
    a, b = pair(left), pair(right)
    return f'{min(a[0], b[0])}:{max(a[1], b[1])}'


def reconcile_state(state, rules, items):
    """Add canonical aliases while preserving every original history key."""
    maps = state.setdefault('episode_maps', {})
    for rule in rules:
        rid = rule['id']
        observed = [i for i in items if i.get('_rid') == rid]
        mapping = maps.setdefault(rid, {})
        mapping.update(evidence_map(observed, rule))
        for bucket in ('episodes', 'primary_episodes'):
            records = state.get(bucket, {})
            for key, value in list(records.items()):
                parts = key.split('|')
                if parts[0] != rid or len(parts) < 2:
                    continue
                ep = canonical_episode({'episode': parts[1]}, rule, mapping)
                alias = '|'.join([rid, ep, *parts[2:]])
                if alias != key:
                    records[alias] = merge_seen(records[alias], value) if bucket == 'episodes' and alias in records else value
        waiting = state.get('fallback_waiting', {})
        for key, entry in list(waiting.items()):
            if not key.startswith(rid + '|'):
                continue
            candidate = entry['candidate']
            ep = canonical_episode(candidate, rule, mapping)
            candidate['episode'] = ep
            alias = rid + '|' + (ep or candidate['btih'])
            if alias != key:
                if alias not in waiting:
                    waiting[alias] = entry
                else:
                    current = waiting[alias]
                    current['first_seen'] = min(current['first_seen'], entry['first_seen'])
                    quality = lambda v: (v.get('_tier', 0), -v.get('_rev', 1))
                    if quality(entry['candidate']) < quality(current['candidate']):
                        current['candidate'] = entry['candidate']
                del waiting[key]
        for item in observed:
            item['episode'] = canonical_episode(item, rule, mapping)
