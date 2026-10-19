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
    out = ['# 我的追番列表', '', f"**已配置 {len(rules)} 部，其中 {sum((r.get('enabled', True) for r in rules))} 部监听中。** 自动生成，请通过规则管理修改。", '', f"兜底等待：{sum((not e.get('ready', False) for e in st.get('fallback_waiting', {}).values()))} 个集号（逐集等待 7 天）", f"最近检查：{st.get('health', {}).get('last_check', '尚无记录')}", f"抓取状态：{st.get('health', {}).get('status', '待检查')}；连续失败：{st.get('health', {}).get('failures', 0)} 次", f"最近成功推送：{st.get('health', {}).get('last_delivery', '尚无记录')}；待重试：{len(st.get('pending_notifications', {}))} 条", '', '| 番剧 / ID | 状态 | 字幕组 | 已记录到 | 集号数 | 最近推送 | 说明 |', '|---|---|---|---|---|---|---|']
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
        label = f'{name} / {rid}' + (f' · [删除]({url})' if url else '')
        out.append('| ' + ' | '.join(map(safe, [label, '监听中' if r.get('enabled', True) else '已暂停', grp, latest, str(len(eps)), delivery_text, '、'.join(notes) or '—'])) + ' |')
    out += ['', '去重基线不等于全部消息已经送达；集号数不是全季总集数。', '', '## 常用操作', '', '- 新建 Issue：`加番 番名`、`删番 规则ID`、`列表`。', '- 管理规则：`暂停 规则ID`、`恢复 规则ID`、`修改 规则ID`。', '- 修改时正文填写 `### 字幕组`、`### 第几季`、`### 只要第几集之后`；只写需要改的项。', '- 加番可选 `### 推哪些`：all / latest / none，默认 all。', '- 清单选组：回复原清单序号或完整组名；回复“刷新”重新获取。', '', '详细说明见 [使用说明](docs/usage.md)，异常恢复见 [恢复说明](docs/recovery.md)。', '']
    out += ['', '## 点击删除', '', '点击对应番剧行的“删除”，核对番名、季度和字幕组，勾选确认后提交。提交前不会删除。',
            '同名番剧可能有多条字幕组规则，每个入口只对应一个唯一规则 ID；规则变化后需刷新列表重新确认。',
            '本地生成删除入口时需设置 GITHUB_REPOSITORY=用户名/仓库名；GitHub Actions 自动提供该值。', '']
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
