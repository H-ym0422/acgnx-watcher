"""Stable IDs and identical duplicate checks for every entry point."""
import json
from pathlib import Path
import re
import unicodedata
import uuid

from .configuration import write_config
from .parsing import extract_season


def normalized(value):
    return re.sub(r'[\s\W_]+', '', unicodedata.normalize('NFKC', value).casefold())


def display_name(rule):
    name = rule.get('name') or '?'
    groups = rule.get('group') or []
    group_label = ('/'.join(groups[:2]) + ('等' if len(groups) > 2 else '')) if groups else '任意字幕组'
    suffix = f'（{group_label}）'
    if name.endswith(suffix):
        name = name[:-len(suffix)]
    season = rule.get('season', 0)
    if season and extract_season(name) != season:
        digit = {1: '一', 2: '二', 3: '三', 4: '四', 5: '五', 6: '六', 7: '七', 8: '八', 9: '九', 10: '十'}
        name += f' 第{digit.get(season, str(season))}季'
    return name


def target_label(rule):
    return display_name(rule) + '（' + (' / '.join(rule.get('group') or []) or '任意字幕组') + '）'


def select_rule(rules, selector, season=None):
    exact = [r for r in rules if r['id'] == selector]
    matches = exact or [r for r in rules if normalized(selector) and
                        normalized(selector) in normalized(target_label(r))]
    if season is not None:
        matches = [r for r in matches if (r.get('season') or extract_season(r.get('name', '')) or 0) == season]
    if not matches:
        raise ValueError('没有找到对应追番，请从“我的番剧.md”点击删除，或使用列表中的唯一规则 ID')
    if len(matches) != 1:
        choices = '\n'.join(target_label(r) + ' / ' + r['id'] for r in matches)
        raise ValueError('匹配到多条规则，未修改；请从“我的番剧.md”选择唯一 ID 对应的行：\n' + choices)
    return matches[0]


def identity(rule):
    names = rule.get('keywords') or rule.get('include_any') or [rule.get('name', '')]
    season = rule.get('season', 0)
    def canonical(value):
        if season and extract_season(value) == season:
            value = re.sub(r'(?:第\s*[0-9零〇一二两三四五六七八九十百千]+\s*[季期]|\bS0*' + str(season) + r')\s*$', '', value, flags=re.I)
        return normalized(value)
    return (tuple(sorted({canonical(x) for x in names})),
            rule.get('season', 0), tuple(sorted({normalized(x) for x in rule.get('group', [])})))


def append(path, rule):
    cfg = json.loads(Path(path).read_text(encoding='utf-8'))
    if any(identity(existing) == identity(rule) for existing in cfg.get('rules', [])):
        return False
    rule.setdefault('id', 'r-' + uuid.uuid4().hex)
    cfg.setdefault('rules', []).append(rule)
    write_config(path, cfg)
    return True


def edit(path, selector, changes):
    cfg = json.loads(Path(path).read_text(encoding='utf-8'))
    rule = select_rule(cfg.get('rules', []), selector)
    original = identity(rule)
    old_groups = rule.get('group') or []
    old_label = ('/'.join(old_groups[:2]) + ('等' if len(old_groups) > 2 else '')) if old_groups else '任意字幕组'
    suffix = f'（{old_label}）'
    generated_name = rule.get('name', '').endswith(suffix)
    rule.update(changes)
    if 'group' in changes and 'name' not in changes and generated_name:
        groups = rule.get('group') or []
        label = ('/'.join(groups[:2]) + ('等' if len(groups) > 2 else '')) if groups else '任意字幕组'
        rule['name'] = rule['name'][:-len(suffix)] + f'（{label}）'
    if any(r is not rule and identity(r) == identity(rule) for r in cfg['rules']):
        raise ValueError('修改后与另一条规则重复，未保存')
    write_config(path, cfg)
    return rule['id'], original != identity(rule)
