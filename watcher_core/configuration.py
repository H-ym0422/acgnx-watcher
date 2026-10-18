"""Validate configuration before any network call or file mutation."""
import json
from pathlib import Path
import shutil


class ConfigError(ValueError):
    pass


def validate_config(cfg):
    if not isinstance(cfg, dict):
        raise ConfigError('config: 必须是对象')
    settings = cfg.get('settings', {})
    rules = cfg.get('rules', [])
    if not isinstance(settings, dict) or not isinstance(rules, list):
        raise ConfigError('settings 必须是对象，rules 必须是数组')
    ranges = {'interval_minutes': (1, 1440), 'request_timeout': (1, 300), 'retry': (1, 10),
              'push_min_interval_seconds': (0, 3600), 'poll_jitter_seconds': (0, 3600),
              'failure_alert_after': (1, 100)}
    for field, (lo, hi) in ranges.items():
        if field in settings and (type(settings[field]) is not int or not lo <= settings[field] <= hi):
            raise ConfigError(f'settings.{field}: 必须是 {lo}..{hi} 的整数')
    for field in ['watch_global_feed', 'skip_traditional_only']:
        if field in settings and type(settings[field]) is not bool:
            raise ConfigError(f'settings.{field}: 必须是布尔值')
    for field in ['proxy', 'title_prefix', 'webhook_url', 'pushplus_token', 'serverchan_sendkey',
                  'dingtalk_secret', 'feishu_secret', 'feishu_app_id', 'feishu_app_secret', 'feishu_chat_id']:
        if field in settings and not isinstance(settings[field], str):
            raise ConfigError(f'settings.{field}: 必须是字符串')
    if settings.get('first_run_mode', 'latest') not in ('latest', 'all', 'silent'):
        raise ConfigError('settings.first_run_mode: 仅支持 latest/all/silent')
    if settings.get('push_channel', 'auto') not in ('auto', 'webhook', 'pushplus', 'serverchan', 'feishu_app'):
        raise ConfigError('settings.push_channel: 不支持的推送渠道')
    ids = set()
    for i, rule in enumerate(rules):
        prefix = f'rules[{i}]'
        if not isinstance(rule, dict):
            raise ConfigError(f'{prefix}: 必须是对象')
        rid = rule.get('id')
        if not isinstance(rid, str) or not rid.strip() or '|' in rid:
            raise ConfigError(f'{prefix}.id: 必须是非空字符串且不能包含 |')
        if rid in ids:
            raise ConfigError(f'{prefix}.id: 重复 ID {rid}')
        ids.add(rid)
        for field in ['name', 'note']:
            if field in rule and not isinstance(rule[field], str):
                raise ConfigError(f'{prefix}.{field}: 必须是字符串')
        for field in ['keywords', 'include_any', 'exclude_any', 'group', 'variant_prefer']:
            value = rule.get(field, [])
            if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
                raise ConfigError(f'{prefix}.{field}: 必须是非空字符串组成的数组')
            if field == 'group' and any('|' in x for x in value):
                raise ConfigError(f'{prefix}.group: 组名不能包含 |')
        for field in ['season', 'from_episode']:
            value = rule.get(field, 0)
            if type(value) is not int or not 0 <= value <= 9999:
                raise ConfigError(f'{prefix}.{field}: 必须是 0..9999 的整数')
        for field in ['enabled', 'fallback_any_group']:
            if field in rule and type(rule[field]) is not bool:
                raise ConfigError(f'{prefix}.{field}: 必须是布尔值')
    return cfg


def write_config(path, cfg):
    validate_config(cfg)
    path = Path(path)
    if path.exists():
        shutil.copyfile(path, str(path) + '.bak')
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)
