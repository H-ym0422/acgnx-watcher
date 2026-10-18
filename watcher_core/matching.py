"""Rule matching and primary/fallback selection policy."""
from __future__ import annotations
import re
from .parsing import norm, season_ok, RE_NOT_EPISODE, group_eq

def match_rule(item: dict, rule: dict) -> bool:
    # 音乐/原声/主题曲这类不是正片，直接不要
    if RE_NOT_EPISODE.search(item.get("title", "")):
        return False
    nt = norm(item["title"])
    na = norm(item["author"])

    inc = rule.get("include_any") or []
    if inc and not any(norm(k) in nt for k in inc):
        return False

    exc = rule.get("exclude_any") or []
    if exc and any(norm(k) in nt for k in exc):
        return False

    # 分季过滤：想只看第一季，就别让第二三四五季混进来
    sea = rule.get("season")
    if sea and not season_ok(item["title"], int(sea), rule.get("allow_unmarked_season", False)):
        return False

    grp = rule.get("group") or []
    if grp and not any(norm(k) in nt or norm(k) in na for k in grp):
        return False

    return True


def _compact(s: str) -> str:
    """归一化 + 去掉空格和标点，用于宽松比较番名。"""
    return re.sub(r"[\s\-_/·・,，、。()（）\[\]【】!！?？]+", "", norm(s or ""))


def rule_anime_name(rule: dict) -> str:
    """规则名里「（字幕组）」之前那部分就是番名。"""
    return re.split(r"[（(]", rule.get("name") or "")[0].strip()


def fallback_match(item: dict, rule: dict) -> bool:
    """兜底匹配：其它条件照旧，只把「字幕组」这一条放开，
    但**额外要求标题里出现完整的番名**。

    没有这道保险就会出洋相：规则关键词只是「久留美」「暗部」这种两三个字，
    一放宽字幕组，钢铁天使久留美、火影忍者卡卡西暗部篇全都会被当成命中。"""
    relaxed = dict(rule)
    relaxed["group"] = []
    if not match_rule(item, relaxed):
        return False

    nt = _compact(item.get("title") or "")
    name = rule_anime_name(rule)
    if name and _compact(name) in nt:
        return True
    # 退一步：规则里那些够长的译名也算（够长 = 歧义小）
    return any(len(a) >= 4 and _compact(a) in nt
               for a in (rule.get("include_any") or []))


def suppress_fallbacks(items: list[dict], state: dict | None = None) -> list[dict]:
    """Use all observed primary versions, not only this cycle's unsent candidates."""
    primary = state.setdefault("primary_episodes", {}) if state is not None else {}
    for item in items:
        ep = item.get("episode") or item["btih"]
        if state is not None and item.get("_fallback"):
            prefix = f"{item['_rid']}|{ep}|"
            wanted = item.get("_rule", {}).get("group") or []
            if any(key.startswith(prefix) and any(group_eq(key[len(prefix):], w) for w in wanted)
                   for key in state.get("episodes", {})):
                primary[f"{item['_rid']}|{ep}"] = True
        if not item.get("_fallback"):
            primary[f"{item['_rid']}|{ep}"] = True
    return [item for item in items if not item.get("_fallback") or
            not primary.get(f"{item['_rid']}|{item.get('episode') or item['btih']}")]


__all__ = ['match_rule', '_compact', 'rule_anime_name', 'fallback_match', 'suppress_fallbacks']
