"""Search releases and build stable group-selection surveys and rules."""
from __future__ import annotations
from .subtitle_summary import subtitle_lines
from .rss_search import fetch_search_feed


def search(term: str, *, api) -> list[dict]:
    """搜一部番的所有已发布条目。

    ⚠️ **站点每次搜索最多只返回 50 条**，而且没有分页参数（试过 page/p/start/offset，
    全都还是那 50 条）。热门番十几个字幕组一叠加，50 条根本装不下 ——
    实测「与你相恋到生命尽头」只搜一次会漏掉 TSDM、喵萌的大部分资源。

    所以改成：**用几个关键词变体各搜一次，再按 btih 合并**。
    不同关键词命中的是不同排序/不同切片，合并后能翻出一倍多的结果。
    实测 50 → 100 条，覆盖明显更全。"""
    cfg = {'settings': {'request_timeout': 15, 'retry': 2, 'proxy': ''}, 'rules': []}
    (base, _) = api.aw.pick_base(cfg)
    if base is None:
        print('❌ 所有镜像都连不上，检查一下网络。')
        return []
    merged: dict[str, dict] = {}
    successful_queries = 0
    for kw in api._search_variants(term):
        body = fetch_search_feed(base, kw, api=api.aw)
        if body is None:
            continue
        successful_queries += 1
        for it in api.aw.parse_feed(body):
            merged.setdefault(it['btih'], it)
        if len(merged) >= 400:
            break
    if not successful_queries:
        raise RuntimeError('RSS 搜索接口不可用，无法查询资源或生成字幕组清单；这不代表没有资源，请稍后重试')
    return list(merged.values())


def survey_text(term: str, limit: int=20, season: int=0, snapshot_path: str | None=None, *, api) -> str | None:
    """生成「谁在做、覆盖哪些集、各是什么类型」的清单（Markdown），供 Issue 回复用。"""
    items = api.search(term)
    if not items:
        return None
    if season and int(season) > 0:
        items = [i for i in items if api.aw.season_ok(i['title'], int(season))]
        if not items:
            return f'没搜到「{term}」第 {season} 季的条目。\n\n搜索只覆盖最近发布的，老季一般得靠后来重发的 BD / 合集。试着把标题改成 `加番 {term} BD` 或者 `加番 {term} 合集` 再看看。'
    order = api.rank_groups(items)
    if not order:
        return None
    if snapshot_path:
        with open(snapshot_path, 'w', encoding='utf-8') as f:
            api.json.dump({'version': 1, 'term': term, 'season': season, 'groups': [g['name'] for g in order[:limit]]}, f, ensure_ascii=False)
    head = f'搜到 **{len(order)}** 个字幕组在做「{term}」'
    if season:
        head += f'第 {season} 季'
    out = [head + '。回复这条 Issue 告诉我要哪个：', '']
    for (i, g) in enumerate(order[:limit], 1):
        last = f"{g['last']:%m-%d}" if g['last'] else '  -  '
        out.append(f"`{i:>2}`  **{g['name']}**  ·  {g['n']} 条，最近 {last}")
        out.append(f"\u3000\u3000覆盖 {g['cover']}")
        out.append('\u3000\u3000字幕形式（按集数，可选版本）：')
        out.extend('\u3000\u3000' + line for line in subtitle_lines(g.get('releases', [])))
    if len(order) > limit:
        out.append(f'\u3000\u3000\u3000（还有 {len(order) - limit} 个，没列出来）')
    out += ['', '**回复格式**', '', '- 回复 `1` 或 `1,3` —— 用这几个字幕组', '- 回复 `绿茶字幕组` —— 直接写组名也行', '- 回复 `任意` —— 不挑组，谁先出就推谁']
    return '\n'.join(out)


