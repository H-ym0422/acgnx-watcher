"""Read-only resource lookup: collection only, never seed, queue or notify."""
from copy import deepcopy
from . import release_identity as identity
from .rules import normalized


def query(request, api):
    cfg = deepcopy(api.aw.load_config())
    state = api.aw.load_state()
    related = [r for r in cfg['rules'] if
               (normalized(request.term) in normalized(r.get('name', '')) or
                any(normalized(request.term) == normalized(x) for x in r.get('include_any', [])))
               and r.get('season', 0) == (request.season or 0)]
    existing = related[0] if len(related) == 1 else {}
    rule = {'id': existing.get('id', '__query__'), 'name': request.term,
            'keywords': [request.term], 'include_any': list(dict.fromkeys([request.term] + existing.get('include_any', []))),
            'exclude_any': [], 'group': request.groups, 'season': request.season or 0,
            'enabled': True, 'fallback_any_group': False,
            'variant_prefer': list(api.DEFAULT_PREFER),
            'allow_unmarked_season': existing.get('allow_unmarked_season', False),
            'season_start_episode': existing.get('season_start_episode', 0)}
    cfg['rules'] = [rule]
    cfg['_episode_maps'] = deepcopy(state.get('episode_maps', {}))
    cfg['settings']['watch_global_feed'] = False
    items = api.aw.collect(cfg)
    if not getattr(items, 'available', True):
        return {'ok': False, 'step': 'query', 'text': '资源源暂时不可用，请稍后重试。未修改订阅，未发送飞书通知。'}
    mapping = dict(cfg['_episode_maps'].get(rule['id'], {}))
    mapping.update(identity.evidence_map(items, rule))
    floor = identity.effective_floor(dict(rule, from_episode=request.floor or 0), mapping)
    end = identity.effective_floor(dict(rule, from_episode=request.end), mapping) if request.end else None
    chosen = {}
    for item in items:
        episode = identity.canonical_episode(item, rule, mapping)
        numbers = identity.numbers(episode)
        if (request.floor is not None or end is not None) and (not numbers or max(numbers) < floor or (end is not None and min(numbers) > end)):
            continue
        key = (api.aw.extract_season(item['title']), episode or item['btih'], api.aw.group_key(item, request.groups))
        quality = lambda v: (v.get('_tier', 0), -v.get('_rev', 1))
        if key not in chosen or quality(item) < quality(chosen[key]):
            chosen[key] = dict(item, episode=episode)
    rows = sorted(chosen.values(), key=lambda v: (api.ep_sort_key(v.get('episode', '')), api.aw.group_key(v, request.groups)))
    if request.backfill == 'latest' and rows:
        last = max(api.ep_sort_key(v.get('episode', '')) for v in rows)
        rows = [v for v in rows if api.ep_sort_key(v.get('episode', '')) == last]
    lines = ['本次仅查询，未新增订阅，未发送飞书通知。', f'当前 RSS 范围找到 {len(rows)} 条匹配资源。']
    if not rows:
        lines.append('暂未找到不代表字幕组停更；各组发布时间不同，稍后可以再查。')
    for item in rows[:20]:
        date = item.get('pub_dt')
        lines += ['', f"第 {item.get('episode') or '未标注'} 集 · {api.aw.group_key(item, request.groups)} · {date:%Y-%m-%d}" if date else f"第 {item.get('episode') or '未标注'} 集 · {api.aw.group_key(item, request.groups)}",
                  item['title'], item.get('magnet') or item.get('link') or '']
    if len(rows) > 20:
        lines.append('最多展示 20 条，请缩小集数范围。')
    if getattr(items, 'ambiguous', {}):
        lines.append('部分资源季度信息不足，已保守排除；请核对季度或累计集号。')
    if getattr(items, 'failed_sources', 0):
        lines.append('部分搜索源失败，结果可能不完整。')
    lines.append('RSS 仅覆盖近期资源；合集按范围交集返回，可能含范围外集数。')
    return {'ok': True, 'step': 'query', 'text': '\n'.join(lines)[:10000]}
