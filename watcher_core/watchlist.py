"""Render rule health, listening status and delivery history."""
from __future__ import annotations
from .rules import display_name
from .delete_confirmation import delete_url


def render_watchlist(*, api) -> str:
    """把当前在追的番渲染成一份 Markdown 清单。返回正文。"""
    cfg = api.aw.load_config()
    st = api.aw.load_state()
    rules = cfg['rules']
    repository = api.os.environ.get('GITHUB_REPOSITORY', '')
    eps_map = st.get('episodes', {})
    out = ['# 我的追番列表', '', f"**已配置 {len({display_name(r) for r in rules})} 部番，共 {len(rules)} 条规则，其中 {sum((r.get('enabled', True) for r in rules))} 条监听中。**", '', f"兜底等待：{sum((not e.get('ready', False) for e in st.get('fallback_waiting', {}).values()))} 个集号（逐集等待 7 天）", f"最近检查：{st.get('health', {}).get('last_check', '尚无记录')}", f"抓取状态：{st.get('health', {}).get('status', '待检查')}；连续失败：{st.get('health', {}).get('failures', 0)} 次", f"最近成功推送：{st.get('health', {}).get('last_delivery', '尚无记录')}；待重试：{len(st.get('pending_notifications', {}))} 条", '', '| 番剧 | 状态 | 字幕组 | 已记录到 | 集号数 | 最近推送 | 说明 |', '|---|---|---|---|---|---|---|']
    for r in rules:
        rid = r.get('id') or ''
        eps: set[str] = set()
        for k in eps_map:
            if not k.startswith(rid + '|'):
                continue
            parts = k.split('|')
            for cand in (parts[1], parts[2] if len(parts) > 2 else ''):
                if cand and api.re.fullmatch('\\d{1,3}(?:-\\d{1,3})?', cand):
                    eps.add(cand)
                    break
        eps = sorted(eps, key=api.ep_sort_key)
        if eps:
            latest = f'第 {eps[-1]} 集' + ('（合集）' if '-' in eps[-1] else '')
        else:
            latest = '暂时没有'
        grp = ' / '.join(r.get('group') or []) or '任意字幕组'
        name = display_name(r)
        notes = []
        rule_status = st.get('rule_health', {}).get(rid, {})
        notes.append('抓取：' + rule_status.get('status', '待检查'))
        if rule_status.get('ambiguous_episodes'):
            notes.append('需确认季度：' + ','.join(rule_status['ambiguous_episodes'][:5]))
        if r.get('season'):
            notes.append(f"只收第 {r['season']} 季")
        if r.get('note'):
            notes.append(r['note'][:24])
        deliveries = [v for (k, v) in st.get('deliveries', {}).items() if k.startswith(rid + '|')]
        last = max(deliveries, key=lambda v: v['time']) if deliveries else None
        delivery_text = last['time'] + ' / ' + ('兜底' if last['fallback'] else '指定组/任意组') + (' / 集数待确认' if not last['episode_known'] else '') if last else '尚无记录'
        safe = lambda value: str(value).replace('|', '\\|').replace('\n', ' ')
        url = delete_url(r, repository)
        label = name + (f' · [删除]({url})' if url else '')
        out.append('| ' + ' | '.join(map(safe, [label, '监听中' if r.get('enabled', True) else '已暂停', grp, latest, str(len(eps)), delivery_text, '、'.join(notes) or '—'])) + ' |')
    out += ['', '去重基线不等于全部消息已经送达；集号数不是全季总集数。', '',
            '## 管理追番', '',
            '- 删除：点击对应行的“删除”，核对番名、季度和字幕组，勾选后提交。',
            '- 删除成功后该行自动移除，已完成的操作记录自动归档到已关闭 Issues。',
            '- 暂停或恢复：新建 Issue 写“帮我暂停番名”或“帮我恢复番名”。',
            '- 添加追番：新建 Issue 写“帮我追番名”，按清单选择字幕组。',
            '- 同一番剧可配置多个字幕组规则，每行只管理对应字幕组。',
            '', '详细说明见 [使用说明](docs/usage.md)。', '']
    return '\n'.join(out) + '\n'


def write_watchlist(*, api) -> str:
    """写出「我的番剧.md」，返回文件路径。内容没变就不重写。"""
    text = api.render_watchlist()
    path = api.os.path.join(api.os.path.dirname(api.os.path.abspath(api.aw.CONFIG_PATH)), api.WATCHLIST_NAME)
    old = ''
    if api.os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                old = f.read()
        except Exception:
            old = ''
    if old != text:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        return path
    return path
