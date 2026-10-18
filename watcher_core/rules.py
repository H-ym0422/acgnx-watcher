"""Stable IDs and identical duplicate checks for every entry point."""
import json
from pathlib import Path
import re
import unicodedata
import uuid

from .configuration import write_config


def normalized(value):
    return re.sub(r'[\s\W_]+', '', unicodedata.normalize('NFKC', value).casefold())


def identity(rule):
    names = rule.get('keywords') or rule.get('include_any') or [rule.get('name', '')]
    return (tuple(sorted({normalized(x) for x in names})),
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
    rule.update(changes)
    if any(r is not rule and identity(r) == identity(rule) for r in cfg['rules']):
        raise ValueError('修改后与另一条规则重复，未保存')
    write_config(path, cfg)
    return rule['id'], original != identity(rule)
