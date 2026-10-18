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
    exact = [r for r in cfg.get('rules', []) if r['id'] == selector]
    matches = exact or [r for r in cfg.get('rules', []) if selector in r.get('name', '')]
    if len(matches) != 1:
        raise ValueError('规则未找到或匹配多条，请使用列表中的唯一 ID')
    rule = matches[0]
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
