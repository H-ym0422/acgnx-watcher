#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ACGNX 新番发布实时监听器
=========================================================================
数据源：末日動漫資源庫 (Project AcgnX Torrent)
    最新发布流   https://share.acgnx.cc/rss.xml
    关键词搜索   https://share.acgnx.cc/topics/rss/rss.xml?keyword=xxx

为什么用 .cc 而不是 .se：
    官方主域名 share.acgnx.se 在国内已被 DNS 污染（解析到 Facebook 的 IP），
    直连必然超时。share.acgnx.cc 是官方镜像，走 Cloudflare，国内可直连。
    网页版有 Cloudflare 的 JS 反爬挑战，但 RSS 接口不设挑战 —— 所以走 RSS。

特性：
    * 双路抓取：全站最新流（不漏）+ 每条规则的关键词搜索（可回溯补漏）
    * 只依赖 Python 标准库，无需 pip install
    * 磁力链接直接提取（RSS 的 <enclosure url="magnet:?xt=...">）
    * 按 (规则, 集数) 归并多版本（简繁日内封 / 简日内嵌 / 繁日內嵌 …），
      每个集数只推一条磁力，更优版本出现时再补推
    * 状态持久化，重启不重复推送；多域名自动降级
    * 推送支持 企业微信群机器人 / 钉钉群机器人 / 飞书机器人（自动识别）

用法：
    python acgnx_watch.py                 持续监听（默认 5 分钟一轮）
    python acgnx_watch.py --once          只跑一轮就退出（可配 Windows 计划任务）
    python acgnx_watch.py --once --dry-run   只打印命中，不推送、不写状态
    python acgnx_watch.py --test          发一条测试消息到 Webhook
    python acgnx_watch.py --list          列出当前能命中的最新条目（校准规则用）
    python acgnx_watch.py --reset-state   清空去重记录
