"""Seed historical baselines and queue policy-aware historical notifications."""
from __future__ import annotations


def inspect_and_seed(rule: dict, from_ep: int=0, backfill_mode: str | None=None, *, api) -> list[tuple[str, dict]]:
    """抓一遍，做两件事：
       ① 把这条规则当前命中的集数记进 state 当「已记录」基线 ——
          这样**之后只有新发布的集数才会提醒**；
       ② 返回按「第1集 → 最新集」排好的 [(集数, 最优版本条目), ...]，
          交给 notify_result 一次性推给用户。"""
    print('\n🧪 抓取一遍，看看这条规则现在能命中什么 ...')
    cfg = api.aw.load_config()
    probe = dict(cfg)
    probe['rules'] = [rule]
    probe['_episode_maps'] = api.aw.load_state().get('episode_maps', {})
    matched = api.aw.collect(probe)
    if not getattr(matched, 'available', True):
        raise RuntimeError('抓取失败，未建立初始化基线，请检查网络后重试')
    st = api.aw.load_state()
    api.aw.release_identity.reconcile_state(st, [rule], matched)
    st['season_ambiguous'] = getattr(matched, 'ambiguous', {})
    matched = api.aw.suppress_fallbacks(matched, st)
    observed = list(matched)
    policy_rules = [r for r in cfg.get('rules', []) if r['id'] != rule['id']] + [rule]
    matched = [i for i in api.aw.fallback_wait.apply_wait(matched, st, policy_rules) if i['_rid'] == rule['id']]
    want_g = [g for g in rule.get('group') or [] if not g.endswith('*')]
    api._want_groups = want_g
    buckets: dict[tuple, list] = {}
    for m in matched:
        ep = m.get('episode') or m['btih']
        gk = api.aw.group_key(m, want_g)
        buckets.setdefault((ep, gk), []).append(m)
    api.aw.note_unparsed([m for m in matched if not m.get('episode') and (not api.aw.RE_BATCH_HINT.search(m['title']))], st)
    for ((ep, gk), items) in buckets.items():
        items.sort(key=api.aw.sort_key)
        key = f"{rule['id']}|{ep}|{gk}" if gk else f"{rule['id']}|{ep}"
        st['episodes'].setdefault(key, items[0]['_seen'])
    newest = max((api.ep_sort_key(i.get('episode') or i['btih']) for i in observed), default=None)
    for item in observed:
        ep = item.get('episode') or item['btih']
        skip_history = backfill_mode in (None, 'none') or (backfill_mode == 'latest' and api.ep_sort_key(ep) != newest)
        if item.get('_fallback') and skip_history:
            gk = api.aw.group_key(item, want_g)
            key = f"{rule['id']}|{ep}|{gk}" if gk else f"{rule['id']}|{ep}"
            st['episodes'].setdefault(key, item['_seen'])
    st['initialized'] = True

    def finish(ordered):
        if backfill_mode is not None:
            (title, body, _settings) = api.format_result(rule, ordered, backfill_mode, from_ep)
            queued = api.aw.enqueue_notification(st, rule['id'], title, body)
            st['pending_notifications'][queued]['rule_signature'] = api.aw.fallback_wait.signature(rule)
        api.aw.save_state(st)
        return ordered
    if not matched:
        print('   （当前没有可推送条目；其他组资源按集等待 7 天）')
        return finish([])
    want_groups = [g for g in rule.get('group') or [] if not g.endswith('*')]
    api._want_groups = want_groups
    ordered: list[tuple[str, dict]] = []
    if len(want_groups) > 1:
        for ep in sorted({k[0] for k in buckets}, key=api.ep_sort_key):
            items_ep = [it for ((e, _g), lst) in buckets.items() if e == ep for it in lst]
            per_slot: dict[str, tuple[str, dict]] = {}
            for it in sorted(items_ep, key=api.aw.sort_key):
                g = api._group_of(it)
                slot = next((w for w in want_groups if api._same_group(g, w)), None) or (g or '__fb__')
                cur = per_slot.get(slot)
                if cur is None or api.aw.sort_key(it) < api.aw.sort_key(cur[1]):
                    per_slot[slot] = (ep, it)
            for w in want_groups:
                for (slot, v) in per_slot.items():
                    if slot == w or api._same_group(slot, w):
                        ordered.append(v)
                        break
            rest = [v for (slot, v) in per_slot.items() if not any((slot == w or api._same_group(slot, w) for w in want_groups))]
            ordered.extend(sorted(rest, key=lambda t: api.aw.sort_key(t[1])))
    else:
        for ep in sorted({k[0] for k in buckets}, key=api.ep_sort_key):
            items_ep = [it for ((e, _g), lst) in buckets.items() if e == ep for it in lst]
            ordered.append((ep, api.aw.best_of(items_ep)))
    ordered = api.aw.collapse_overlaps(ordered, api._group_of)
    ordered.sort(key=lambda t: (api.ep_sort_key(t[0]), api.aw.sort_key(t[1])))
    all_n = len(ordered)
    if from_ep > 0:
        effective = api.aw.release_identity.effective_floor(dict(rule, from_episode=from_ep), st.get('episode_maps', {}).get(rule['id'], {}))
        ordered = [(ep, it) for (ep, it) in ordered if api.ep_at_least(ep, effective)]
        skipped = all_n - len(ordered)
        print(f'\n📌 按你的要求，只回溯第 {from_ep} 集及以后 —— 跳过 {skipped} 条（第 1~{from_ep - 1} 集已记为看过，以后出修正版也不会再推你）')
    if not ordered:
        print(f'\n（要的第 {from_ep} 集及以后暂时还没有资源，等更新了会推你）')
        return finish([])
    print(f'   原始命中 {len(matched)} 条，归并成 {len(ordered)} 集（按集数从小到大）：')
    for (ep, item) in ordered:
        d = f"{item['pub_dt']:%m-%d}" if item.get('pub_dt') else '??'
        title = item['title'][:56] + '…' if len(item['title']) > 56 else item['title']
        print(f'   [{ep:>8}] {d}  {title}')
        print(f"              {item.get('magnet') or '（无磁力，见详情页）'}")
    print(f'\n📌 这 {len(ordered)} 个集数已记为基线 ——')
    print('   之后只有新发布的集数才会提醒。')
    return finish(ordered)


