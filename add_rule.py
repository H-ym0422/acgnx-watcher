#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
加新番向导 —— 不用手改 config.json
=========================================================================
它会去 ACGNX 上搜你想看的番，把**实际在做这部番的字幕组**列给你选，
再自动从标题里认出这部番的**所有译名**（中日罗马音），
然后生成一条规则写进 config.json（原文件会备份成 config.json.bak）。

用法：
    python add_rule.py                      交互式
    python add_rule.py 咒术回战              直接给番名
    python add_rule.py 咒术回战 --any-group    任意字幕组都推
    python add_rule.py 咒术回战 --group 喵萌奶茶屋
    python add_rule.py 咒术回战 --also 咒术廻戦 --also Jujutsu  手动补译名
    python add_rule.py --list               看现在配了哪些规则
    python add_rule.py --del 3              删掉第 3 条规则
=========================================================================
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import acgnx_watch as aw  # noqa: E402

log = logging.getLogger("add_rule")

DEFAULT_PREFER = ["简日双语", "简繁日内封", "简日内嵌", "简体", "简"]

# 字幕组名识别统一放在 acgnx_watch 里 —— 推送时要用它标注「本条实际来自哪个组」，
# 两边各写一份容易改漏，这里只做转发。
GROUP_HINT = aw.GROUP_HINT
BRACKET = aw.BRACKET
RE_JUNK_TOKEN = aw.RE_JUNK_TOKEN
RE_MIRROR_TOKEN = aw.RE_MIRROR_TOKEN
CJK = aw.CJK
is_group_like = aw.is_group_like
extract_group_tokens = aw.extract_group_tokens


def extract_alias_names(title: str) -> list[str]:
    """从标题里抽出这部番的各个译名，例如
    '[喵萌奶茶屋] 药师少女的独语 / 药屋少女的呢喃 / Kusuriya no Hitorigoto - 50 [WebRip...'
      -> ['药师少女的独语', '药屋少女的呢喃', 'Kusuriya no Hitorigoto']"""
    t = title
    t = re.sub(r"^\s*(?:[\[【][^\]】]*[\]】]\s*)+", "", t)   # 去掉开头的发布组标记
    t = re.sub(r"★[^★]*★", " ", t)                         # 去掉 ★10月新番★
    t = re.split(r"[\[\(（【]", t)[0]                        # 切到第一个集数/画质标记之前
    t = re.sub(r"\s*[-–—]\s*\d{1,3}.*$", "", t)             # 去掉 "- 50"
    t = re.sub(r"\s+", " ", t)

    out: list[str] = []
    for p in t.split("/"):
        p = p.strip(" -–—·")
        if 3 <= len(p) <= 60 and (CJK.search(p) or re.search(r"[A-Za-z]{4,}", p)):
            out.append(p)
    return out


def search(term: str) -> list[dict]:
    """搜一部番的所有已发布条目。

    ⚠️ **站点每次搜索最多只返回 50 条**，而且没有分页参数（试过 page/p/start/offset，
    全都还是那 50 条）。热门番十几个字幕组一叠加，50 条根本装不下 ——
    实测「与你相恋到生命尽头」只搜一次会漏掉 TSDM、喵萌的大部分资源。

    所以改成：**用几个关键词变体各搜一次，再按 btih 合并**。
    不同关键词命中的是不同排序/不同切片，合并后能翻出一倍多的结果。
    实测 50 → 100 条，覆盖明显更全。"""
    cfg = {"settings": {"request_timeout": 15, "retry": 2, "proxy": ""}, "rules": []}
    base, _ = aw.pick_base(cfg)
    if base is None:
        print("❌ 所有镜像都连不上，检查一下网络。")
        return []

    merged: dict[str, dict] = {}
    for kw in _search_variants(term):
        path = "/topics/rss/rss.xml?keyword=" + urllib.parse.quote(kw)
        for it in aw.parse_feed(aw.http_get(base + path, timeout=15, retry=2) or ""):
            merged.setdefault(it["btih"], it)      # 按 btih 去重，先到的为准
        if len(merged) >= 400:                       # 够用了，别把站点惹毛了
            break
    return list(merged.values())


def _search_variants(term: str) -> list[str]:
    """从番名生成几个搜索关键词，覆盖不同切片的搜索结果。

    只对**比较长的中文名**做切分（短的切了会搜到无关内容）；
    英文/罗马音名原样搜即可。"""
    t = (term or "").strip()
    if not t:
        return []
    out = [t]
    if len(t) >= 5 and re.search(r"[\u4e00-\u9fff]", t):
        # 「与你相恋到生命尽头」→ 加「与你相恋」和「相恋到生命尽头」
        # 取头 4 字和尾 4 字，两头都盖到
        if len(t) >= 7:
            out.append(t[:4])
            out.append(t[-4:])
        # 中间再切一刀，命中排序不同的那批
        mid = len(t) // 2
        out.append(t[max(0, mid - 2): mid + 2])
    seen, uniq = set(), []
    for k in out:
        if k and k not in seen:
            seen.add(k)
            uniq.append(k)
    return uniq[:5]


def extract_variant(title: str) -> str:
    """抽字幕形式。实现放在 acgnx_watch 里，推送时也用它，保证两边一致。"""
    return aw.extract_variant(title)


def ep_numbers(keys) -> set[int]:
    """把 ['01','02','05-08'] 这种集数键展开成 {1,2,5,6,7,8}。
    跨度太夸张的不展开（防止 [01-1000] 这种把内存吃光）。"""
    out: set[int] = set()
    for k in keys:
        m = re.match(r"^(\d{1,4})(?:-(\d{1,4}))?$", (k or "").strip())
        if not m:
            continue
        a = int(m.group(1))
        b = int(m.group(2) or a)
        if b - a > 300:
            continue
        out.update(range(a, b + 1))
    return out


