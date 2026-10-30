#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ACGNX 新番发布实时监听器
=========================================================================
数据源：末日動漫資源庫 (Project AcgnX Torrent)
    最新发布流   https://share.acgnx.cc/rss.xml
    关键词搜索   https://share.acgnx.cc/rss.xml?keyword=xxx

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
from watcher_core import state_store, health, fallback_wait, release_identity, outbox_policy, remote_checkpoint
from watcher_core.send_budget import SendBudget
from watcher_core.parsing import *  # Backward-compatible public parser API
from watcher_core.matching import *
from watcher_core.notifications import *
from watcher_core.network import http_get, pick_base


import argparse
import copy
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
    remote_checkpoint.publish()


# HTTP
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# RSS 解析
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# 推送
# --------------------------------------------------------------------------
def build_push_content(item: dict, rule: dict, prefix: str, upgrade: bool=False) -> tuple[str, str]:
    from watcher_core import presentation as _impl
    return _impl.build_push_content(item, rule, prefix, upgrade, api=sys.modules[__name__])


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
    from watcher_core import collection as _impl
    return _impl.collect(cfg, api=sys.modules[__name__])


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
    from watcher_core import outbox as _impl
    return _impl.enqueue_notification(state, rule_id, title, body, api=sys.modules[__name__])


def deliver_pending(state: dict, settings: dict, only: str | None=None, rules: list[dict] | None=None, budget=None) -> int:
    from watcher_core import outbox as _impl
    return _impl.deliver_pending(state, settings, only, rules, budget, api=sys.modules[__name__])


def run_cycle(cfg: dict, state: dict, dry_run: bool=False) -> int:
    from watcher_core import cycle as _impl
    return _impl.run_cycle(cfg, state, dry_run, api=sys.modules[__name__])


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