def pick(term: str, want_all_groups: bool, forced_groups: list[str], extra_aliases: list[str] | None=None, auto_top: int=0, pick_indexes: list[int] | None=None, fallback: bool=True, season: int=0, allow_unmarked_season: bool=False, season_start_episode: int=0, *, api) -> dict | None:
    print(f'\n🔍 正在 ACGNX 搜索「{term}」...')
    items = api.search(term)
    if not items:
        print('⚠️  没搜到任何结果。可能是：')
        print('    · 番名写法和站上不一致（试试日文原名、罗马音，或只取其中 2~4 个字）')
        print('    · 站点这一轮恰好返回空（偶发，等一会儿重试）')
        return None
    if season and int(season) > 0:
        before = len(items)
        evidence_rule = {'season': int(season), 'season_start_episode': season_start_episode}
        mapping = api.aw.release_identity.evidence_map(items, evidence_rule)
        items = [i for i in items if api.aw.season_ok(i['title'], int(season), allow_unmarked_season) or api.aw.release_identity.season_evidence(i, evidence_rule, mapping) == int(season)]
        print(f'🎬 只要第 {season} 季：{before} 条 -> {len(items)} 条')
        if not items:
            print(f'⚠️  没搜到第 {season} 季的条目。')
            print('    搜索只覆盖最近发布，老季通常要靠后来重发的 BD / 合集。试试换个词：')
            print(f'      python add_rule.py --survey "{term} BD"')
            print(f'      python add_rule.py --survey "{term} 合集"')
            return None
    ds = [i['pub_dt'] for i in items if i.get('pub_dt')]
    span = f'，跨度 {min(ds):%Y-%m-%d} ~ {max(ds):%Y-%m-%d}' if ds else ''
    print(f'✅ 命中 {len(items)} 条{span}')
    order = api.rank_groups(items)
    if not order:
        print('⚠️  没识别出任何字幕组名，可能是条目太少。')
        return None
    print('\n📺 在做这部番的字幕组（含覆盖集数和字幕类型）：')
    for (idx, g) in enumerate(order, 1):
        last = f"{g['last']:%m-%d}" if g['last'] else '  -  '
        print(f"   [{idx:>2}] {g['name']:<26} {g['n']:>3} 条   最近 {last}")
        print(f"        覆盖 {g['cover']}")
        print('        字幕形式（按集数，可选版本）：')
        for line in subtitle_lines(g.get('releases', [])):
            print('        ' + line)
    selected: list[str] = []
    if pick_indexes is not None:
        for p in pick_indexes:
            if 1 <= p <= len(order):
                selected.append(order[p - 1]['name'])
        if selected:
            print(f"\n🤖 按你选的序号取到：{'、'.join(selected)}")
        else:
            print('⚠️  没识别到有效序号，改成自动选发布最多的前 2 个')
            auto_top = 2
    if not selected:
        if forced_groups:
            for f in forced_groups:
                matched = [g['name'] for g in order if api.aw.norm(f) in api.aw.norm(g['name'])]
                if matched:
                    selected.extend(matched)
                else:
                    selected.append(f)
                    print(f'   ⚠️  列表里没有「{f}」，仍按你写的原样加入规则')
        elif want_all_groups:
            selected = []
        elif auto_top > 0:
            selected = [g['name'] for g in order[:auto_top]]
            print(f"\n🤖 自动选组（前 {auto_top} 个发布最多的）：{'、'.join(selected)}")
        else:
            print('\n要盯哪几个字幕组？（多选用逗号，如 1,3）')
            print('   回车 = 全选      输入 any = 任何字幕组都推')
            try:
                raw = input('   你的选择: ').strip()
            except EOFError:
                raw = ''
                print('   （没有交互终端，按全选处理）')
            if raw.lower() in ('any', '*', '任意'):
                selected = []
            elif raw == '':
                selected = [g['name'] for g in order]
            else:
                for part in api.re.split('[,，\\s]+', raw):
                    if part.isdigit() and 1 <= int(part) <= len(order):
                        selected.append(order[int(part) - 1]['name'])
                if not selected:
                    print('   ⚠️  没识别到有效选择，按全选处理')
                    selected = [g['name'] for g in order]
    selected = list(dict.fromkeys(selected))
    pool = [i for i in items if not selected or any((api.aw.norm(s) in api.aw.norm(i['title']) or api.aw.norm(s) in api.aw.norm(i['author']) for s in selected))]
    if not pool:
        pool = items
    alias_count: dict[str, dict] = {}
    for i in pool:
        for a in api.extract_alias_names(i['title']):
            key = api.aw.norm(a)
            e = alias_count.setdefault(key, {'name': a, 'n': 0})
            e['n'] += 1
            if len(a) < len(e['name']):
                e['name'] = a
    threshold = max(2, int(len(pool) * 0.3))
    aliases = [e['name'] for e in alias_count.values() if e['n'] >= threshold]
    aliases.sort(key=lambda a: -alias_count[api.aw.norm(a)]['n'])
    aliases = list(dict.fromkeys([term] + (extra_aliases or []) + aliases))[:8]
    nt = api.aw.norm(term)
    hit = sum((1 for i in pool if any((api.aw.norm(a) in api.aw.norm(i['title']) for a in aliases))))
    print(f'\n🏷️  自动认出的译名（共 {len(aliases)} 个，覆盖选中组 {hit}/{len(pool)} 条标题）：')
    for a in aliases:
        print(f'     · {a}')
    rule = {'id': 'r-' + api.uuid.uuid4().hex, 'name': f"{term}（{('任意字幕组' if not selected else '/'.join(selected[:2]) + ('等' if len(selected) > 2 else ''))}）", 'enabled': True, 'keywords': [term], 'include_any': aliases, 'exclude_any': [], 'group': selected, 'variant_prefer': list(api.DEFAULT_PREFER), 'fallback_any_group': bool(fallback), 'note': ''}
    if season and int(season) > 0:
        rule['season'] = int(season)
        print(f'\n🎬 已设为「只收第 {season} 季」—— 标题里标了别的季号的会被丢掉')
    else:
        for kw in [term] + (extra_aliases or [])[:2]:
            sn = api.aw.extract_season(kw)
            if sn and sn > 1:
                rule['season'] = sn
                print(f'\n🎬 从番名里认出「第 {sn} 季」，已自动设为只收这一季（不想这样就重发 Issue，在「第几季」填 0）')
                break
    return rule