def cover_text(nums: set[int], limit: int = 6) -> str:
    """把集数集合说成人话：「第 01~09 集（9 集）」，有缺集就标出来。"""
    if not nums:
        return "暂时没有"
    lo, hi = min(nums), max(nums)
    if hi == lo:
        return f"第 {lo:02d} 集"
    if hi - lo > 200:
        return f"第 {lo:02d}~{hi:02d} 集"
    gaps = sorted(set(range(lo, hi + 1)) - nums)
    tail = ""
    if gaps:
        shown = "、".join(f"{g:02d}" for g in gaps[:limit])
        tail = f"，缺 {shown}" + ("等" if len(gaps) > limit else "")
    return f"第 {lo:02d}~{hi:02d} 集（{len(nums)} 集{tail}）"


def rank_groups(items: list[dict]) -> list[dict]:
    """把一批条目按字幕组归并排序，并统计每个组都发过哪些字幕类型。

    这里**跳过「A&B」这种合作组名** —— 因为规则匹配是子串匹配，
    只有「豌豆字幕组」也能命中 `[豌豆字幕组&风之圣殿]` 这种合作发布的标题，
    所以列清单时没必要把合作组单列一行（会把列表撑得很乱）。"""
    grp: dict[str, dict] = {}
    for i in items:
        for t in extract_group_tokens(i["title"], i["author"]):
            if "&" in t or "＆" in t:
                continue
            key = aw.norm(t)
            g = grp.setdefault(key, {"name": t, "n": 0, "last": None, "vs": {},
                                     "eps": set()})
            if len(t) < len(g["name"]):
                g["name"] = t
            g["n"] += 1
            ep = aw.extract_episode(i["title"])
            if ep:
                g["eps"].add(ep)
            v = extract_variant(i["title"])
            if v:
                g["vs"][v] = g["vs"].get(v, 0) + 1
            d = i.get("pub_dt")
            if d and (g["last"] is None or d > g["last"]):
                g["last"] = d

    order = sorted(grp.values(), key=lambda g: (-g["n"], g["name"]))
    for g in order:
        g["variants"] = [k for k, _ in sorted(g["vs"].items(), key=lambda kv: -kv[1])]
        g["cover"] = cover_text(ep_numbers(g["eps"]))
    return order


def survey_text(term: str, limit: int = 20, season: int = 0) -> str | None:
    """生成「谁在做、覆盖哪些集、各是什么类型」的清单（Markdown），供 Issue 回复用。"""
    items = search(term)
    if not items:
        return None
    if season and int(season) > 0:
        items = [i for i in items if aw.season_ok(i["title"], int(season))]
        if not items:
            return (f"没搜到「{term}」第 {season} 季的条目。\n\n"
                    f"搜索只覆盖最近发布的，老季一般得靠后来重发的 BD / 合集。"
                    f"试着把标题改成 `加番 {term} BD` 或者 `加番 {term} 合集` 再看看。")
    order = rank_groups(items)
    if not order:
        return None

    head = f"搜到 **{len(order)}** 个字幕组在做「{term}」"
    if season:
        head += f"第 {season} 季"
    out = [head + "。回复这条 Issue 告诉我要哪个：", ""]
    for i, g in enumerate(order[:limit], 1):
        last = f"{g['last']:%m-%d}" if g["last"] else "  -  "
        vs = "、".join(g["variants"][:4]) if g["variants"] else "未标注"
        out.append(f"`{i:>2}`  **{g['name']}**  ·  {g['n']} 条，最近 {last}")
        out.append(f"　　覆盖 {g['cover']} · 类型 {vs}")
    if len(order) > limit:
        out.append(f"　　　（还有 {len(order) - limit} 个，没列出来）")

    out += ["", "**回复格式**", "",
            "- 回复 `1` 或 `1,3` —— 用这几个字幕组",
            "- 回复 `绿茶字幕组` —— 直接写组名也行",
            "- 回复 `任意` —— 不挑组，谁先出就推谁"]
    return "\n".join(out)


def resolve_picks(term: str, picks: list[int], season: int = 0) -> list[str]:
    """把用户在清单里选的序号换算回字幕组名。"""
    items = search(term)
    if not items:
        return []
    if season and int(season) > 0:
        items = [i for i in items if aw.season_ok(i["title"], int(season))]
    order = rank_groups(items)
    names = []
    for p in picks:
        if 1 <= p <= len(order):
            names.append(order[p - 1]["name"])
    return list(dict.fromkeys(names))


