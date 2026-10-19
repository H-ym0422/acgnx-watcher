"""Translate parsed requests to the existing command path after validation."""
from copy import deepcopy
from .rules import normalized, select_rule
from .resource_query import query


def merge_body(request, body, api):
    data = api.fields(body)
    group, season, floor, backfill = api.options(body if data else '')
    for prefix, value, parsed in (
        ('第几季', season, request.season),
        ('只要第几集之后', floor, request.floor),
        ('推哪些', backfill, request.backfill),
    ):
        if parsed is not None and any(k.startswith(prefix) and v for k, v in data.items()) and value != parsed:
            raise ValueError('标题与正文条件冲突，未修改规则')
    if (request.groups or request.any_group) and group:
        body_groups = [] if group.lower() in ('任意', 'any', '*') else [x.strip() for x in api.re.split('[,，、]', group)]
        if {normalized(x) for x in body_groups} != {normalized(x) for x in request.groups}:
            raise ValueError('标题与正文字幕组冲突，未修改规则')
    values = dict(data)
    updates = [('第几季', request.season), ('只要第几集之后', request.floor),
               ('推哪些', request.backfill),
               ('字幕组', '任意' if request.any_group else ('、'.join(request.groups) or None)),
               ('兜底', '关闭' if request.strict_group else None)]
    for key, value in updates:
        if value is not None:
            for existing in list(values):
                if existing.startswith(key):
                    del values[existing]
            values[key] = str(value)
    return '\n\n'.join(f'### {k}\n{v}' for k, v in values.items())


def resolve_selector(request, api):
    rules = api.ar.aw.load_config()['rules']
    exact = any(r['id'] == request.term for r in rules)
    season = None if (exact and request.mode == 'edit') else request.season
    return select_rule(rules, request.term, season)['id']


def handle(request, event, event_name, api):
    if request.mode == 'search':
        # Queries have no form fields: avoid silently ignoring competing conditions.
        if any(v for k, v in api.fields(event['issue'].get('body') or '').items() if not k.startswith('补充说明')):
            raise ValueError('自然语言查询请把条件完整写在标题，正文留空')
        result = query(request, api.ar)
    else:
        if event_name == 'issue_comment' and request.mode in ('del', 'pause', 'resume', 'edit'):
            return {'ok': True, 'step': 'done', 'text': '管理操作仅在新 Issue 执行，未重复修改。'}
        body = merge_body(request, event['issue'].get('body') or '', api)
        term = resolve_selector(request, api) if request.mode in ('del', 'pause', 'resume', 'edit') else request.term
        commands = dict(add='加番', pause='暂停', resume='恢复', edit='修改', **{'del': '删番', 'list': '列表'})
        translated = deepcopy(event)
        translated['issue'].update(title=commands[request.mode] + ' ' + term, body=body)
        result = api.handle_legacy(translated, event_name)
    result['text'] = request.summary() + '\n\n' + result['text']
    return result
