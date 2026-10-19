import re

from .parsing import extract_variant, norm


def variant_of(item):
    return (item.get('_variant') or extract_variant(item.get('title', ''))).strip() or '未标注，请查看发布说明'


def compact_episodes(episodes):
    spans = []
    unknown = set()
    for episode in episodes:
        value = str(episode or '').strip()
        match = re.fullmatch(r'(\d{1,4})(?:[-~～](\d{1,4}))?', value)
        if match and int(match[1]) <= int(match[2] or match[1]):
            spans.append((int(match[1]), int(match[2] or match[1])))
        else:
            unknown.add('集数未识别' if not value or re.fullmatch(r'[a-zA-Z0-9]{32,40}', value) else value)
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    labels = [f'{start:02d}' if start == end else f'{start:02d}~{end:02d}' for start, end in merged]
    return '、'.join(labels + sorted(unknown)) or '集数未识别'


def subtitle_lines(episodes, group_of=None):
    groups = {}
    for episode, item in episodes:
        group = (group_of(item) or '') if group_of else ''
        variant = variant_of(item)
        key = (norm(group), norm(variant))
        entry = groups.setdefault(key, {'episodes': [], 'variant': variant, 'group': group})
        entry['episodes'].append(episode)
    return [f"{compact_episodes(entry['episodes'])} {entry['variant']}" +
            (f"（{entry['group']}）" if entry['group'] else '') for entry in groups.values()]