def pick(term: str, want_all_groups: bool, forced_groups: list[str],
         extra_aliases: list[str] | None = None,
         auto_top: int = 0, pick_indexes: list[int] | None = None,
         fallback: bool = True, season: int = 0) -> dict | None:
    print(f"\n🔍 正在 ACGNX 搜索「{term}」...")
    items = search(term)
    if not items:
        print("⚠️  没搜到任何结果。可能是：")
        print("    · 番名写法和站上不一致（试试日文原名、罗马音，或只取其中 2~4 个字）")
        print("    · 站点这一轮恰好返回空（偶发，等一会儿重试）")
        return None

    # 多季番：只留想看的那一季
    if season and int(season) > 0:
        before = len(items)
        items = [i for i in items if aw.season_ok(i["title"], int(season))]
        print(f"🎬 只要第 {season} 季：{before} 条 -> {len(items)} 条")
        if not items:
            print(f"⚠️  没搜到第 {season} 季的条目。")
            print("    搜索只覆盖最近发布，老季通常要靠后来重发的 BD / 合集。试试换个词：")
            print(f"      python add_rule.py --survey \"{term} BD\"")
            print(f"      python add_rule.py --survey \"{term} 合集\"")
            return None

    ds = [i["pub_dt"] for i in items if i.get("pub_dt")]
    span = f"，跨度 {min(ds):%Y-%m-%d} ~ {max(ds):%Y-%m-%d}" if ds else ""
    print(f"✅ 命中 {len(items)} 条{span}")

    order = rank_groups(items)
    if not order:
        print("⚠️  没识别出任何字幕组名，可能是条目太少。")
        return None

    print("\n📺 在做这部番的字幕组（含覆盖集数和字幕类型）：")
    for idx, g in enumerate(order, 1):
        last = f"{g['last']:%m-%d}" if g["last"] else "  -  "
        vs = "、".join(g["variants"][:4]) if g["variants"] else "未标注"
        print(f"   [{idx:>2}] {g['name']:<26} {g['n']:>3} 条   最近 {last}")
        print(f"        覆盖 {g['cover']}")
        print(f"        类型 {vs}")

    # ---- 选字幕组 ----
    selected: list[str] = []

    if pick_indexes is not None:
        # 序号来自 Issue 里的回复
        for p in pick_indexes:
            if 1 <= p <= len(order):
                selected.append(order[p - 1]["name"])
        if selected:
            print(f"\n🤖 按你选的序号取到：{'、'.join(selected)}")
        else:
            print("⚠️  没识别到有效序号，改成自动选发布最多的前 2 个")
            auto_top = 2

    if not selected:
        if forced_groups:
            for f in forced_groups:
                matched = [g["name"] for g in order if aw.norm(f) in aw.norm(g["name"])]
                if matched:
                    selected.extend(matched)
                else:
                    selected.append(f)
                    print(f"   ⚠️  列表里没有「{f}」，仍按你写的原样加入规则")
        elif want_all_groups:
            selected = []            # 空数组 = 任意字幕组
        elif auto_top > 0:
            # 无人值守模式：直接取发布最多的前 N 个组（通常就是主力字幕组）
            selected = [g["name"] for g in order[:auto_top]]
            print(f"\n🤖 自动选组（前 {auto_top} 个发布最多的）：{'、'.join(selected)}")
        else:
            print("\n要盯哪几个字幕组？（多选用逗号，如 1,3）")
            print("   回车 = 全选      输入 any = 任何字幕组都推")
            try:
                raw = input("   你的选择: ").strip()
            except EOFError:
                # 没有交互终端（例如在 CI 里漏传了参数），退回自动选组
                raw = ""
                print("   （没有交互终端，按全选处理）")
            if raw.lower() in ("any", "*", "任意"):
                selected = []
            elif raw == "":
                selected = [g["name"] for g in order]
            else:
                for part in re.split(r"[,，\s]+", raw):
                    if part.isdigit() and 1 <= int(part) <= len(order):
                        selected.append(order[int(part) - 1]["name"])
                if not selected:
                    print("   ⚠️  没识别到有效选择，按全选处理")
                    selected = [g["name"] for g in order]

    selected = list(dict.fromkeys(selected))

    # ---- 自动认译名：只取"被选中字幕组"发的那些条目来统计 ----
    pool = [i for i in items
            if not selected or any(aw.norm(s) in aw.norm(i["title"]) or aw.norm(s) in aw.norm(i["author"])
                                   for s in selected)]
    if not pool:
        pool = items

    alias_count: dict[str, dict] = {}
    for i in pool:
        for a in extract_alias_names(i["title"]):
            key = aw.norm(a)
            e = alias_count.setdefault(key, {"name": a, "n": 0})
            e["n"] += 1
            if len(a) < len(e["name"]):
                e["name"] = a

    threshold = max(2, int(len(pool) * 0.3))
    aliases = [e["name"] for e in alias_count.values() if e["n"] >= threshold]
    aliases.sort(key=lambda a: -alias_count[aw.norm(a)]["n"])
    aliases = list(dict.fromkeys([term] + (extra_aliases or []) + aliases))[:8]

    nt = aw.norm(term)
    hit = sum(1 for i in pool if any(aw.norm(a) in aw.norm(i["title"]) for a in aliases))
    print(f"\n🏷️  自动认出的译名（共 {len(aliases)} 个，覆盖选中组 {hit}/{len(pool)} 条标题）：")
    for a in aliases:
        print(f"     · {a}")

    rule = {
        "id": f"r{int(time.time())}",
        "name": f"{term}（{'任意字幕组' if not selected else '/'.join(selected[:2]) + ('等' if len(selected) > 2 else '')}）",
        "enabled": True,
        "keywords": [term],
        "include_any": aliases,
        "exclude_any": [],
        "group": selected,
        "variant_prefer": list(DEFAULT_PREFER),
        # 指定字幕组没做这一集（或中途弃番）时，允许用别的组补上。
        # 不加这个的话，字幕组一停，后面就永远收不到了。
        "fallback_any_group": bool(fallback),
        "note": "",
    }
    # 季号：用户没填就从番名里认（「药屋少女的呢喃 第三季」→ 只收第三季）。
    # 不认的话，「第三季」的规则会把第一、二季的合集和单集一起收进来
    # ——实测 药屋那条就是这么混进来 01-12、01-24、13-18 三个第二季的包。
    if season and int(season) > 0:
        rule["season"] = int(season)
        print(f"\n🎬 已设为「只收第 {season} 季」—— 标题里标了别的季号的会被丢掉")
    else:
        for kw in [term] + (extra_aliases or [])[:2]:
            sn = aw.extract_season(kw)
            if sn and sn > 1:
                rule["season"] = sn
                print(f"\n🎬 从番名里认出「第 {sn} 季」，已自动设为只收这一季"
                      "（不想这样就重发 Issue，在「第几季」填 0）")
                break
    return rule


