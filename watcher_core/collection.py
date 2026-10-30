"""Fetch RSS sources, match rules and report per-rule source health."""
from __future__ import annotations
from .rss_search import fetch_search_feed


def collect(cfg: dict, *, api) -> list[dict]:
    """跑一轮抓取，返回所有命中的条目（已标注规则）。"""
    s = cfg['settings']
    rules = [r for r in cfg['rules'] if r.get('enabled', True)]
    (base, feed_body) = api.pick_base(cfg)
    if base is None:
        api.log.error('所有镜像都不可用，本轮跳过（下轮会重试）')
        return api.health.CollectionResult(failed_sources=len(api.BASES))
    successful_sources = 0
    failed_sources = 0
    global_feed_available = False
    timeout = int(s.get('request_timeout', 15))
    retry = int(s.get('retry', 2))
    proxy = (s.get('proxy') or '').strip()
    all_items: dict[str, dict] = {}
    if s.get('watch_global_feed', True) and feed_body:
        if api.valid_feed(feed_body):
            successful_sources += 1
            global_feed_available = True
        else:
            failed_sources += 1
        for it in api.parse_feed(feed_body):
            it['from'] = {'feed'}
            all_items[it['btih']] = it
    seen_kw: set[str] = set()
    query_health = {}
    rule_queries = {}

    def _variants(kw: str) -> list[str]:
        out = [kw]
        if len(kw) >= 7 and api.re.search('[\\u4e00-\\u9fff]', kw):
            out += [kw[:4], kw[-4:], kw[max(0, len(kw) // 2 - 2):len(kw) // 2 + 2]]
        (seen, uniq) = (set(), [])
        for k in out:
            if k and k not in seen:
                seen.add(k)
                uniq.append(k)
        return uniq[:4]
    for rule in rules:
        rule_queries[rule['id']] = set()
        for kw in rule.get('keywords') or []:
            for q in _variants(kw):
                rule_queries[rule['id']].add(q)
                if q in seen_kw:
                    continue
                seen_kw.add(q)
                body = fetch_search_feed(base, q, timeout, retry, proxy, api=api)
                query_health[q] = api.valid_feed(body)
                if api.valid_feed(body):
                    successful_sources += 1
                else:
                    failed_sources += 1
                got = api.parse_feed(body or '')
                if body is None:
                    api.log.warning('关键词「%s」RSS 搜索失败：未获得有效订阅，不能视为没有资源', q)
                elif not got and q == kw:
                    api.log.info('关键词「%s」本轮返回 0 条（站点偶发空结果，下轮重试）', kw)
                for it in got:
                    if it['btih'] in all_items:
                        all_items[it['btih']]['from'].add('search')
                    else:
                        it['from'] = {'search'}
                        all_items[it['btih']] = it
            api.time.sleep(0.5)
    skip_trad = s.get('skip_traditional_only', True)
    matched: list[dict] = []
    ambiguous = {}
    for (ri, rule) in enumerate(rules):
        rid = api.rule_id(rule, ri)
        primary = list(rule.get('group') or [])
        use_fallback = bool(primary) and rule.get('fallback_any_group', True)
        mapping = dict(cfg.get('_episode_maps', {}).get(rid, {}))
        mapping.update(api.release_identity.evidence_map(all_items.values(), rule))
        for raw in all_items.values():
            it = dict(raw)
            it['_season_evidence'] = api.release_identity.season_evidence(it, rule, mapping)
            it['episode'] = api.release_identity.canonical_episode(it, rule, mapping)
            relaxed = dict(rule, season=0)
            if rule.get('season') and it['_season_evidence'] is None and (not rule.get('allow_unmarked_season')) and api.match_rule(it, relaxed):
                ambiguous.setdefault(rid, []).append(it.get('episode') or it['btih'])
            if not api.episode_allowed(it.get('episode') or '', api.release_identity.effective_floor(rule, mapping)):
                continue
            if api.match_rule(it, rule):
                fallback = False
            elif use_fallback and api.fallback_match(it, rule):
                fallback = True
            else:
                continue
            if fallback and primary:
                if api.group_key(it, primary) in primary:
                    fallback = False
            variant = api.extract_variant(it['title'])
            if skip_trad and api.is_traditional_only(variant):
                continue
            m = dict(it)
            m['_rule'] = rule
            m['_rid'] = rid
            m['_tier'] = api.get_variant_tier(it['title'], rule.get('variant_prefer') or [])
            m['_variant'] = variant
            m['_rev'] = api.get_revision(it['title'])
            m['_seen'] = f"{m['_tier']}:{m['_rev']}"
            m['_fallback'] = fallback
            matched.append(m)
    uniq: dict[tuple, dict] = {}
    for m in matched:
        key = (m['_rid'], m['btih'])
        if key not in uniq:
            uniq[key] = m
    per_rule = {r['id']: {'successful_sources': sum((bool(query_health.get(q)) for q in rule_queries.get(r['id'], []))), 'failed_sources': sum((not query_health.get(q, False) for q in rule_queries.get(r['id'], [])))} for r in rules}
    for r in rules:
        if not rule_queries.get(r['id']):
            per_rule[r['id']] = {'successful_sources': successful_sources, 'failed_sources': failed_sources}
    return api.health.CollectionResult(uniq.values(), successful_sources, failed_sources, per_rule, ambiguous,
                                       global_feed_available=global_feed_available)