=========================================================================
"""

from __future__ import annotations

from watcher_core.configuration import validate_config
from watcher_core import state_store, health
from watcher_core.parsing import *  # Backward-compatible public parser API
from watcher_core.matching import *
from watcher_core.notifications import *
from watcher_core.network import http_get, pick_base


import argparse
import base64
import gzip
import hashlib
import hmac
import html
import json
import logging
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from logging.handlers import RotatingFileHandler

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------
APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
STATE_PATH = os.path.join(APP_DIR, "state.json")
LOG_DIR = os.path.join(APP_DIR, "logs")

# 镜像站按优先级排列，前一个失败自动降级到下一个
BASES = [
    "https://share.acgnx.cc",
    "https://share.acgnx.net",
    "https://share.acgnx.se",
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TZ8 = timezone(timedelta(hours=8))

log = logging.getLogger("acgnx")


# --------------------------------------------------------------------------
# 繁 -> 简 归一化表
#   ACGNX 上同一个字幕组/番名会同时出现简繁两种写法
#   （例如 [绿茶字幕组] / [綠茶字幕組]、某暗部的少女共栖 / 共棲）
#   匹配前统一转成简体，避免规则写两遍。config 里也允许直接写多个变体。
#
#   ⚠️ 每一项都写成「繁简」两个字。之前这里是一长串字符按位置两两配对，
#      只要有一处错位后面全乱（出过「风→飛」「绿→維」这种事故，直接毁掉繁简匹配）。
#      两个字一组的写法从根上杜绝错位，模块加载时还会做一致性检查。
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# 配置 / 状态
# --------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "settings": {
        "interval_minutes": 5,
        # ---- 推送渠道 ----
        # push_channel: auto = 按 PushPlus → Server酱 → 群机器人 挑第一个填了的；
        #               也可以强制指定 pushplus / serverchan / webhook
        "push_channel": "auto",
        "pushplus_token": "",          # 个人微信，免费 200 条/天（需手机号实名）
        "serverchan_sendkey": "",      # 个人微信，免费 5 条/天
        "webhook_url": "",             # 群机器人：企业微信 / 钉钉 / 飞书
        "dingtalk_secret": "",         # 钉钉「加签」
        "dingtalk_keyword": "新番",     # 钉钉「自定义关键词」
        "feishu_secret": "",           # 飞书「签名校验」
        # ---- 其它 ----
        "title_prefix": "【新番速递】",
        "first_run_mode": "latest",       # latest | all | silent
        # PushPlus / Server酱 都限流（PushPlus 消息接口 1 分钟最多 5 次），
        # 关机一段时间后集中补推时靠这个间隔把请求摊开，避免被判超频
        "push_min_interval_seconds": 13,
        "request_timeout": 15,
        "retry": 2,
        "proxy": "",
        "poll_jitter_seconds": 20,
        "watch_global_feed": True,
        # 只要繁体字幕（繁日內嵌、繁體 这种）的版本直接不推。
        # 「简繁日内封」这种同时含简体的不受影响。
        "skip_traditional_only": True,
    },
    "rules": []
}


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        raise SystemExit(f"找不到配置文件：{CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    merged["settings"].update(cfg.get("settings", {}))
    merged["rules"] = cfg.get("rules", [])

    # 允许用环境变量覆盖（云端 / 计划任务里不必把密钥写进文件）
    env_map = {
        "webhook_url": ("ACGNX_WEBHOOK_URL",),
        "dingtalk_secret": ("ACGNX_DINGTALK_SECRET",),
        "feishu_secret": ("ACGNX_FEISHU_SECRET",),
        "pushplus_token": ("ACGNX_PUSHPLUS_TOKEN", "PUSHPLUS_TOKEN"),
        "serverchan_sendkey": ("ACGNX_SERVERCHAN_SENDKEY", "SERVERCHAN_SENDKEY"),
        # 飞书自建应用（能拿到已读回执）。三个都齐了才会启用这个渠道。
        "feishu_app_id": ("ACGNX_FEISHU_APP_ID",),
        "feishu_app_secret": ("ACGNX_FEISHU_APP_SECRET",),
        "feishu_chat_id": ("ACGNX_FEISHU_CHAT_ID",),
    }
    for field, names in env_map.items():
        for n in names:
            v = (os.environ.get(n) or "").strip()
            if v:
                merged["settings"][field] = v
                break
    validate_config(merged)
    return merged


def load_state() -> dict:
    return state_store.read(STATE_PATH, log)


def save_state(st: dict) -> None:
    state_store.write(STATE_PATH, st)


# HTTP
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# RSS 解析
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# 推送
# --------------------------------------------------------------------------
def build_push_content(item: dict, rule: dict, prefix: str,
                       upgrade: bool = False) -> tuple[str, str]:
    """返回 (标题, 正文)。
    标题保证单行 —— Server酱 / PushPlus 的标题都不允许出现换行。"""
    name = rule.get("name") or item["title"][:60]
    rev = item.get("_rev", 1)
    if upgrade:
        tag = "（修正版）" if rev > 1 else "（更优版本）"
    else:
        tag = ""
    title = f"{prefix}{tag}{name}"[:120]

    # 推送时间放在最前面，像微信聊天记录那样一眼看到是几点收到的
    # （2026-10-16 用户要求：年月日都要）
    # ⚠️ 必须显式用 TZ8，不能写 datetime.now()：
    #    GitHub Actions 的 runner 是 UTC，裸的 now() 会比北京时间晚 8 小时
    #    （2026-10-16 用户在飞书里看到 13:16 而实际是 21:16，提出来的）
    lines = [f"推送时间：{datetime.now(TZ8):%Y年%m月%d日 %H:%M}（北京时间）", ""]
    lines.append(f"标题：{item['title']}")

    # 字幕组：显示的是**实际发布这个文件的组**。
    # 之前这里显示的是「规则里配置的组」，导致兜底时会连着出现两个组名，
    # 而且上面写着「字幕组：喵萌奶茶屋」下面又写「指定字幕组还没有」，
    # 看着自相矛盾（2026-10-16 用户反馈）。
    want_list = [g for g in (rule.get("group") or []) if not g.endswith("*")]
    want = " / ".join(rule.get("group") or []) or "任意字幕组"
    src = group_key(item, want_list) or "其它字幕组"
    lines.append(f"字幕组：{src}")
    # 只在真的换了组时才说明，且**必须说清是哪一集、实际用了谁**
    # （2026-10-17 用户反馈：老写法「你指定的 X 这一集还没出」很容易被理解成
    #   「整部番都没有」，而实际上只是**那一集**喵萌没做）
    if item.get("_fallback") and src not in want_list:
        epn = item.get("episode") or "这一集"
        lines.append(f"※ 第 {epn} 集：「{'、'.join(want_list)}」还没出，"
                     f"这条用的是「{src}」做的")

    # 字幕形式（简日双语 / 简繁日内封 …）。
    # 没标出来时**照样写出这一行**并说清为什么 ——
    # 省略整行的话，你分不清是「脚本没解析出来」还是「这集本来就是生肉」。
    # （2026-10-16 你问「为什么没标注」：实测 50 条里 37 条标不出，主要是 ABEMA 生肉）
    if item.get("_variant"):
        lines.append(f"字幕形式：{item['_variant']}")
    else:
        lines.append("字幕形式：未标注，请查看发布说明")

    if rev > 1:
        lines.append(f"版本：v{rev}（修正版）")

    # 集数。原来解析不出时会退回用 magnet 的 btih 当键，显示出来就是一串哈希，
    # 看着像乱码。现在明确说明是整季打包（2026-10-16 你问「为什么有的磁力没标集数」）。
    ep = item.get("episode") or ""
    if not ep:
        lines.append("集数：合集（标题有合集标记，未识别范围）" if RE_BATCH_HINT.search(item["title"]) else "集数：暂未识别，请查看发布标题")
    elif "-" in ep:
        lines.append(f"集数：第 {ep} 集（合集/整季）")
    elif ep == "合集":
        lines.append("集数：合集（标题里没写具体范围）")
    else:
        lines.append(f"集数：第 {ep} 集")

    if item.get("size"):
        lines.append(f"大小：{item['size']}")
    if item.get("pub_dt"):
        lines.append(f"发布：{item['pub_dt']:%Y-%m-%d %H:%M}")
    if item.get("link"):
        lines.append(f"详情：{item['link']}")
    if item.get("magnet"):
        lines.append("磁力：")
        lines.append(item["magnet"])
    else:
        lines.append("（本条 RSS 未附带磁力，请点详情页获取）")
    if rule.get("note"):
        lines.append(f"备注：{rule['note']}")
    return title, "\n".join(lines)


# --------------------------------------------------------------------------
# 底层 POST（返回原始响应文本，由各渠道自己判断成功与否）
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# 抓取
# --------------------------------------------------------------------------


def valid_feed(body: str | None) -> bool:
    if not body:
        return False
    try:
        return ET.fromstring(body.strip()).tag in ("rss", "{http://www.w3.org/2005/Atom}feed")
    except ET.ParseError:
        return False


def collect(cfg: dict) -> list[dict]:
    """跑一轮抓取，返回所有命中的条目（已标注规则）。"""
    s = cfg["settings"]
    rules = [r for r in cfg["rules"] if r.get("enabled", True)]
    base, feed_body = pick_base(cfg)
    if base is None:
        log.error("所有镜像都不可用，本轮跳过（下轮会重试）")
        return health.CollectionResult(failed_sources=len(BASES))

    successful_sources = 0
    failed_sources = 0
    timeout = int(s.get("request_timeout", 15))
    retry = int(s.get("retry", 2))
    proxy = (s.get("proxy") or "").strip()
    all_items: dict[str, dict] = {}

    # ---- 路径 1：全站最新流（主路径，保证不漏） ----
    if s.get("watch_global_feed", True) and feed_body:
        if valid_feed(feed_body):
            successful_sources += 1
        else:
            failed_sources += 1
        for it in parse_feed(feed_body):
            it["from"] = {"feed"}
            all_items[it["btih"]] = it

    # ---- 路径 2：每条规则的关键词搜索（补漏 / 电脑睡过一觉后回溯） ----
    # ⚠️ **站点每次搜索最多只返回 50 条，且没有分页参数**（page/p/start/offset 全试过）。
    #    热门番十几个字幕组一叠加，50 条装不下 —— 实测「与你相恋到生命尽头」
    #    单搜一次只拿到 8 个组，绿茶发布机那 14 条根本没进来。
    #    所以对**长中文番名**额外搜几个切分变体，按 btih 合并
    #    （命中的是不同排序/不同切片，合并后能翻出一倍多的结果：50 → 79~100 条）。
    seen_kw: set[str] = set()

    def _variants(kw: str) -> list[str]:
        out = [kw]
        if len(kw) >= 7 and re.search(r"[\u4e00-\u9fff]", kw):
            out += [kw[:4], kw[-4:], kw[max(0, len(kw) // 2 - 2): len(kw) // 2 + 2]]
        seen, uniq = set(), []
        for k in out:
            if k and k not in seen:
                seen.add(k)
                uniq.append(k)
        return uniq[:4]

    for rule in rules:
        for kw in (rule.get("keywords") or []):
            for q in _variants(kw):
                if q in seen_kw:
                    continue
                seen_kw.add(q)
                path = "/topics/rss/rss.xml?keyword=" + urllib.parse.quote(q)
                body = http_get(base + path, timeout=timeout, retry=retry, proxy=proxy)
                if body is None:
                    # 只在这个 base 上失败时才尝试一次其它镜像
                    for alt in BASES:
                        if alt == base:
                            continue
                        body = http_get(alt + path, timeout=timeout, retry=1, proxy=proxy)
                        if body:
                            break
                if valid_feed(body):
                    successful_sources += 1
                else:
                    failed_sources += 1
                got = parse_feed(body or "")
                if not got and q == kw:
                    log.info("关键词「%s」本轮返回 0 条（站点偶发空结果，下轮重试）", kw)
                for it in got:
                    if it["btih"] in all_items:
                        all_items[it["btih"]]["from"].add("search")
                    else:
                        it["from"] = {"search"}
                        all_items[it["btih"]] = it
            time.sleep(0.5)  # 温柔一点，别把站点惹毛了

    # ---- 规则匹配 ----
    skip_trad = s.get("skip_traditional_only", True)
    matched: list[dict] = []
    for ri, rule in enumerate(rules):
        rid = rule_id(rule, ri)
        primary = list(rule.get("group") or [])
        # 兜底：指定字幕组没做这一集（或中途弃番）时，用别的组补上。
        # 判定见 fallback_match() —— 必须标题里带完整番名，免得短关键词误伤别的番。
        use_fallback = bool(primary) and rule.get("fallback_any_group", True)

        for it in all_items.values():
            if not episode_allowed(it.get("episode") or "", int(rule.get("from_episode") or 0)):
                continue
            if match_rule(it, rule):
                fallback = False
            elif use_fallback and fallback_match(it, rule):
                fallback = True
            else:
                continue

            # ⭐ **兜底判定和「字幕组」显示必须一致**（2026-10-17 用户反馈过矛盾文案）：
            #   上面按「标题里有没有你的组名」判兜底，下面按「标题/author 归一后是谁」显示，
            #   两套口径不一致就会出现「说没出、却又显示你的组名」。
            #   只要归一后能认成你指定的组，就一律**不是兜底**。
            if fallback and primary:
                if group_key(it, primary) in primary:
                    fallback = False

            variant = extract_variant(it["title"])
            if skip_trad and is_traditional_only(variant):
                continue                      # 只要繁体的版本直接丢掉
            m = dict(it)
            m["_rule"] = rule
            m["_rid"] = rid
            m["_tier"] = get_variant_tier(it["title"], rule.get("variant_prefer") or [])
            m["_variant"] = variant
            m["_rev"] = get_revision(it["title"])
            m["_seen"] = f"{m['_tier']}:{m['_rev']}"   # 存进 state 的格式
            m["_fallback"] = fallback
            matched.append(m)

    # 同一条目可能被多条规则命中，去重
    uniq: dict[tuple, dict] = {}
    for m in matched:
        key = (m["_rid"], m["btih"])
        if key not in uniq:
            uniq[key] = m
    return health.CollectionResult(uniq.values(), successful_sources, failed_sources)


# --------------------------------------------------------------------------
# 解析失败自查
# --------------------------------------------------------------------------
# 为什么要有这个：标题格式是字幕组自己定的，今天能认出来的写法明天可能就冒出新花样。
# 之前是「等用户看到推送里出现一串哈希，才发现又漏了一种」—— 反馈太慢。
# 现在系统自己记下来，跑 `add_rule.py --parse-report` 就能看到有哪些写法没覆盖，
# 补一条正则就能解决，不用靠人眼扫推送。
UNPARSE_CAP = 60          # 最多记多少条（避免 state 无限膨胀）


def note_unparsed(items: list[dict], st: dict) -> None:
    """把「看起来是正经单集、但集数解析不出来」的标题记进 state。"""
    if not items:
        return
    box = st.get("unparsed")
    if not isinstance(box, dict):
        box = {}
    for it in items:
        key = it["btih"]
        e = box.get(key)
        today = datetime.now(TZ8).strftime("%m-%d")
        if e:
            e["n"] = int(e.get("n", 1)) + 1
            # ⚠️ 每轮都刷新 last 会让 state 每 5 分钟变一次 → 一次提交，
            #    一天 288 次无意义提交。只在跨天时更新。
            if e.get("last", "")[:5] != today:
                e["last"] = datetime.now(TZ8).strftime("%m-%d %H:%M")
        else:
            box[key] = {"title": it["title"][:140],
                        "group": actual_group(it)[:30],
                        "n": 1,
                        "first": datetime.now(TZ8).strftime("%m-%d %H:%M"),
                        "last": datetime.now(TZ8).strftime("%m-%d %H:%M")}
    # 只留最近出现的那些
    items_sorted = sorted(box.items(), key=lambda kv: kv[1].get("last", ""), reverse=True)
    st["unparsed"] = dict(items_sorted[:UNPARSE_CAP])


def sort_key(m: dict):
    """越小越优先：先看字幕形式档位，再看得修订号高的（v2 优于 v1），最后看新的。"""
    return (m.get("_tier", 0), -m.get("_rev", 1),
            -(m["pub_dt"].timestamp() if m.get("pub_dt") else 0))


def best_of(items: list[dict]) -> dict:
    """同一集往往有几个版本。**优先用规则里指定字幕组的**，
    只有当那个组没做这一集（或者不做了）时，才退回用兜底抓来的别的组。"""
    primary = [i for i in items if not i.get("_fallback")]
    pool = primary or items
    pool.sort(key=sort_key)
    return pool[0]


# --------------------------------------------------------------------------
# 一轮处理
# --------------------------------------------------------------------------
def episode_allowed(ep: str, minimum: int) -> bool:
    """Apply the persistent episode floor; retain unknown episode formats."""
    match = re.fullmatch(r"(\d{1,4})(?:-(\d{1,4}))?", ep.strip())
    return minimum <= 0 or match is None or int(match.group(2) or match.group(1)) >= minimum


def enqueue_notification(state: dict, rule_id: str, title: str, body: str) -> str:
    """Persist only message content, never credentials, until delivery succeeds."""
    key = hashlib.sha256((rule_id + "\0" + title + "\0" + body).encode()).hexdigest()
    state.setdefault("pending_notifications", {}).setdefault(key, {
        "rule_id": rule_id, "title": title, "body": body,
    })
    return key


def deliver_pending(state: dict, settings: dict, only: str | None = None,
                    rules: list[dict] | None = None) -> int:
    pending = state.setdefault("pending_notifications", {})
    active = None if rules is None else {r["id"] for r in rules if r.get("enabled", True)}
    sent = attempts = 0
    for key, message in list(pending.items()):
        if only is not None and key != only:
            continue
        if active is not None and message["rule_id"] != "__health__" and message["rule_id"] not in active:
            del pending[key]
            save_state(state)
            continue
        if attempts:
            time.sleep(max(0, int(settings.get("push_min_interval_seconds", 13))))
        attempts += 1
        # Persist the complete outbox before touching the external service.
        save_state(state)
        try:
            ok, _ = send_notification(message["title"], message["body"], settings)
        except Exception:
            log.exception("Queued notification failed; retained for next cycle")
            ok = False
        if ok:
            delivery = message.get("delivery")
            if delivery:
                state.setdefault("deliveries", {})[delivery["state_key"]] = {
                    k: v for k, v in delivery.items() if k != "state_key"
                }
                state["deliveries"][delivery["state_key"]]["time"] = health.now()
                state.setdefault("health", {})["last_delivery"] = health.now()
            del pending[key]
            sent += 1
            # Never wait until the entire batch finishes to checkpoint a receipt.
            save_state(state)
        else:
            log.warning("Queued notification retained for retry")
    return sent


def run_cycle(cfg: dict, state: dict, dry_run: bool = False) -> int:
    settings = cfg["settings"]
    prefix = settings.get("title_prefix") or "【新番速递】"
    first_run = not state.get("initialized")
    mode = (settings.get("first_run_mode") or "latest").lower()

    pending_sent = 0 if dry_run else deliver_pending(state, settings, rules=cfg.get("rules"))
    matched = collect(cfg)
    if not dry_run:
        transition = health.update(state, matched, settings.get("failure_alert_after", 3))
        if transition:
            key = enqueue_notification(state, "__health__", prefix + transition[0], transition[1])
            pending_sent += deliver_pending(state, settings, only=key, rules=cfg.get("rules"))
        if not getattr(matched, "available", True):
            save_state(state)
            log.error("本轮抓取失败，状态基线未推进")
            return pending_sent
    matched = suppress_fallbacks(matched, None if dry_run else state)
    if not dry_run:
        note_unparsed([m for m in matched if not m.get("episode") and not RE_BATCH_HINT.search(m["title"])], state)
    log.info("本轮共命中 %d 条（去重后）", len(matched))

    # 按 (规则, 集数, 字幕组) 分组
    # ⭐ **加了「字幕组」这一维**（2026-10-17）：规则里显式选了多个组时，
    #   同一集要**每个组各推一份**，否则用户选了 N 个组只能拿到 1 个组的数据
    #   （加番回溯那边已经这么改了，定时检查这边漏了）。
    #   组名用 group_key 归一 —— 标题写[绿茶字幕组]、author 是「绿茶发布机」的情况
    #   不归一就会算成两个不同的组。
    groups: dict[tuple, list[dict]] = {}
    for m in matched:
        ep = m.get("episode") or m["btih"]
        rule_groups = [g for g in (m["_rule"].get("group") or []) if not g.endswith("*")]
        gk = group_key(m, rule_groups)
        groups.setdefault((m["_rid"], ep, gk), []).append(m)

    def date_key(m: dict) -> float:
        return -(m["pub_dt"].timestamp() if m.get("pub_dt") else 0)

    def ep_num(ep: str) -> int:
        mm = re.match(r"^(\d+)", ep or "")
        return int(mm.group(1)) if mm else 0

    pushes: list[tuple[dict, bool, str]] = []   # (条目, 是否"更优版本"补推)

    if first_run and mode != "all":
        # 首次运行：每条规则只推"最新一集里最好的那个版本"，用来验证通路；
        # 其余全部记为已读，避免开机狂刷历史消息
        per_rule: dict[str, tuple[int, float, dict]] = {}
        for (rid, ep, _gk), items in groups.items():
            items.sort(key=sort_key)
            best = items[0]
            newest_ts = max((i["pub_dt"].timestamp() if i.get("pub_dt") else 0) for i in items)
            key = (ep_num(ep), newest_ts)
            cur = per_rule.get(rid)
            if cur is None or key > (cur[0], cur[1]):
                per_rule[rid] = (key[0], key[1], best)
        if mode != "silent":
            for rid, v in per_rule.items():
                best = v[2]
                ep = best.get("episode") or best["btih"]
                wanted = [g for g in (best["_rule"].get("group") or []) if not g.endswith("*")]
                gk = group_key(best, wanted)
                pushes.append((best, False, f"{rid}|{ep}|{gk}" if gk else f"{rid}|{ep}"))
    else:
        # 按规则分组收集候选，顺便折叠：本轮如果同时在推「01-12 全集」，
        # 就别再把它涵盖的 05~12 单集也推一遍（同一个番收两条消息很莫名其妙）
        cand_by_rid: dict[str, list[tuple[str, dict, bool]]] = {}
        for (rid, ep, gk), items in groups.items():
            best = best_of(items)
            # 多组规则要按「集数+组」分别记，否则 A 组记过一次，
            # B 组这一集再出就会被当成「见过且没变好」而永远不推。
            sk = f"{rid}|{ep}|{gk}" if gk else f"{rid}|{ep}"
            prev = _seen_pair(state_store.previous_episode(state, rid, ep, gk, group_eq))
            # 两种情况值得打扰你：字幕形式更好了，或者这一集出了修正版（v2 及以后）
            if prev is not None and best["_tier"] >= prev[0] and best["_rev"] <= prev[1]:
                continue
            # prev 存在 = 是"更优版本/修正版"；不存在 = 这是第一次见到这一集
            cand_by_rid.setdefault(rid, []).append((ep, best, prev is not None, sk, gk))

        for cand in cand_by_rid.values():
            # ⭐ **兜底组的资源，只有在这一集没有任何指定组版本时才推**
            #    否则组A 已出了 14 集、兜底的组C 也出了 14 集，
            #    你会收到两条同一集的消息 —— 那条组C 的纯属噪音。
            has_primary = {ep for ep, b, *_ in cand if not b.get("_fallback")}
            cand = [c for c in cand
                    if not c[1].get("_fallback") or c[0] not in has_primary]
            if not cand:
                continue
            # 折叠时按「这一条自己的组」分组 —— cand 里本来就带着 gk，
            # 所以直接按 gk 分桶各自折叠，不要用标题里解析出来的组名
            #（绿茶发布机标题写的是 [绿茶字幕组]，会跟绿茶字幕组混在一起）
            keep: set[int] = set()
            by_g: dict[str, list] = {}
            for ep, b, _up, _sk, gk in cand:
                by_g.setdefault(gk, []).append((ep, b))
            for gk, items_g in by_g.items():
                keep |= {id(x) for _, x in collapse_overlaps(items_g, lambda _it: gk)}
            for ep, best, is_up, sk, gk in cand:
                if id(best) in keep:
                    pushes.append((best, is_up, sk))

    if dry_run:
        for best, is_upgrade, _sk in pushes:
            title, body = build_push_content(best, best["_rule"], prefix, is_upgrade)
            print("\n" + "=" * 70)
            print(title)
            print(body)
        return len(pushes)

    queued = []
    for best, is_upgrade, sk in pushes:
        rule = best["_rule"]
        title, body = build_push_content(best, rule, prefix, is_upgrade)
        key = enqueue_notification(state, best["_rid"], title, body)
        state["pending_notifications"][key]["delivery"] = {
            "state_key": sk, "group": group_key(best, rule.get("group") or []),
            "fallback": bool(best.get("_fallback")), "episode_known": bool(best.get("episode")),
        }
        queued.append(key)

    # Baselines and the corresponding outbox are saved together. A failed send
    # retries its original queued content, even after the item drops out of RSS.
    for (rid, ep, gk), items in groups.items():
        sk = f"{rid}|{ep}|{gk}" if gk else f"{rid}|{ep}"
        best = best_of(items)
        prev = _seen_pair(state_store.previous_episode(state, rid, ep, gk, group_eq))
        state.setdefault("episodes", {})[sk] = (f"{best['_tier']}:{best['_rev']}" if prev is None else
            f"{min(prev[0], best['_tier'])}:{max(prev[1], best['_rev'])}")
    state["initialized"] = True
    save_state(state)

    sent = pending_sent
    gap = max(0, int(settings.get("push_min_interval_seconds", 13)))
    for idx, key in enumerate(queued):
        if (idx > 0 or pending_sent) and gap:
            time.sleep(gap)
        sent += deliver_pending(state, settings, only=key, rules=cfg.get("rules"))
    return sent


# --------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------
def setup_logging(verbose: bool) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    fh = RotatingFileHandler(os.path.join(LOG_DIR, "watcher.log"),
                             maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)


def setup_stdout() -> None:
    """控制台跟随系统码页（避免乱码），管道/重定向则用 UTF-8。
    再加 errors='replace'，保证遇到控制台编不出来的字符也不会崩。"""
    try:
        if sys.stdout.isatty():
            sys.stdout.reconfigure(errors="replace")
        else:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    setup_stdout()
    ap = argparse.ArgumentParser(description="ACGNX 新番发布实时监听器")
    ap.add_argument("--once", action="store_true", help="只跑一轮就退出")
    ap.add_argument("--dry-run", action="store_true", help="只打印命中，不推送、不写状态")
    ap.add_argument("--list", action="store_true", help="列出当前命中的最新条目（校准规则用）")
    ap.add_argument("--test", action="store_true", help="发一条测试消息到 Webhook")
    ap.add_argument("--reset-state", action="store_true", help="清空去重记录后退出")
    ap.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    args = ap.parse_args()

    setup_logging(args.verbose)
    cfg = load_config()

    if args.reset_state:
        # Explicit reset updates both primary and backup so a backup cannot resurrect it.
        save_state({"episodes": {}, "initialized": False})
        save_state({"episodes": {}, "initialized": False})
        log.info("已清空去重记录：%s", STATE_PATH)
        return

    if args.test:
        s = cfg["settings"]
        title = f"{s.get('title_prefix') or '【新番速递】'}推送通路正常（这是一条演示）"
        # 直接用真实推送的排版函数拼一条样例，这样看到的就是将来真正收到的样子
        demo = {
            "title": "[喵萌奶茶屋] 学生会也有洞！ [03][WebRip 1080p HEVC AAC][简日内嵌]",
            "author": "喵萌奶茶屋", "_variant": "简日内嵌", "_rev": 1,
            "episode": "03", "size": "1.21 GB", "magnet": "magnet:?xt=urn:btih:888df506fbd9db6b32e5d332718b561759ab01a1",
            "link": "https://share.acgnx.cc/announce/888df506fbd9db6b32e5d332718b561759ab01a1",
            "_fallback": False,
        }
        _, demo_body = build_push_content(demo, {
            "name": "学生会有洞（喵萌奶茶屋）",
            "group": ["喵萌奶茶屋", "nekomoekissaten"],
        }, "")
        # 演示尾巴只保留一句提醒。之前还附「已配置的渠道 / 强制指定」，
        # 那是指令行（feishu_app 只在内存里有），只有开发时看，推给你没意义（2026-10-16 用户反馈）。
        body = ("👇 上面就是将来真正推送时的样子（磁力是假的，别下）\n\n"
                + demo_body)
        ok, _mid = send_notification(title, body, s)
        print("测试消息发送" + ("成功 ✅" if ok else "失败 ❌（检查 config.json 里的 token / SendKey）"))
        return

    if args.list:
        matched = collect(cfg)
        matched.sort(key=sort_key)
        print(f"\n当前命中 {len(matched)} 条：\n" + "-" * 78)
        for m in matched[:60]:
            dt = f"{m['pub_dt']:%m-%d %H:%M}" if m.get("pub_dt") else "??"
            print(f"[{m['_rule'].get('name', '?')}] {dt} tier={m['_tier']}")
            print(f"    {m['title'][:96]}")
            print(f"    组: {m['author']} | 大小: {m['size'] or '?'} | 集: {m['episode'] or '-'}")
            print(f"    {m['magnet'][:110]}")
        print("-" * 78)
        return

    state = load_state()

    if args.once:
        n = run_cycle(cfg, state, dry_run=args.dry_run)
        log.info("本轮结束，推送 %d 条", n)
        if not args.dry_run and state.get("health", {}).get("status") == "failed":
            raise SystemExit(3)
        return

    interval = max(1, int(cfg["settings"].get("interval_minutes", 5)))
    jitter = int(cfg["settings"].get("poll_jitter_seconds", 20))
    log.info("启动监听：每 %d 分钟检测一次（Ctrl+C 停止）", interval)

    # 首次运行做基线，避免开机狂刷历史消息
    if not state.get("initialized"):
        run_cycle(cfg, state, dry_run=args.dry_run)

    while True:
        try:
            time.sleep(interval * 60 + random.randint(0, max(0, jitter)))
            run_cycle(cfg, state, dry_run=args.dry_run)
        except KeyboardInterrupt:
            log.info("收到停止信号，退出。")
            break
        except Exception as e:  # noqa: BLE001
            log.exception("本轮异常：%s", e)
            time.sleep(60)


if __name__ == "__main__":
    main()