def append_rule(rule: dict) -> None:
    with open(aw.CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    shutil.copyfile(aw.CONFIG_PATH, aw.CONFIG_PATH + ".bak")
    cfg.setdefault("rules", []).append(rule)
    with open(aw.CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"\n💾 已写入 {os.path.basename(aw.CONFIG_PATH)}（原文件备份为 config.json.bak）")


def ep_sort_key(ep: str):
    """把集数键排成「第1集 → 最新集」的自然顺序。
    '01' -> (1,1)；'01-12' -> (1,12)；没有集数的排最后。"""
    m = re.match(r"^(\d+)(?:-(\d+))?", ep or "")
    if m:
        a = int(m.group(1))
        return (a, int(m.group(2) or a))
    return (10 ** 6, 0)


_want_groups: list[str] = []      # 当前规则里用户显式指定的字幕组


def _group_of(it: dict) -> str:
    """这条算哪个组 —— 统一走 acgnx_watch.group_key（两边口径必须一致）。"""
    return aw.group_key(it, _want_groups)


def _same_group(a: str, b: str) -> bool:
    return aw.group_eq(a, b)


def ep_at_least(ep: str, n: int) -> bool:
    """这个集数算不算「第 n 集及以后」。

    拿不准的时候（合集、认不出的哈希键）一律返回 True —— 宁可多给你一条，
    也别把你想要的漏掉。"""
    if n <= 0:
        return True
    s = (ep or "").strip()
    if not s or s == "合集":
        return True
    m = re.match(r"^(\d{1,4})-(\d{1,4})$", s)
    if m:                       # 区间：末尾集号 >= n 就算有交集
        return int(m.group(2)) >= n
    if s.isdigit():
        return int(s) >= n
    return True


def inspect_and_seed(rule: dict, from_ep: int = 0) -> list[tuple[str, dict]]:
    """抓一遍，做两件事：
       ① 把这条规则当前命中的集数记进 state 当「已记录」基线 ——
          这样**之后只有新发布的集数才会提醒**；
       ② 返回按「第1集 → 最新集」排好的 [(集数, 最优版本条目), ...]，
          交给 notify_result 一次性推给用户。"""
    print("\n🧪 抓取一遍，看看这条规则现在能命中什么 ...")
    cfg = aw.load_config()
    probe = dict(cfg)
    probe["rules"] = [rule]
    matched = aw.collect(probe)

    # ---- ① 记基线 ----
    # ⚠️ 键的格式必须跟 run_cycle 完全一致（2026-10-17 体检发现）：
    # 这里原来写 2 段、run_cycle 读 3 段（多组规则带组名），会导致
    # 「加完番后的第一次定时检查」把全部历史又推一遍。
    want_g = [g for g in (rule.get("group") or []) if not g.endswith("*")]
    global _want_groups
    _want_groups = want_g
    buckets: dict[tuple, list] = {}
    for m in matched:
        ep = m.get("episode") or m["btih"]
        gk = aw.group_key(m, want_g)
        buckets.setdefault((ep, gk), []).append(m)
    st = aw.load_state()
    for (ep, gk), items in buckets.items():
        items.sort(key=aw.sort_key)
        key = f"{rule['id']}|{ep}|{gk}" if gk else f"{rule['id']}|{ep}"
        st["episodes"].setdefault(key, items[0]["_seen"])
    st["initialized"] = True
    aw.save_state(st)

    if not matched:
        print("   （当前没有命中的条目 —— 正常，说明这部番最近没更新）")
        return []

    # ---- ② 按集数从小到大排；同一集优先用指定字幕组的 ----
    # ⭐ **规则里显式选了多个字幕组时，每个组的资源都保留**（2026-10-17 修）。
    # 原来每集只留 best_of 一个版本，你选了「绿茶发布机 + 北宇治」，
    # 结果北宇治那一份被绿茶挤掉了 —— 选两个组却只拿到一个组的资源。
    # 现在：指定组之间互不覆盖；只有「兜底组」跟指定组抢时才让位。
    want_groups = [g for g in (rule.get("group") or []) if not g.endswith("*")]
    _want_groups = want_groups
    ordered: list[tuple[str, dict]] = []
    if len(want_groups) > 1:
        # ⭐ **每个指定字幕组各留各的**（2026-10-17 修了两轮才对）
        #   第 1 轮错在：所有指定组共用一个 "__pick__" 槽位 → 同一集里两个组只活一个
        #   第 2 轮错在：折叠时用 actual_group 分组，
        #     而绿茶发布机的条目标题里写的是 [绿茶字幕组]，
        #     导致它和真正的绿茶字幕组混在一组里互相折叠
        # 现在：槽位 key = 你认识的那个组名（_group_of 已经做过归一）
        for ep in sorted({k[0] for k in buckets}, key=ep_sort_key):
            items_ep = [it for (e, _g), lst in buckets.items() if e == ep for it in lst]
            per_slot: dict[str, tuple[str, dict]] = {}
            for it in sorted(items_ep, key=aw.sort_key):
                g = _group_of(it)
                slot = next((w for w in want_groups if _same_group(g, w)), None) \
                    or (g or "__fb__")
                cur = per_slot.get(slot)
                if cur is None or aw.sort_key(it) < aw.sort_key(cur[1]):
                    per_slot[slot] = (ep, it)
            # 指定组的资源排在前面，兜底组靠后
            for w in want_groups:
                for slot, v in per_slot.items():
                    if slot == w or _same_group(slot, w):
                        ordered.append(v)
                        break
            rest = [v for slot, v in per_slot.items()
                    if not any(slot == w or _same_group(slot, w) for w in want_groups)]
            ordered.extend(sorted(rest, key=lambda t: aw.sort_key(t[1])))
    else:
        for ep in sorted({k[0] for k in buckets}, key=ep_sort_key):
            items_ep = [it for (e, _g), lst in buckets.items() if e == ep for it in lst]
            ordered.append((ep, aw.best_of(items_ep)))
    ordered = aw.collapse_overlaps(ordered, _group_of)   # 只在同组内折叠；分组口径跟上面保持一致
    ordered.sort(key=lambda t: (ep_sort_key(t[0]), aw.sort_key(t[1])))

    # ⭐ 「只要第 N 集之后」（用户 2026-10-17 提的需求）：
    #   前面那些集数**照样写进 state 基线**（所以它们的修正版/更好版本以后也不会再推），
    #   只是**这次不列进回溯**。用户本地已有的内容不该再推一遍。
    all_n = len(ordered)
    if from_ep > 0:
        ordered = [(ep, it) for ep, it in ordered if ep_at_least(ep, from_ep)]
        skipped = all_n - len(ordered)
        print(f"\n📌 按你的要求，只回溯第 {from_ep} 集及以后 —— 跳过 {skipped} 条"
              f"（第 1~{from_ep - 1} 集已记为看过，以后出修正版也不会再推你）")

    if not ordered:
        print(f"\n（要的第 {from_ep} 集及以后暂时还没有资源，等更新了会推你）")
        return []

    print(f"   原始命中 {len(matched)} 条，归并成 {len(ordered)} 集（按集数从小到大）：")
    for ep, item in ordered:
        d = f"{item['pub_dt']:%m-%d}" if item.get("pub_dt") else "??"
        title = (item["title"][:56] + "…") if len(item["title"]) > 56 else item["title"]
        print(f"   [{ep:>8}] {d}  {title}")
        print(f"              {item.get('magnet') or '（无磁力，见详情页）'}")

    print(f"\n📌 这 {len(ordered)} 个集数已记为基线 ——")
    print("   之后只有新发布的集数才会提醒。")
    return ordered


def notify_result(rule: dict, episodes: list[tuple[str, dict]],
                  mode: str = "all", from_ep: int = 0) -> None:
    """把加番结果推到手机。
    mode: all = 把第1集到最新集的磁力全部列出（默认）
          latest = 只推最新一集
          none = 都不推，只记基线"""
    cfg = aw.load_config()
    s = cfg["settings"]
    prefix = s.get("title_prefix") or "【新番速递】"
    title = f"{prefix}已加入监听：{rule['name']}"[:120]

    lines = [f"字幕组：{' / '.join(rule['group']) if rule['group'] else '任意字幕组'}"]
    if rule.get("include_any"):
        lines.append(f"标题需含：{'、'.join(rule['include_any'])}")
    if from_ep > 0:
        lines.append(f"只要第 {from_ep} 集及以后（前面的已记为看过，不再提醒）")

    if not episodes:
        lines.append("")
        lines.append("现在还没搜到已发布的集数，之后出新集会提醒你。")
        aw.send_notification(title, "\n".join(lines), s)
        return

    nums = [ep for ep, _ in episodes if ep and ep != "-"]

    if mode == "none":
        lines += ["", f"已经发布的共 {len(episodes)} 集（按设置不推送），之后出新集会提醒。"]

    elif mode == "latest":
        ep, item = episodes[-1]
        lines += ["", f"最新一集（第 {ep} 集）：", item.get("magnet") or "（无磁力，见详情页）",
                  "", f"（这部分共 {len(episodes)} 集，其余按设置未推送）"]

    else:  # all
        rng = f"第 {nums[0]} ~ {nums[-1]} 集" if nums else f"{len(episodes)} 集"
        # 总览放在磁力**之前** —— 之前「以后只推新集数」那句挂在磁力列表最后面，
        # 容易让人以为是某条磁力的说明（用户 2026-10-17 指出）
        all_nums: set[int] = set()
        span_ends: list[int] = []
        for ep, _ in episodes:
            m2 = re.match(r"^(\d{1,3})-(\d{1,3})$", (ep or "").strip())
            if m2:
                span_ends.append(int(m2.group(2)))       # 有合集：末尾集号最能代表整季
            for x in ep_numbers([ep]):
                if x < 10 ** 6:
                    all_nums.add(x)
        if all_nums:
            # 「全季 N 集」是**推断**的：站点不提供总集数。
            # 优先用合集的末尾集号（01-13 这种包通常就是整季），否则退到已知的最大集号。
            season_max = max(span_ends) if span_ends else max(all_nums)
            got = len(all_nums)
            tail = f"（{min(all_nums):02d}~{max(all_nums):02d}）" if got > 1 else ""
            lines += ["", f"📺 全季为 {season_max} 集，已出 {got} 集{tail}",
                      f"下面共 {len(episodes)} 条磁力，按集数从小到大：", ""]
        else:
            lines += ["", f"📺 共 {len(episodes)} 条磁力，按集数从小到大：", ""]
        for ep, item in episodes:
            tag = f"[{ep}]"
            g = _group_of(item)
            if g:
                tag += f" {g}"
            lines.append(f"{tag} {item.get('magnet') or '（无磁力，见详情页）'}")
        lines += ["", "———", "以上是历史集，已经记下来了。以后只推新发布的集数，不会再重复推这些。"]

    aw.send_notification(title, "\n".join(lines), s)


WATCHLIST_NAME = "我的番剧.md"


def render_watchlist() -> str:
    """把当前在追的番渲染成一份 Markdown 清单。返回正文。"""
    cfg = aw.load_config()
    st = aw.load_state()
    rules = [r for r in cfg["rules"] if r.get("enabled", True)]
    eps_map = st.get("episodes", {})

    out = [
        "# 我的追番列表",
        "",
        f"**当前在追 {len(rules)} 部。** 这个文件自动生成，有新集时会刷新，不用手动改。",
        "",
        "| 番剧 | 字幕组 | 已记录到 | 已记录 | 说明 |",
        "|---|---|---|---|---|",
    ]
    for r in rules:
        rid = r.get("id") or ""
        # ⚠️ 键有两种格式，都要认（2026-10-17 修）：
        #   2 段 `规则id|集数`             —— 单字幕组规则
        #   3 段 `规则id|集数|字幕组`       —— 多字幕组规则（2026-10-17 加的）
        # 原来只 split("|", 1)[1]，3 段键会取出「01|喵萌奶茶屋」这种
        # 不合法值，全被过滤掉 → 列表显示「暂时没有」（药屋那部就中招了）
        eps: set[str] = set()
        for k in eps_map:
            if not k.startswith(rid + "|"):
                continue
            parts = k.split("|")
            for cand in (parts[1], parts[2] if len(parts) > 2 else ""):
                if cand and re.fullmatch(r"\d{1,3}(?:-\d{1,3})?", cand):
                    eps.add(cand)
                    break
        eps = sorted(eps, key=ep_sort_key)
        if eps:
            latest = f"第 {eps[-1]} 集" + ("（合集）" if "-" in eps[-1] else "")
        else:
            latest = "暂时没有"
        grp = " / ".join(r.get("group") or []) or "任意字幕组"
        # 规则名里已经带了字幕组，表格里只留番名部分
        name = (r.get("name") or "?").split("（")[0]
        notes = []
        if r.get("season"):
            notes.append(f"只收第 {r['season']} 季")
        if r.get("note"):
            notes.append(r["note"][:24])
        out.append(f"| {name} | {grp} | {latest} | {len(eps)} 集 | {'、'.join(notes) or '—'} |")

    out += [
        "",
        "**怎么看这张表**",
        "",
        "| 列 | 意思 |",
        "|---|---|",
        "| 已记录到 | 已经推送到第几集。之后每出一集新集都会推给你 |",
        "| 已记录 | 一共记了多少条（含多个字幕组的版本、去重前的） |",
        "| 说明 | 有特殊设置时会写在这里，比如「只收第 3 季」 |",
        "",
        "---",
        "",
        "## 加番 / 删番 / 看列表",
        "",
        "在仓库的 **Issues** 里新开一个，标题写命令：",
        "",
        "| 标题写 | 作用 |",
        "|---|---|",
        "| `加番 咒术回战` | 加一部番 |",
        "| `删番 咒术回战` | 删掉，不再推 |",
        "| `列表` | 重新看一遍这张表 |",
        "",
        "提交后约 20 秒，机器人会在那条 Issue 里回复结果。",
        "",
        "## 加番时正文能填什么",
        "",
        "用「新建议题」按钮新建的话，下面有三个输入框：",
        "",
        "| 输入框 | 什么时候填 | 例子 |",
        "|---|---|---|",
        "| **字幕组**（可选） | 只想看某个组做的 | `喵萌奶茶屋`<br>`绿茶字幕组,喵萌奶茶屋` |",
        "| **第几季**（可选） | 多季番只追某一季 | `3` |",
        "| **只要第几集之后**（可选） | **本地已经有前面几集了** | `49` |",
        "",
        "都不填也能用：机器人会先列出有哪些字幕组在做这部番、各自做到了第几集、",
        "字幕类型是什么，你在同一条 Issue 下面回复序号或组名，它才会真正加上。",
        "",
        "### 「只要第几集之后」怎么用",
        "",
        "比如《药屋少女的呢喃》第三季你本地已经有前 48 集了，就填 **49**：",
        "",
        "```",
        "加番 药屋少女的呢喃 第三季",
        "",
        "喵萌奶茶屋",
        "",
        "49        ← 只要第几集之后",
        "```",
        "",
        "- 只会推第 49 集及以后的",
        "- **前面那些会记成「已看过」** —— 以后它们出修正版、或出了更好的字幕形式，**也不会再推你**",
        "- **留空 = 从第 1 集开始**（跟以前一样）",
        "",
        "判断依据是集数本身：第 48 集会跳过、第 49 集会保留；",
        "合集看它覆盖到的最大集号（`45-50` 这种有交集的会保留）；",
        "认不出集数的条目一律保留 —— 宁可多给一条也不漏。",
        "",
        "### 三个例子",
        "",
        "**① 最简单**：标题写 `加番 咒术回战`，正文全留空 → 机器人列清单，你回 `2`",
        "",
        "**② 指定字幕组一步到位**：标题 `加番 咒术回战`，正文只填 `喵萌奶茶屋` → 直接加，不问",
        "",
        "**③ 跳过已有集数**：标题 `加番 药屋少女的呢喃 第三季`，",
        "正文填 `喵萌奶茶屋` + `只要第几集之后` 填 `49` → 只推 49 集及以后",
        "",
        "---",
        "",
        f"<sub>自动生成于 {aw.datetime.now(aw.TZ8):%Y-%m-%d %H:%M}（北京时间）</sub>",
    ]
    return "\n".join(out) + "\n"


def write_watchlist() -> str:
    """写出「我的番剧.md」，返回文件路径。内容没变就不重写。"""
    text = render_watchlist()
    path = os.path.join(os.path.dirname(os.path.abspath(aw.CONFIG_PATH)), WATCHLIST_NAME)
    old = ""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                old = f.read()
        except Exception:  # noqa: BLE001
            old = ""
    if old != text:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path
    return path


# --------------------------------------------------------------------------
# 如果当前目录是个带 remote 的 git 仓库（云端版），顺手提交并推送
# --------------------------------------------------------------------------
def _git(root: str, *args: str):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)


