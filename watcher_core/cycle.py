"""Coordinate one collection, selection, outbox and state cycle."""
from __future__ import annotations


def run_cycle(cfg: dict, state: dict, dry_run: bool=False, *, api) -> int:
    settings = cfg['settings']
    prefix = settings.get('title_prefix') or '【新番速递】'
    first_run = not state.get('initialized')
    mode = (settings.get('first_run_mode') or 'latest').lower()
    budget = api.SendBudget(settings.get('max_pushes_per_cycle', 12), settings.get('send_budget_seconds', 240))
    pending_sent = 0
    probe = dict(cfg, _episode_maps=state.get('episode_maps', {}))
    matched = api.collect(probe)
    if not dry_run:
        transition = api.health.update(state, matched, settings.get('failure_alert_after', 3))
        if transition:
            api.enqueue_notification(state, '__health__', prefix + transition[0], transition[1])
        for (rid, title, body) in api.health.update_rules(state, matched, cfg.get('rules', []), settings.get('failure_alert_after', 3)):
            api.enqueue_notification(state, '__health__', prefix + title, body)
        if not getattr(matched, 'available', True):
            pending_sent += api.deliver_pending(state, settings, rules=cfg.get('rules'), budget=budget)
            api.save_state(state)
            api.log.error('本轮抓取失败，状态基线未推进')
            return pending_sent
    policy_state = api.copy.deepcopy(state) if dry_run else state
    policy_state['season_ambiguous'] = getattr(matched, 'ambiguous', {})
    api.release_identity.reconcile_state(policy_state, cfg.get('rules', []), matched)
    matched = api.suppress_fallbacks(matched, policy_state)
    matched = api.fallback_wait.apply_wait(matched, policy_state, cfg.get('rules', []))
    api.log.info('兜底等待期内 %d 个集号（逐集等待 7 天）', sum((not e.get('ready', False) for e in policy_state.get('fallback_waiting', {}).values())))
    if not dry_run:
        pending_sent += api.deliver_pending(state, settings, rules=cfg.get('rules'), budget=budget)
    if not dry_run:
        api.note_unparsed([m for m in matched if not m.get('episode') and (not api.RE_BATCH_HINT.search(m['title']))], state)
    api.log.info('本轮共命中 %d 条（去重后）', len(matched))
    groups: dict[tuple, list[dict]] = {}
    for m in matched:
        ep = m.get('episode') or m['btih']
        rule_groups = [g for g in m['_rule'].get('group') or [] if not g.endswith('*')]
        gk = api.group_key(m, rule_groups)
        groups.setdefault((m['_rid'], ep, gk), []).append(m)

    def date_key(m: dict) -> float:
        return -(m['pub_dt'].timestamp() if m.get('pub_dt') else 0)

    def ep_num(ep: str) -> int:
        mm = api.re.match('^(\\d+)', ep or '')
        return int(mm.group(1)) if mm else 0
    pushes: list[tuple[dict, bool, str]] = []
    if first_run and mode != 'all':
        per_rule: dict[str, tuple[int, float, dict]] = {}
        for ((rid, ep, _gk), items) in groups.items():
            items.sort(key=api.sort_key)
            best = items[0]
            newest_ts = max((i['pub_dt'].timestamp() if i.get('pub_dt') else 0 for i in items))
            key = (ep_num(ep), newest_ts)
            cur = per_rule.get(rid)
            if cur is None or key > (cur[0], cur[1]):
                per_rule[rid] = (key[0], key[1], best)
        if mode != 'silent':
            for (rid, v) in per_rule.items():
                best = v[2]
                ep = best.get('episode') or best['btih']
                wanted = [g for g in best['_rule'].get('group') or [] if not g.endswith('*')]
                gk = api.group_key(best, wanted)
                pushes.append((best, False, f'{rid}|{ep}|{gk}' if gk else f'{rid}|{ep}'))
    else:
        cand_by_rid: dict[str, list[tuple[str, dict, bool]]] = {}
        for ((rid, ep, gk), items) in groups.items():
            best = api.best_of(items)
            sk = f'{rid}|{ep}|{gk}' if gk else f'{rid}|{ep}'
            prev = api._seen_pair(api.state_store.previous_episode(state, rid, ep, gk, api.group_eq))
            if prev is not None and best['_tier'] >= prev[0] and (best['_rev'] <= prev[1]):
                continue
            cand_by_rid.setdefault(rid, []).append((ep, best, prev is not None, sk, gk))
        for cand in cand_by_rid.values():
            has_primary = {ep for (ep, b, *_) in cand if not b.get('_fallback')}
            cand = [c for c in cand if not c[1].get('_fallback') or c[0] not in has_primary]
            if not cand:
                continue
            keep: set[int] = set()
            by_g: dict[str, list] = {}
            for (ep, b, _up, _sk, gk) in cand:
                by_g.setdefault(gk, []).append((ep, b))
            for (gk, items_g) in by_g.items():
                keep |= {id(x) for (_, x) in api.collapse_overlaps(items_g, lambda _it: gk)}
            for (ep, best, is_up, sk, gk) in cand:
                if id(best) in keep:
                    pushes.append((best, is_up, sk))
    if dry_run:
        for (best, is_upgrade, _sk) in pushes:
            (title, body) = api.build_push_content(best, best['_rule'], prefix, is_upgrade)
            print('\n' + '=' * 70)
            print(title)
            print(body)
        return len(pushes)
    queued = []
    for (best, is_upgrade, sk) in pushes:
        rule = best['_rule']
        (title, body) = api.build_push_content(best, rule, prefix, is_upgrade)
        key = api.enqueue_notification(state, best['_rid'], title, body)
        state['pending_notifications'][key]['source'] = api.fallback_wait.freeze(best)
        state['pending_notifications'][key]['rule_signature'] = api.fallback_wait.signature(rule)
        state['pending_notifications'][key]['delivery'] = {'state_key': sk, 'group': api.group_key(best, rule.get('group') or []), 'fallback': bool(best.get('_fallback')), 'episode_known': bool(best.get('episode'))}
        queued.append(key)
    for ((rid, ep, gk), items) in groups.items():
        sk = f'{rid}|{ep}|{gk}' if gk else f'{rid}|{ep}'
        best = api.best_of(items)
        prev = api._seen_pair(api.state_store.previous_episode(state, rid, ep, gk, api.group_eq))
        state.setdefault('episodes', {})[sk] = f"{best['_tier']}:{best['_rev']}" if prev is None else f"{min(prev[0], best['_tier'])}:{max(prev[1], best['_rev'])}"
    state['initialized'] = True
    api.save_state(state)
    sent = pending_sent
    for key in queued:
        sent += api.deliver_pending(state, settings, only=key, rules=cfg.get('rules'), budget=budget)
    return sent
