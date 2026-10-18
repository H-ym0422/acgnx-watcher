"""Parse and execute rule-management commands using shared domain policies."""
from __future__ import annotations


def main(*, api) -> None:
    api.aw.setup_stdout()
    api.logging.basicConfig(level=api.logging.INFO, format='[%(levelname)s] %(message)s')
    ap = api.argparse.ArgumentParser(description='ACGNX 监听器 —— 加新番向导')
    ap.add_argument('term', nargs='?', help='番名（越有辨识度越好）')
    ap.add_argument('--group', action='append', default=[], help='限定字幕组，可重复')
    ap.add_argument('--also', action='append', default=[], help='手动补充番名的其它写法')
    ap.add_argument('--any-group', action='store_true', help='任意字幕组都推')
    ap.add_argument('--auto-group', action='store_true', help='不问，自动选发布最多的前 2 个字幕组（无人值守用）')
    ap.add_argument('--survey', action='store_true', help='只列出有哪些字幕组在做、各发过什么类型，不改配置（Issue 第一步用）')
    ap.add_argument('--pick', metavar='序号', help='按 --survey 里的序号挑字幕组，例如 1,3')
    ap.add_argument('--notify', action='store_true', help='把加番结果推到手机（云端用，含最新一集磁力）')
    ap.add_argument('--backfill', choices=['all', 'latest', 'none'], default='all', help='加番时把已发布的集数推到什么程度：all=第1集到最新集全部给你（默认）；latest=只给最新一集；none=都不推')
    ap.add_argument('--from-episode', type=int, default=0, metavar='N', help='只要第 N 集及以后。前面那些会记成「已看过」，既不推给你，以后出修正版也不会再推。0=不限制（默认）')
    ap.add_argument('--list', action='store_true', help='列出已有规则')
    ap.add_argument('--render', action='store_true', help=f'生成/刷新「{api.WATCHLIST_NAME}」并打印（手机上直接看这个文件）')
    ap.add_argument('--no-fallback', action='store_true', help='只推指定字幕组；默认会用别的组补上他们没做的集数')
    ap.add_argument('--season', type=int, default=0, metavar='N', help='多季番只想看第 N 季时用，例如 --season 1')
    ap.add_argument('--parse-report', action='store_true', help='看看有哪些标题的集数还没认出来（补正则用）')
    ap.add_argument('--exists', metavar='关键词', help='只检查这部番是不是已经在追了，在追就退出码 0、否则 1')
    ap.add_argument('--del', dest='delete', type=int, metavar='N', help='删除第 N 条规则')
    ap.add_argument('--del-name', metavar='关键词', help='删除名字里包含该关键词的规则（手机上传个词就能删，不用记序号）')
    ap.add_argument('--yes', action='store_true', help='跳过确认直接写入')
    ap.add_argument('--no-push', action='store_true', help='就算在 git 仓库里也不自动推送')
    ap.add_argument('--season-start-episode', type=int, default=0, help='本季第 1 集对应的累计集号，0 表示未指定')
    ap.add_argument('--allow-unmarked-season', action='store_true', help='明确允许未标季度资源')
    ap.add_argument('--strict-season', action='store_true', help='关闭未标季度的显式放行')
    ap.add_argument('--pause', metavar='ID', help='暂停规则')
    ap.add_argument('--resume', metavar='ID', help='恢复规则')
    ap.add_argument('--edit', metavar='ID', help='修改规则，保留 ID 和历史')
    ap.add_argument('--validate', action='store_true', help='只验证配置')
    ap.add_argument('--snapshot-out', help='保存选组清单快照')
    ap.add_argument('--snapshot-file', help='按序号选择时读取原清单快照')
    args = ap.parse_args()
    if args.validate:
        api.aw.load_config()
        print('配置验证通过')
        return
    selector = args.pause or args.resume or args.edit
    if selector:
        changes = {}
        if args.pause or args.resume:
            changes['enabled'] = bool(args.resume)
        if args.edit:
            if args.group or args.any_group:
                changes['group'] = [] if args.any_group else args.group
            if '--from-episode' in api.sys.argv:
                changes['from_episode'] = args.from_episode
            if '--season' in api.sys.argv:
                changes['season'] = args.season
            if '--no-fallback' in api.sys.argv:
                changes['fallback_any_group'] = False
            if '--season-start-episode' in api.sys.argv:
                changes['season_start_episode'] = args.season_start_episode
            if args.allow_unmarked_season or args.strict_season:
                changes['allow_unmarked_season'] = bool(args.allow_unmarked_season and (not args.strict_season))
            if not changes:
                raise ValueError('请指定要修改的字幕组、季号或起始集数')
        semantic_change = bool(set(changes) - {'enabled', 'name', 'note'})
        (rid, identity_changed) = api.rule_store.edit(api.aw.CONFIG_PATH, selector, changes)
        st = api.aw.load_state()
        pending = st.get('pending_notifications', {})
        st['pending_notifications'] = {k: v for (k, v) in pending.items() if v.get('rule_id') != rid or not (identity_changed or args.pause)}
        current_rule = next((r for r in api.aw.load_config()['rules'] if r['id'] == rid))
        st['pending_notifications'] = {k: v for (k, v) in st['pending_notifications'].items() if v.get('rule_id') != rid or api.aw.outbox_policy.allowed(v, current_rule, st)}
        if semantic_change or args.pause:
            st['fallback_waiting'] = {k: v for (k, v) in st.get('fallback_waiting', {}).items() if not k.startswith(rid + '|')}
        if identity_changed:
            st['primary_episodes'] = {k: v for (k, v) in st.get('primary_episodes', {}).items() if not k.startswith(rid + '|')}
        api.aw.save_state(st)
        api.write_watchlist()
        api.maybe_push(selector, not args.no_push)
        print(f'已更新规则 {rid}，历史去重记录保留')
        return
    if args.exists:
        cfg = api.aw.load_config()
        hit = [r.get('name') for r in cfg['rules'] if args.exists in ' '.join(r.get('keywords') or [])]
        if hit:
            print('已在追：' + '、'.join((str(h) for h in hit)))
            raise SystemExit(0)
        raise SystemExit(1)
    if args.survey:
        term = args.term or input('哪部番？（2~4 个最有辨识力的字）\n> ').strip()
        if not term:
            print('没输入番名。')
            return
        txt = api.survey_text(term, season=args.season, snapshot_path=args.snapshot_out)
        print(txt if txt else f'没搜到「{term}」。试试日文原名、罗马音，或者只取其中 2~4 个字。')
        return
    if args.list:
        cfg = api.aw.load_config()
        st = api.aw.load_state()
        rules = cfg['rules']
        print(f'\n当前已配置 {len(rules)} 部：\n' + '-' * 70)
        for (i, r) in enumerate(rules, 1):
            rid = r.get('id') or ''
            eps = sorted((k.split('|', 1)[1] for k in st.get('episodes', {}) if k.startswith(rid + '|')), key=api.ep_sort_key)
            done = f'已记录到第 {eps[-1]} 集（共 {len(eps)} 集）' if eps else '还没记录'
            print(f"[{i}] {r.get('name', '?')} | ID={rid} | 状态={('监听中' if r.get('enabled', True) else '已暂停')}")
            print(f"    字幕组={r.get('group') or '任意'}  关键词={r.get('keywords')}  {done}")
        print('-' * 70)
        print(f'（完整清单：跑 --render，或看仓库里的「{api.WATCHLIST_NAME}」）')
        return
    if args.render:
        text = api.render_watchlist()
        path = api.write_watchlist()
        print(text, end='')
        api.log.info('清单已写入 %s', path)
        api.maybe_push('watchlist', not args.no_push)
        return
    if args.parse_report:
        st = api.aw.load_state()
        box = st.get('unparsed') or {}
        if not box:
            print('最近没有解析不出集数的标题 —— 当前规则覆盖得不错 ✅')
            return
        rows = sorted(box.items(), key=lambda kv: kv[1].get('last', ''), reverse=True)
        print(f'有 {len(rows)} 个条目的集数还没认出来（最新在前）：\n')
        for (btih, e) in rows:
            print(f"  出现 {e.get('n', 1):>3} 次  最近 {e.get('last', '?')}  [{e.get('group', '?')}]")
            print(f"      {e.get('title', '')}")
        print('\n这些条目的磁力还是能正常用，只是推送里会写「整季打包，没有单一集数」。')
        print('看到新写法就告诉我，补一条正则就能认出来。')
        return
    if args.del_name:
        with open(api.aw.CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = api.json.load(f)
        rules = cfg.get('rules', [])
        hit = [i for (i, r) in enumerate(rules) if args.del_name == r.get('id')]
        if not hit:
            hit = [i for (i, r) in enumerate(rules) if args.del_name in (r.get('name') or '')]
        if len(hit) > 1:
            raise ValueError('匹配多条规则，未删除；请使用列表中的唯一规则 ID')
        if not hit:
            print(f'没找到名字包含「{args.del_name}」的规则。当前规则：')
            for (i, r) in enumerate(rules, 1):
                print(f"  [{i}] {r.get('name')}")
            raise SystemExit(2)
        api.shutil.copyfile(api.aw.CONFIG_PATH, api.aw.CONFIG_PATH + '.bak')
        removed_ids = []
        for i in reversed(hit):
            print('已删除：', rules[i].get('name'))
            removed_ids.append(rules[i].get('id') or '')
            rules.pop(i)
        with open(api.aw.CONFIG_PATH, 'w', encoding='utf-8') as f:
            api.json.dump(cfg, f, ensure_ascii=False, indent=2)
        n = api.purge_baseline(removed_ids)
        if n:
            print(f'   （顺带清掉 {n} 条去重基线，不然重新加回来不会推送回溯）')
        api.write_watchlist()
        api.maybe_push(args.del_name, not args.no_push)
        print(f'\n✅ 删掉 {len(hit)} 条规则。')
        return
    if args.delete:
        with open(api.aw.CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = api.json.load(f)
        rules = cfg.get('rules', [])
        if not 1 <= args.delete <= len(rules):
            print('序号超出范围。')
            return
        gone = rules.pop(args.delete - 1)
        api.shutil.copyfile(api.aw.CONFIG_PATH, api.aw.CONFIG_PATH + '.bak')
        with open(api.aw.CONFIG_PATH, 'w', encoding='utf-8') as f:
            api.json.dump(cfg, f, ensure_ascii=False, indent=2)
        print(f"已删除规则：{gone.get('name')}")
        n = api.purge_baseline([gone.get('id') or ''])
        if n:
            print(f'   （顺带清掉 {n} 条去重基线）')
        api.write_watchlist()
        api.maybe_push(gone.get('name') or 'rule', not args.no_push)
        return
    term = args.term or input('想看哪部番？（输番名里最有辨识度的 2~4 个字）\n> ').strip()
    if not term:
        print('没输入番名，退出。')
        return
    pick_indexes = None
    if args.pick:
        pick_indexes = [int(x) for x in api.re.split('[,，、\\s]+', args.pick) if x.isdigit()]
        args.group = api.resolve_picks(term, pick_indexes, args.season, args.snapshot_file)
        pick_indexes = None
    rule = api.pick(term, args.any_group, args.group, args.also, auto_top=2 if args.auto_group else 0, pick_indexes=pick_indexes, fallback=not args.no_fallback, season=args.season, allow_unmarked_season=args.allow_unmarked_season, season_start_episode=args.season_start_episode)
    if rule is None:
        if args.notify:
            s = api.aw.load_config()['settings']
            api.aw.send_notification(f"{s.get('title_prefix') or '【新番速递】'}加番失败：{term}"[:120], '没在 ACGNX 搜到这部番。\n\n可能的原因：\n· 番名写法跟站上不一致 —— 试试日文原名、罗马音，或只取其中 2~4 个字\n· 站点这一轮恰好返回空结果（偶发，过几分钟重跑一次即可）', s)
        raise SystemExit(2)
    print('\n即将加入的规则：')
    print(api.json.dumps(rule, ensure_ascii=False, indent=2))
    if not args.yes:
        if input('\n确认写入 config.json？(y/N) ').strip().lower() not in ('y', 'yes'):
            print('已取消。')
            return
    if args.season_start_episode:
        rule['season_start_episode'] = args.season_start_episode
    if args.allow_unmarked_season:
        rule['allow_unmarked_season'] = True
    if args.from_episode > 0:
        rule['from_episode'] = args.from_episode
    if not api.append_rule(rule):
        return
    episodes = api.inspect_and_seed(rule, from_ep=args.from_episode, backfill_mode=args.backfill if args.notify else None)
    api.write_watchlist()
    if args.notify:
        api.notify_result(rule, episodes, args.backfill, from_ep=args.from_episode)
    api.maybe_push(term, not args.no_push)
    print('\n🎉 完成。想微调就编辑 config.json 里这条规则（字段含义见 README.md）。')