DEFAULT_PROXY = "http://127.0.0.1:7890"


def _git_push(root: str) -> tuple[bool, str]:
    """推送。先走本地代理再试直连 ——
    国内直连 github.com 经常握手到一半被掐断（send-pack: unexpected disconnect），
    而代理没开的时候连接会被立刻拒绝，所以「代理优先」两种情况下都不会白等。"""
    proxy = (os.environ.get("ACGNX_PROXY") or DEFAULT_PROXY).strip()
    attempts = [
        (f"走代理 {proxy}", ["-c", f"http.proxy={proxy}",
                             "-c", f"https.proxy={proxy}", "push"]),
        ("直连", ["push"]),
    ]
    last = ""
    for label, extra in attempts:
        r = _git(root, *extra)
        if r.returncode == 0:
            return True, label
        last = (r.stderr or r.stdout).strip()
        print(f"   · {label}失败：{last.splitlines()[-1][:120] if last else '未知原因'}")
    return False, last


def maybe_push(term: str, do_push: bool) -> None:
    root = os.path.dirname(os.path.abspath(__file__))
    if not do_push:
        return
    if _git(root, "rev-parse", "--show-toplevel").returncode != 0:
        return                                    # 不是 git 仓库，跳过，静默
    if not _git(root, "remote").stdout.strip():
        print("\nℹ️  这个目录是个 git 仓库但还没配远程地址，")
        print("    等 `push_to_github.bat` 推过一次之后，以后加番就会自动同步了。")
        return

    changed = [f for f in ("config.json", "state.json", WATCHLIST_NAME)
               if _git(root, "status", "--porcelain", "--", f).stdout.strip()]
    if not changed:
        print("\nℹ️  配置没有变化，不需要推送。")
        return

    if input("\n🚀 顺便提交并推送到 GitHub，让云端 5 分钟内生效？(Y/n) ").strip().lower() in ("n", "no"):
        print("   已跳过。记得自己 git push，否则云端不会更新。")
        return

    r = _git(root, "add", *changed)
    if r.returncode != 0:
        print(f"   ✗ git add 失败：{(r.stderr or r.stdout).strip()[:200]}")
        return
    r = _git(root, "commit", "-m", f"{term} [skip ci]")
    if r.returncode != 0:
        print(f"   ✗ git commit 失败：{(r.stderr or r.stdout).strip()[:200]}")
        return

    ok, info = _git_push(root)
    if ok:
        print(f"   ✅ 已推送（{info}），云端下一次运行就会生效。")
    else:
        print("   ✗ 两种方式都没推上去。常见原因：")
        print("     · 代理没开，而且直连也连不上 GitHub")
        print("     · 代理端口不是 7890 —— 可以设环境变量 ACGNX_PROXY 指定，例如")
        print("         set ACGNX_PROXY=http://127.0.0.1:7897")
        print("     修好在目录里手动执行一次 `git push` 即可。")


