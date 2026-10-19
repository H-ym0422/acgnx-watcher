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
import uuid
from watcher_core import rules as rule_store
from watcher_core.configuration import validate_config, write_config
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
    from watcher_core import discovery as _impl
    return _impl.search(term, api=sys.modules[__name__])


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
            g.setdefault('releases', []).append((ep, i))
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


def survey_text(term: str, limit: int=20, season: int=0, snapshot_path: str | None=None) -> str | None:
    from watcher_core import discovery as _impl
    return _impl.survey_text(term, limit, season, snapshot_path, api=sys.modules[__name__])


def resolve_snapshot(snapshot: dict, term: str, picks: list[int], season: int = 0) -> list[str]:
    if snapshot.get("version") != 1 or snapshot.get("term") != term or snapshot.get("season") != season:
        raise ValueError("清单与番名/季号不一致，请重新获取清单")
    groups = snapshot.get("groups")
    if not isinstance(groups, list) or not groups or not all(isinstance(g, str) and g for g in groups):
        raise ValueError("清单无效，请重新获取清单")
    if not picks or any(p < 1 or p > len(groups) for p in picks):
        raise ValueError("序号超出原清单范围，未添加规则")
    return list(dict.fromkeys(groups[p - 1] for p in picks))


def resolve_picks(term: str, picks: list[int], season: int = 0, snapshot_path: str | None = None) -> list[str]:
    if not snapshot_path:
        raise ValueError("按序号选组必须使用原清单快照，请重新获取清单")
    with open(snapshot_path, encoding="utf-8") as f:
        return resolve_snapshot(json.load(f), term, picks, season)


def pick(term: str, want_all_groups: bool, forced_groups: list[str], extra_aliases: list[str] | None=None, auto_top: int=0, pick_indexes: list[int] | None=None, fallback: bool=True, season: int=0, allow_unmarked_season: bool=False, season_start_episode: int=0) -> dict | None:
    from watcher_core import discovery as _impl
    return _impl.pick(term, want_all_groups, forced_groups, extra_aliases, auto_top, pick_indexes, fallback, season, allow_unmarked_season, season_start_episode, api=sys.modules[__name__])


def append_rule(rule: dict) -> bool:
    added = rule_store.append(aw.CONFIG_PATH, rule)
    print("规则已保存" if added else "相同番名、季号和字幕组的规则已存在，未重复添加")
    return added


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


def inspect_and_seed(rule: dict, from_ep: int=0, backfill_mode: str | None=None) -> list[tuple[str, dict]]:
    from watcher_core import backfill as _impl
    return _impl.inspect_and_seed(rule, from_ep, backfill_mode, api=sys.modules[__name__])


def notify_result(rule: dict, episodes: list[tuple[str, dict]], mode: str='all', from_ep: int=0) -> bool:
    from watcher_core import backfill as _impl
    return _impl.notify_result(rule, episodes, mode, from_ep, api=sys.modules[__name__])


def format_result(rule: dict, episodes: list[tuple[str, dict]], mode: str='all', from_ep: int=0) -> tuple[str, str, dict]:
    from watcher_core import backfill as _impl
    return _impl.format_result(rule, episodes, mode, from_ep, api=sys.modules[__name__])


def queue_result(rule: dict, title: str, body: str, settings: dict) -> bool:
    from watcher_core import backfill as _impl
    return _impl.queue_result(rule, title, body, settings, api=sys.modules[__name__])


WATCHLIST_NAME = "我的番剧.md"


def render_watchlist() -> str:
    from watcher_core import watchlist as _impl
    return _impl.render_watchlist(api=sys.modules[__name__])


def write_watchlist() -> str:
    from watcher_core import watchlist as _impl
    return _impl.write_watchlist(api=sys.modules[__name__])


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

    changed = [f for f in ("config.json", "state.json", "state.backup.json", WATCHLIST_NAME)
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
    pending = st.get("pending_notifications") or {}
    queued = [key for key, message in pending.items() if message.get("rule_id") in ids]
    waiting = st.get("fallback_waiting", {})
    waiting_gone = [k for k in waiting if k.split("|", 1)[0] in ids]
    if not gone and not queued and not waiting_gone:
        return 0
    for k in waiting_gone:
        waiting.pop(k, None)
    for k in gone:
        eps.pop(k, None)
    for key in queued:
        pending.pop(key, None)
    aw.save_state(st)
    return len(gone)


def main() -> None:
    from watcher_core import rule_cli as _impl
    return _impl.main(api=sys.modules[__name__])


if __name__ == "__main__":
    main()