def notify_result(rule: dict, episodes: list[tuple[str, dict]], mode: str='all', from_ep: int=0, *, api) -> bool:
    (title, body, settings) = api.format_result(rule, episodes, mode, from_ep)
    return api.queue_result(rule, title, body, settings)


def format_result(rule: dict, episodes: list[tuple[str, dict]], mode: str='all', from_ep: int=0, *, api) -> tuple[str, str, dict]:
    """把加番结果推到手机。
    mode: all = 把第1集到最新集的磁力全部列出（默认）
          latest = 只推最新一集
          none = 都不推，只记基线"""
    cfg = api.aw.load_config()
    s = cfg['settings']
    prefix = s.get('title_prefix') or '【新番速递】'
    title = f"{prefix}已加入监听：{rule['name']}"[:120]
    lines = [f"字幕组：{(' / '.join(rule['group']) if rule['group'] else '任意字幕组')}"]
    if rule.get('include_any'):
        lines.append(f"标题需含：{'、'.join(rule['include_any'])}")
    if from_ep > 0:
        lines.append(f'只要第 {from_ep} 集及以后（前面的已记为看过，不再提醒）')
    if not episodes:
        lines.append('')
        lines.append('目前没有符合推送条件的资源；其他组资源会逐集等待 7 天，指定组发布后会优先提醒。')
        return (title, '\n'.join(lines), s)
    nums = [ep for (ep, _) in episodes if ep and ep != '-']
    if mode == 'none':
        lines += ['', f'已经发布的共 {len(episodes)} 集（按设置不推送），之后出新集会提醒。']
    elif mode == 'latest':
        (ep, item) = episodes[-1]
        lines += ['', f'最新一集（第 {ep} 集）：', item.get('magnet') or '（无磁力，见详情页）', '', f'（这部分共 {len(episodes)} 集，其余按设置未推送）']
    else:
        rng = f'第 {nums[0]} ~ {nums[-1]} 集' if nums else f'{len(episodes)} 集'
        all_nums: set[int] = set()
        span_ends: list[int] = []
        for (ep, _) in episodes:
            m2 = api.re.match('^(\\d{1,3})-(\\d{1,3})$', (ep or '').strip())
            if m2:
                span_ends.append(int(m2.group(2)))
            for x in api.ep_numbers([ep]):
                if x < 10 ** 6:
                    all_nums.add(x)
        if all_nums:
            season_max = max(span_ends) if span_ends else max(all_nums)
            got = len(all_nums)
            tail = f'（{min(all_nums):02d}~{max(all_nums):02d}）' if got > 1 else ''
            lines += ['', f'📺 目前检索到第 {season_max} 集，覆盖 {got} 个集号{tail}（不代表全季总集数）', f'下面共 {len(episodes)} 条磁力，按集数从小到大：', '']
        else:
            lines += ['', f'📺 共 {len(episodes)} 条磁力，按集数从小到大：', '']
        for (ep, item) in episodes:
            tag = f'[{ep}]'
            g = api._group_of(item)
            if g:
                tag += f' {g}'
            lines.append(f"{tag} {item.get('magnet') or '（无磁力，见详情页）'}")
        lines += ['', '———', '以上是历史集，已经记下来了。以后只推新发布的集数，不会再重复推这些。']
    return (title, '\n'.join(lines), s)


def queue_result(rule: dict, title: str, body: str, settings: dict, *, api) -> bool:
    state = api.aw.load_state()
    key = api.aw.enqueue_notification(state, rule['id'], title, body)
    state['pending_notifications'][key]['rule_signature'] = api.aw.fallback_wait.signature(rule)
    api.aw.save_state(state)
    delivered = bool(api.aw.deliver_pending(state, settings, only=key, rules=[rule]))
    api.aw.save_state(state)
    print('历史通知已送达' if delivered else '规则已保存；通知待重试，定时检测会自动补发')
    return delivered