def purge_baseline(rule_ids: list[str]) -> int:
    """删规则时顺手清掉它在 state.json 里的去重基线。

    不清的话有个隐蔽的坑：删掉某部番、过几天重新加回来，
    因为「以前推过」的记录还在，加番时**不会推送回溯**，
    用户会以为加番坏了。（2026-10-16 全面体检时发现：state 从 40 集涨到 80 集，
    全是已删规则的残留基线。）"""
    ids = {i for i in rule_ids if i}
    if not ids:
        return 0
    st = aw.load_state()
    eps = st.get("episodes") or {}
    gone = [k for k in eps if k.split("|", 1)[0] in ids]
    if not gone:
        return 0
    for k in gone:
        eps.pop(k, None)
    aw.save_state(st)
    return len(gone)


def main() -> None:
    aw.setup_stdout()
    # 让 acgnx_watch 里的推送日志（成功/失败）能显示出来，否则出问题看不见
    logging.basicConfig(level=logging.INFO,
                        format="[%(levelname)s] %(message)s")

    ap = argparse.ArgumentParser(description="ACGNX 监听器 —— 加新番向导")
    ap.add_argument("term", nargs="?", help="番名（越有辨识度越好）")
    ap.add_argument("--group", action="append", default=[], help="限定字幕组，可重复")
    ap.add_argument("--also", action="append", default=[], help="手动补充番名的其它写法")
    ap.add_argument("--any-group", action="store_true", help="任意字幕组都推")
    ap.add_argument("--auto-group", action="store_true",
                    help="不问，自动选发布最多的前 2 个字幕组（无人值守用）")
    ap.add_argument("--survey", action="store_true",
                    help="只列出有哪些字幕组在做、各发过什么类型，不改配置（Issue 第一步用）")
    ap.add_argument("--pick", metavar="序号",
                    help="按 --survey 里的序号挑字幕组，例如 1,3")
    ap.add_argument("--notify", action="store_true",
                    help="把加番结果推到手机（云端用，含最新一集磁力）")
    ap.add_argument("--backfill", choices=["all", "latest", "none"], default="all",
                    help="加番时把已发布的集数推到什么程度："
                         "all=第1集到最新集全部给你（默认）；latest=只给最新一集；none=都不推")
    ap.add_argument("--from-episode", type=int, default=0, metavar="N",
                    help="只要第 N 集及以后。前面那些会记成「已看过」，"
                         "既不推给你，以后出修正版也不会再推。0=不限制（默认）")
    ap.add_argument("--list", action="store_true", help="列出已有规则")
    ap.add_argument("--render", action="store_true",
                    help=f"生成/刷新「{WATCHLIST_NAME}」并打印（手机上直接看这个文件）")
    ap.add_argument("--no-fallback", action="store_true",
                    help="只推指定字幕组；默认会用别的组补上他们没做的集数")
    ap.add_argument("--season", type=int, default=0, metavar="N",
                    help="多季番只想看第 N 季时用，例如 --season 1")
    ap.add_argument("--parse-report", action="store_true",
                    help="看看有哪些标题的集数还没认出来（补正则用）")
    ap.add_argument("--exists", metavar="关键词",
                    help="只检查这部番是不是已经在追了，在追就退出码 0、否则 1")
    ap.add_argument("--del", dest="delete", type=int, metavar="N", help="删除第 N 条规则")
    ap.add_argument("--del-name", metavar="关键词",
                    help="删除名字里包含该关键词的规则（手机上传个词就能删，不用记序号）")
    ap.add_argument("--yes", action="store_true", help="跳过确认直接写入")
    ap.add_argument("--no-push", action="store_true", help="就算在 git 仓库里也不自动推送")
    args = ap.parse_args()

    if args.exists:
        cfg = aw.load_config()
        hit = [r.get("name") for r in cfg["rules"]
               if args.exists in " ".join(r.get("keywords") or [])]
        if hit:
            print("已在追：" + "、".join(str(h) for h in hit))
            raise SystemExit(0)
        raise SystemExit(1)

    if args.survey:
        term = args.term or input("哪部番？（2~4 个最有辨识力的字）\n> ").strip()
        if not term:
            print("没输入番名。")
            return
        txt = survey_text(term, season=args.season)
        print(txt if txt else
              f"没搜到「{term}」。试试日文原名、罗马音，或者只取其中 2~4 个字。")
        return

    if args.list:
        cfg = aw.load_config()
        st = aw.load_state()
        rules = [r for r in cfg["rules"] if r.get("enabled", True)]
        print(f"\n当前在追 {len(rules)} 部：\n" + "-" * 70)
        for i, r in enumerate(rules, 1):
            rid = r.get("id") or ""
            eps = sorted((k.split("|", 1)[1] for k in st.get("episodes", {})
                          if k.startswith(rid + "|")), key=ep_sort_key)
            done = f"已记录到第 {eps[-1]} 集（共 {len(eps)} 集）" if eps else "还没记录"
            print(f"[{i}] {r.get('name', '?')}")
            print(f"    字幕组={r.get('group') or '任意'}  关键词={r.get('keywords')}  {done}")
        print("-" * 70)
        print(f"（完整清单：跑 --render，或看仓库里的「{WATCHLIST_NAME}」）")
        return

    if args.render:
        text = render_watchlist()
        path = write_watchlist()
        print(text, end="")          # stdout 只放纯 Markdown，方便被别的脚本直接引用
        log.info("清单已写入 %s", path)
        maybe_push("watchlist", not args.no_push)
        return

    if args.parse_report:
        st = aw.load_state()
        box = st.get("unparsed") or {}
        if not box:
            print("最近没有解析不出集数的标题 —— 当前规则覆盖得不错 ✅")
            return
        rows = sorted(box.items(), key=lambda kv: kv[1].get("last", ""), reverse=True)
        print(f"有 {len(rows)} 个条目的集数还没认出来（最新在前）：\n")
        for btih, e in rows:
            print(f"  出现 {e.get('n', 1):>3} 次  最近 {e.get('last', '?')}  [{e.get('group', '?')}]")
            print(f"      {e.get('title', '')}")
        print("\n这些条目的磁力还是能正常用，只是推送里会写「整季打包，没有单一集数」。")
        print("看到新写法就告诉我，补一条正则就能认出来。")
        return

    if args.del_name:
        with open(aw.CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        rules = cfg.get("rules", [])
        hit = [i for i, r in enumerate(rules) if args.del_name in (r.get("name") or "")]
        if not hit:
            print(f"没找到名字包含「{args.del_name}」的规则。当前规则：")
            for i, r in enumerate(rules, 1):
                print(f"  [{i}] {r.get('name')}")
            return
        shutil.copyfile(aw.CONFIG_PATH, aw.CONFIG_PATH + ".bak")
        removed_ids = []
        for i in reversed(hit):
            print("已删除：", rules[i].get("name"))
            removed_ids.append(rules[i].get("id") or "")
            rules.pop(i)
        with open(aw.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        n = purge_baseline(removed_ids)
        if n:
            print(f"   （顺带清掉 {n} 条去重基线，不然重新加回来不会推送回溯）")
        write_watchlist()
        maybe_push(args.del_name, not args.no_push)
        print(f"\n✅ 删掉 {len(hit)} 条规则。")
        return

    if args.delete:
        with open(aw.CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        rules = cfg.get("rules", [])
        if not (1 <= args.delete <= len(rules)):
            print("序号超出范围。")
            return
        gone = rules.pop(args.delete - 1)
        shutil.copyfile(aw.CONFIG_PATH, aw.CONFIG_PATH + ".bak")
        with open(aw.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        print(f"已删除规则：{gone.get('name')}")
        n = purge_baseline([gone.get("id") or ""])
        if n:
            print(f"   （顺带清掉 {n} 条去重基线）")
        write_watchlist()
        maybe_push(gone.get("name") or "rule", not args.no_push)
        return

    term = args.term or input("想看哪部番？（输番名里最有辨识度的 2~4 个字）\n> ").strip()
    if not term:
        print("没输入番名，退出。")
        return

    pick_indexes = None
    if args.pick:
        pick_indexes = [int(x) for x in re.split(r"[,，\s]+", args.pick) if x.isdigit()]

    rule = pick(term, args.any_group, args.group, args.also,
                auto_top=2 if args.auto_group else 0,
                pick_indexes=pick_indexes,
                fallback=not args.no_fallback,
                season=args.season)
    if rule is None:
        if args.notify:
            s = aw.load_config()["settings"]
            aw.send_notification(
                f"{s.get('title_prefix') or '【新番速递】'}加番失败：{term}"[:120],
                "没在 ACGNX 搜到这部番。\n\n"
                "可能的原因：\n"
                "· 番名写法跟站上不一致 —— 试试日文原名、罗马音，或只取其中 2~4 个字\n"
                "· 站点这一轮恰好返回空结果（偶发，过几分钟重跑一次即可）",
                s)
        return

    print("\n即将加入的规则：")
    print(json.dumps(rule, ensure_ascii=False, indent=2))

    if not args.yes:
        if input("\n确认写入 config.json？(y/N) ").strip().lower() not in ("y", "yes"):
            print("已取消。")
            return

    if args.from_episode > 0:
        rule["from_episode"] = args.from_episode
    append_rule(rule)
    episodes = inspect_and_seed(rule, from_ep=args.from_episode)
    write_watchlist()
    if args.notify:
        notify_result(rule, episodes, args.backfill, from_ep=args.from_episode)
    maybe_push(term, not args.no_push)

    print("\n🎉 完成。想微调就编辑 config.json 里这条规则（字段含义见 README.md）。")


if __name__ == "__main__":
    main()

