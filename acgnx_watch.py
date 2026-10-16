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
# --------------------------------------------------------------------------
_T2S_PAIRS = (
    "學学會会組组綠绿戰战國国语语動动畫画劇剧場场樂乐點点實实東东灣湾臺台區区體体產产專专业业"
    "開开關关門门問问間间時时現现對对这这來来個个們们為为與与說说话话聽听見见覺觉愛爱戀恋夢梦"
    "聲声線线級级號号題题準准確确認认識识記记錄录報报變变樣样種种數数電电無无萬万蒼苍藍蓝紅红"
    "黃黄貓猫馬马鳥鸟魚鱼龍龙風风云云陽阳陰阴劍剑槍枪軍军將将師师傳传傷伤聖圣復复獨独員员銀银"
    "鐵铁鏡镜島岛鄉乡約约純纯網网絡络標标選选擇择錯错誤误鍵键藥药醫医護护書书讀读写写課课習习"
    "試试验验檢检測测設设備备裝装載载訊讯頻频視视導导監监編编響响訪访談谈華华亞亚歐欧韓韩蘇苏"
    "羅罗蘭兰薩萨爾尔賴赖買买賣卖費费貴贵價价財财貨货賺赚贏赢輸输賽赛敗败勝胜奪夺擊击殺杀滅灭"
    "絕绝斷断續续結结終终當当權权勢势態态條条範范圍围繞绕轉转换换適适應应該该讓让給给尋寻幫帮"
    "於于並并麼么蓋盖燈灯麗丽靈灵輝辉爛烂穩稳積积極极藝艺术术慣惯憶忆願愿驚惊歡欢慶庆賀贺憂忧"
    "憤愤憐怜惡恶醜丑賠赔贈赠貸贷帳账單单據据證证憑凭檔档頁页鏈链籤签譯译辭辞詞词詩诗講讲評评"
    "論论議议詢询謎谜診诊療疗瘋疯癢痒發发臟脏腦脑腸肠膽胆膚肤臉脸頸颈髮发齒齿齡龄龜龟傑杰屬属"
    "慘惨還还進进遠远邊边過过達达運运連连週周遲迟遺遗舉举興兴舊旧潛潜陣阵隊队階阶際际隨随隱隐"
    "難难雖虽雙双雜杂離离預预領领头头顧顾顯显類类飛飞館馆駛驶騎骑鬥斗麥麦遊游戏戏襲袭墮堕戰战"
    "藝術术語语語语麼么樹树檔档總总縮缩簡简繁繁曆历歷历層层屬属麼么縱纵橫横縱纵編编織织纖纤"
    "細细純纯終终綁绑縫缝續续紅红級级約约給给統统絲丝綠绿維维綱纲緊紧緒绪線线緣缘編编緩缓練练"
    "縱纵總总績绩繁繁織织繞绕繡绣繼继續续纏缠纜缆纏缠罰罚罷罢羅罗羈羁羨羡義义習习翹翘翻翻"
    "聯联聰聪聲声職职聽听肅肃腎肾脅胁脈脉腳脚脫脱腦脑腫肿腸肠膚肤臉脸臘腊臨临舉举舊旧艙舱"
    "艱艰色色花花卉卉華华萬万藍蓝藥药處处號号號号蟄蛰蟲虫術术衛卫衝冲衣衣補补裝装製制複复"
    "見见規规視视親亲覺觉觀观計计討讨訓训訊讯記记訪访設设許许訴诉診诊註注評评詞词試试詩诗"
    "話话該该詳详誇夸誌志認认語语誠诚誤误說说課课誰谁調调談谈請请諒谅論论諷讽講讲謝谢證证"
    "識识譜谱警警譯译議议護护讀读變变讓让讚赞貝贝貞贞負负財财貢贡貧贫貨货販贩責责貴贵買买"
    "貸贷貿贸費费貼贴賀贺資资賊贼賓宾賞赏賣卖賤贱賦赋質质賬账賭赌賴赖購购賽赛贈赠贊赞贏赢"
    "贓赃贖赎贛赣赤赤走走过过趕赶趙赵跡迹踐践身身車车軌轨軍军軒轩軟软較较載载輔辅輕轻輛辆"
    "輝辉輩辈輪轮輸输轄辖轉转轎轿轟轰農农迴回遊游運运過过達达違违遠远遜逊遞递適适選选遺遗"
    "邁迈還还邊边邏逻釀酿釋释里里量量金金針针釣钓鈔钞鈴铃銀银銅铜銘铭錢钱鋼钢錄录錦锦鍵键"
    "鐘钟鐵铁鑄铸鑑鉴長长門门閉闭開开閒闲間间關关闡阐防防陽阳陰阴陣阵陸陆陽阳隊队階阶隨随"
    "隱隐雖虽雙双雛雏雜杂雞鸡離离難难雲云電电需需靜静非非面面鞠鞠韌韧韓韩頁页頂顶項项順顺"
    "須须預预頑顽頒颁頓顿頗颇領领頭头頰颊頸颈頻频顆颗題题顏颜願愿類类顧顾顯显風风飛飞飾饰"
    "飽饱餅饼養养餐餐餘余館馆首首馬马騎骑驚惊骨骨體体高高髮发鬥斗魯鲁鮮鲜鳥鸟鳳凤鳴鸣鴻鸿"
    "鵬鹏鶴鹤麗丽麥麦黃黄黑黑點点黨党鼓鼓鼠鼠齒齿齡龄龍龙龜龟"
)

_T2S_TABLE = {}
for _i in range(0, len(_T2S_PAIRS) - 1, 2):
    _t, _s = _T2S_PAIRS[_i], _T2S_PAIRS[_i + 1]
    if _t != _s:
        _T2S_TABLE[ord(_t)] = _s


def norm(s: str) -> str:
    """归一化：转小写 + 繁转简 + 去掉所有空白。用于规则匹配。"""
    if not s:
        return ""
    s = s.lower().translate(_T2S_TABLE)
    return re.sub(r"[\s\u3000]+", "", s)


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
    }
    for field, names in env_map.items():
        for n in names:
            v = (os.environ.get(n) or "").strip()
            if v:
                merged["settings"][field] = v
                break
    return merged


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                st = json.load(f)
            st.setdefault("episodes", {})
            st.setdefault("initialized", False)
            st.pop("seen", None)      # 老版本留下的冗余字段，清掉（没被任何逻辑使用）
            return st
        except Exception as e:
            log.warning("状态文件损坏，已重建：%s", e)
    return {"episodes": {}, "initialized": False}


def save_state(st: dict) -> None:
    st.pop("seen", None)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def http_get(url: str, timeout: int = 20, retry: int = 3, proxy: str = "") -> str | None:
    last_err = None
    for attempt in range(1, retry + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "close",
            })
            if proxy:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
            else:
                opener = urllib.request.build_opener()
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read()
                enc = (resp.headers.get("Content-Encoding") or "").lower()
                if "gzip" in enc:
                    raw = gzip.decompress(raw)
                elif "deflate" in enc:
                    try:
                        raw = zlib.decompress(raw)
                    except zlib.error:
                        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                return raw.decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < retry:
                time.sleep(1.5 * attempt)
    log.warning("请求失败(%s)：%s | %s", retry, url, last_err)
    return None


# --------------------------------------------------------------------------
# RSS 解析
# --------------------------------------------------------------------------
RE_SIZE = re.compile(r"\|\s*([0-9.]+\s*[KMGTP]?i?B)\s*\|", re.I)
RE_BTIH = re.compile(r"urn:btih:([0-9a-zA-Z]{32,40})", re.I)

EP_PATTERNS = [
    re.compile(r"\[(\d{1,3})(?:v\d)?\]"),                    # [02] [01v2]
    re.compile(r"\[(\d{1,3})-(\d{1,3})\]"),                  # [01-26] 合集
    re.compile(r"[-–—]\s*(\d{1,3})(?:v\d)?\s*(?:[\[\(（]|$)"),  # - 03 [
    re.compile(r"\bS\d{1,2}E(\d{1,2})\b", re.I),             # S01E03
]


def extract_episode(title: str) -> str:
    """从标题里抽出集数标识，用于把同一集的不同版本归并成一组。"""
    m = EP_PATTERNS[1].search(title)
    if m:
        return f"{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    for pat in (EP_PATTERNS[0], EP_PATTERNS[2], EP_PATTERNS[3]):
        m = pat.search(title)
        if m:
            return f"{int(m.group(1)):02d}"
    return ""


def parse_feed(xml_text: str) -> list[dict]:
    items: list[dict] = []
    if not xml_text or "<item" not in xml_text:
        return items
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError as e:
        log.error("RSS 解析失败：%s", e)
        return items

    for node in root.iter("item"):
        def txt(tag: str) -> str:
            el = node.find(tag)
            if el is None or el.text is None:
                return ""
            return html.unescape(el.text).strip()

        title = txt("title")
        link = txt("link")
        author = txt("author")
        desc = txt("description")
        pub_raw = txt("pubDate")
        cat_el = node.find("category")
        category = html.unescape((cat_el.text or "").strip()) if cat_el is not None and cat_el.text else ""

        magnet = ""
        enc = node.find("enclosure")
        if enc is not None:
            magnet = html.unescape((enc.get("url") or "").strip())

        size = ""
        m = RE_SIZE.search(desc)
        if m:
            size = m.group(1).replace(" ", "")

        btih = ""
        m2 = RE_BTIH.search(magnet) or RE_BTIH.search(desc) or RE_BTIH.search(link)
        if m2:
            btih = m2.group(1).lower()

        when = None
        if pub_raw:
            try:
                when = parsedate_to_datetime(pub_raw).astimezone(TZ8)
            except Exception:  # noqa: BLE001
                when = None

        items.append({
            "title": title,
            "link": link,
            "author": author,
            "category": category,
            "size": size,
            "magnet": magnet,
            "btih": btih or hashlib.md5(title.encode("utf-8")).hexdigest(),
            "pub_dt": when,
            "pub_raw": pub_raw,
            "episode": extract_episode(title),
        })
    return items


# --------------------------------------------------------------------------
# 规则匹配
# --------------------------------------------------------------------------
def get_variant_tier(title: str, prefer: list[str]) -> int:
    """返回标题在偏好列表里的优先级档位，越小越优先；都没命中返回 999。"""
    if not prefer:
        return 0
    nt = norm(title)
    for idx, p in enumerate(prefer):
        if norm(p) in nt:
            return idx
    return 999


def match_rule(item: dict, rule: dict) -> bool:
    nt = norm(item["title"])
    na = norm(item["author"])

    inc = rule.get("include_any") or []
    if inc and not any(norm(k) in nt for k in inc):
        return False

    exc = rule.get("exclude_any") or []
    if exc and any(norm(k) in nt for k in exc):
        return False

    grp = rule.get("group") or []
    if grp and not any(norm(k) in nt or norm(k) in na for k in grp):
        return False

    return True


def rule_id(rule: dict, idx: int) -> str:
    return rule.get("id") or f"rule{idx}"


# --------------------------------------------------------------------------
# 推送
# --------------------------------------------------------------------------
def build_push_content(item: dict, rule: dict, prefix: str,
                       upgrade: bool = False) -> tuple[str, str]:
    """返回 (标题, 正文)。
    标题保证单行 —— Server酱 / PushPlus 的标题都不允许出现换行。"""
    name = rule.get("name") or item["title"][:60]
    tag = "（更优版本）" if upgrade else ""
    title = f"{prefix}{tag}{name}"[:120]

    lines = [f"标题：{item['title']}"]
    if rule.get("group"):
        lines.append(f"字幕组：{'/'.join(rule['group'])}")
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
def _post_json(url: str, payload: dict, timeout: int = 15) -> str:
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": UA,
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            return e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return f"ERROR {e}"


def _post_form(url: str, fields: dict, timeout: int = 15) -> str:
    try:
        data = urllib.parse.urlencode(fields).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "User-Agent": UA,
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            return e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return f"ERROR {e}"


def truncate_utf8(text: str, limit_bytes: int) -> str:
    """按 UTF-8 字节数截断（各平台限的是字节数，不是字符数）。
    中文一个字 3 字节，所以不能按字符数算。"""
    raw = text.encode("utf-8")
    if len(raw) <= limit_bytes:
        return text
    cut = raw[:max(0, limit_bytes - 60)].decode("utf-8", "ignore")
    return cut + "\n…（内容过长，已截断。完整列表请到 ACGNX 搜索）"


# --------------------------------------------------------------------------
# 渠道一：PushPlus（推送到个人微信，免费 200 条/天）
#   注册 https://www.pushplus.plus → 微信扫码 → 实名 → 复制 token
# --------------------------------------------------------------------------
def _send_pushplus(title: str, body: str, s: dict) -> bool:
    token = (s.get("pushplus_token") or "").strip()
    resp = _post_json("https://www.pushplus.plus/send", {
        "token": token,
        "title": title,
        "content": f"{title}\n\n{body}",
        "template": "txt",          # txt 模板原样保留换行，磁力最好复制
    })
    try:
        code = int(json.loads(resp).get("code", -1))
    except Exception:  # noqa: BLE001
        code = -1
    if code == 200:
        log.info("PushPlus 推送成功")
        return True
    hints = {
        903: "token 无效 —— 去 pushplus.plus「一对一推送」页面重新复制",
        905: "还没实名认证 —— 去 verify.pushplus.plus 验一下手机号",
        999: "标题里有换行符（不该出现，请把这条日志发我）",
    }
    log.error("PushPlus 推送失败 code=%s | %s%s", code, resp[:200],
              ("  ← " + hints[code]) if code in hints else "")
    return False


# --------------------------------------------------------------------------
# 渠道二：Server酱（推送到个人微信，免费只有 5 条/天）
#   注册 https://sct.ftqq.com → 微信扫码 → 复制 SendKey
#   Server酱³ 的 SendKey 以 sctp 开头，端点跟老版不一样
# --------------------------------------------------------------------------
def serverchan_url(key: str) -> str:
    m = re.match(r"sctp(\d+)t", key, re.I)
    if m:
        return f"https://{m.group(1)}.push.ft07.com/send/{key}.send"
    return f"https://sctapi.ftqq.com/{key}.send"


def _send_serverchan(title: str, body: str, s: dict) -> bool:
    key = (s.get("serverchan_sendkey") or "").strip()
    # desp 按 Markdown 渲染，行尾两个空格 = 强制换行，免得全挤成一坨
    desp = "  \n".join(body.splitlines())
    resp = _post_form(serverchan_url(key), {"title": title, "desp": desp})
    try:
        code = int(json.loads(resp).get("code", -1))
    except Exception:  # noqa: BLE001
        code = -1
    if code == 0:
        log.info("Server酱 推送成功")
        return True
    log.error("Server酱 推送失败 code=%s | %s", code, resp[:200])
    return False


# --------------------------------------------------------------------------
# 渠道三：群机器人（企业微信 / 钉钉 / 飞书），按 webhook 域名自动识别
# --------------------------------------------------------------------------
def _webhook_result(resp: str) -> tuple[bool, str]:
    """三个平台的返回格式各不相同，统一解析。
       钉钉/企业微信：{"errcode":0,"errmsg":"ok"}
       飞书：        {"StatusCode":0,...,"code":0,"msg":"success"}"""
    try:
        j = json.loads(resp)
    except Exception:  # noqa: BLE001
        return False, f"返回不是 JSON：{resp[:200]}"
    if not isinstance(j, dict):
        return False, resp[:200]
    for key in ("errcode", "code", "StatusCode", "status_code"):
        if key in j:
            if str(j.get(key)) == "0":
                return True, ""
            msg = j.get("errmsg") or j.get("msg") or j.get("StatusMessage") or ""
            return False, f"{key}={j.get(key)} {msg}".strip()
    return False, resp[:200]


def _send_webhook(title: str, body: str, s: dict) -> bool:
    url = (s.get("webhook_url") or "").strip()
    text = f"{title}\n{body}"
    host = (urllib.parse.urlparse(url).hostname or "").lower()

    if "dingtalk" in host:
        content = f"{s.get('dingtalk_keyword') or ''} {text}".strip()
        payload = {"msgtype": "text",
                   "text": {"content": truncate_utf8(content, 19000)}}
        secret = (s.get("dingtalk_secret") or "").strip()
        if secret:
            # 钉钉：key=secret, message="timestamp\nsecret"，签名放 URL 上
            ts = str(round(time.time() * 1000))
            digest = hmac.new(secret.encode("utf-8"),
                              f"{ts}\n{secret}".encode("utf-8"),
                              hashlib.sha256).digest()
            sign = urllib.parse.quote_plus(base64.b64encode(digest))
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}timestamp={ts}&sign={sign}"

    elif "feishu" in host or "larksuite" in host:
        # 飞书请求体上限 20 KB，留点余量按 18 KB 截断
        payload = {"msg_type": "text",
                   "content": {"text": truncate_utf8(text, 18000)}}
        secret = (s.get("feishu_secret") or "").strip()
        if secret:
            # ⚠️ 飞书的算法和钉钉不一样：
            #    把 "timestamp\nsecret" 当作 HMAC 的 *密钥*，*消息体为空*，
            #    结果放 body 里，而且 timestamp 是「秒」不是毫秒。
            ts = str(int(time.time()))
            digest = hmac.new(f"{ts}\n{secret}".encode("utf-8"),
                              msg=b"", digestmod=hashlib.sha256).digest()
            payload["timestamp"] = ts
            payload["sign"] = base64.b64encode(digest).decode("utf-8")

    elif "qyapi.weixin.qq.com" in host:
        # 企业微信文本消息上限 2048 字节
        payload = {"msgtype": "text",
                   "text": {"content": truncate_utf8(text, 1900)}}

    else:
        payload = {"msgtype": "text",
                   "text": {"content": truncate_utf8(text, 1900)}}

    resp = _post_json(url, payload)
    ok, detail = _webhook_result(resp)
    if ok:
        log.info("群机器人推送成功 -> %s", host)
    else:
        hint = ""
        if "sign" in detail.lower() or "19021" in detail:
            hint = "  ← 签名校验失败：检查 feishu_secret / dingtalk_secret 是否填对"
        elif "310000" in detail:
            hint = "  ← 钉钉关键词不匹配：把机器人关键词设成 dingtalk_keyword 里的词"
        elif "9499" in detail:
            hint = "  ← 飞书说请求体格式错误（不该出现，把这条日志发我）"
        elif "19001" in detail or "19024" in detail:
            hint = "  ← 飞书说 webhook 地址里的 token 无效，重新复制一下地址"
        log.error("群机器人推送失败 -> %s | %s%s", host, detail[:200], hint)
    return ok


CHANNEL_FIELDS = {
    "pushplus": "pushplus_token",
    "serverchan": "serverchan_sendkey",
    "webhook": "webhook_url",
}
CHANNEL_LABEL = {
    "pushplus": "PushPlus（微信）",
    "serverchan": "Server酱（微信）",
    "webhook": "群机器人（企业微信/钉钉/飞书）",
}
CHANNEL_ORDER = ["pushplus", "serverchan", "webhook"]
CHANNEL_SENDER = {
    "pushplus": _send_pushplus,
    "serverchan": _send_serverchan,
    "webhook": _send_webhook,
}


def available_channels(s: dict) -> list[str]:
    return [ch for ch in CHANNEL_ORDER if (s.get(CHANNEL_FIELDS[ch]) or "").strip()]


def send_notification(title: str, body: str, s: dict) -> bool:
    """按 push_channel 推送。auto = 按 PushPlus → Server酱 → 群机器人 挑第一个配好的。"""
    channel = (s.get("push_channel") or "auto").lower()
    order = CHANNEL_ORDER if channel == "auto" else [channel]

    targets = [ch for ch in order if ch in CHANNEL_SENDER and (s.get(CHANNEL_FIELDS[ch]) or "").strip()]
    if not targets:
        log.warning("没有配置任何推送渠道，本条消息只打印到控制台：\n%s\n%s\n%s",
                    title, body, "-" * 60)
        return False

    for ch in targets:
        if CHANNEL_SENDER[ch](title, body, s):
            return True
        log.warning("渠道「%s」没成功，尝试下一个…", CHANNEL_LABEL.get(ch, ch))
    return False


# --------------------------------------------------------------------------
# 抓取
# --------------------------------------------------------------------------
def pick_base(cfg: dict) -> tuple[str | None, str | None]:
    """用 /rss.xml 探活，挑出当前可用的镜像，顺带把这一份结果复用掉。
    返回 (生效的 base, 该 base 的最新发布 RSS 内容)。"""
    s = cfg["settings"]
    timeout = int(s.get("request_timeout", 15))
    retry = int(s.get("retry", 2))
    proxy = (s.get("proxy") or "").strip()
    for base in BASES:
        body = http_get(base + "/rss.xml", timeout=timeout, retry=retry, proxy=proxy)
        if body and "<rss" in body:
            return base, body
        log.info("镜像探活失败，降级下一个：%s", base)
    return None, None


def collect(cfg: dict) -> list[dict]:
    """跑一轮抓取，返回所有命中的条目（已标注规则）。"""
    s = cfg["settings"]
    rules = [r for r in cfg["rules"] if r.get("enabled", True)]
    base, feed_body = pick_base(cfg)
    if base is None:
        log.error("所有镜像都不可用，本轮跳过（下轮会重试）")
        return []

    timeout = int(s.get("request_timeout", 15))
    retry = int(s.get("retry", 2))
    proxy = (s.get("proxy") or "").strip()
    all_items: dict[str, dict] = {}

    # ---- 路径 1：全站最新流（主路径，保证不漏） ----
    if s.get("watch_global_feed", True) and feed_body:
        for it in parse_feed(feed_body):
            it["from"] = {"feed"}
            all_items[it["btih"]] = it

    # ---- 路径 2：每条规则的关键词搜索（补漏 / 电脑睡过一觉后回溯） ----
    seen_kw: set[str] = set()
    for rule in rules:
        for kw in (rule.get("keywords") or []):
            if kw in seen_kw:
                continue
            seen_kw.add(kw)
            path = "/topics/rss/rss.xml?keyword=" + urllib.parse.quote(kw)
            body = http_get(base + path, timeout=timeout, retry=retry, proxy=proxy)
            if body is None:
                # 只在这个 base 上失败时才尝试一次其它镜像
                for alt in BASES:
                    if alt == base:
                        continue
                    body = http_get(alt + path, timeout=timeout, retry=1, proxy=proxy)
                    if body:
                        break
            got = parse_feed(body or "")
            if not got:
                log.info("关键词「%s」本轮返回 0 条（站点偶发空结果，下轮重试）", kw)
            for it in got:
                if it["btih"] in all_items:
                    all_items[it["btih"]]["from"].add("search")
                else:
                    it["from"] = {"search"}
                    all_items[it["btih"]] = it
            time.sleep(0.5)  # 温柔一点，别把站点惹毛了

    # ---- 规则匹配 ----
    matched: list[dict] = []
    for ri, rule in enumerate(rules):
        rid = rule_id(rule, ri)
        for it in all_items.values():
            if match_rule(it, rule):
                m = dict(it)
                m["_rule"] = rule
                m["_rid"] = rid
                m["_tier"] = get_variant_tier(it["title"], rule.get("variant_prefer") or [])
                matched.append(m)

    # 同一条目可能被多条规则命中，去重
    uniq: dict[tuple, dict] = {}
    for m in matched:
        key = (m["_rid"], m["btih"])
        if key not in uniq:
            uniq[key] = m
    return list(uniq.values())


def sort_key(m: dict):
    return (m["_tier"], -(m["pub_dt"].timestamp() if m.get("pub_dt") else 0))


# --------------------------------------------------------------------------
# 一轮处理
# --------------------------------------------------------------------------
def run_cycle(cfg: dict, state: dict, dry_run: bool = False) -> int:
    settings = cfg["settings"]
    prefix = settings.get("title_prefix") or "【新番速递】"
    first_run = not state.get("initialized")
    mode = (settings.get("first_run_mode") or "latest").lower()

    matched = collect(cfg)
    log.info("本轮共命中 %d 条（去重后）", len(matched))

    # 按 (规则, 集数) 分组，同一集的不同字幕版本归到一组
    groups: dict[tuple, list[dict]] = {}
    for m in matched:
        ep = m.get("episode") or m["btih"]
        groups.setdefault((m["_rid"], ep), []).append(m)

    def date_key(m: dict) -> float:
        return -(m["pub_dt"].timestamp() if m.get("pub_dt") else 0)

    def ep_num(ep: str) -> int:
        mm = re.match(r"^(\d+)", ep or "")
        return int(mm.group(1)) if mm else 0

    pushes: list[tuple[dict, bool]] = []   # (条目, 是否"更优版本"补推)

    if first_run and mode != "all":
        # 首次运行：每条规则只推"最新一集里最好的那个版本"，用来验证通路；
        # 其余全部记为已读，避免开机狂刷历史消息
        per_rule: dict[str, tuple[int, float, dict]] = {}
        for (rid, ep), items in groups.items():
            items.sort(key=sort_key)
            best = items[0]
            newest_ts = max((i["pub_dt"].timestamp() if i.get("pub_dt") else 0) for i in items)
            key = (ep_num(ep), newest_ts)
            cur = per_rule.get(rid)
            if cur is None or key > (cur[0], cur[1]):
                per_rule[rid] = (key[0], key[1], best)
        if mode != "silent":
            pushes = [(v[2], False) for v in per_rule.values()]
    else:
        for (rid, ep), items in groups.items():
            items.sort(key=sort_key)
            best = items[0]
            prev_tier = state["episodes"].get(f"{rid}|{ep}")
            if prev_tier is not None and best["_tier"] >= prev_tier:
                continue          # 已经推过同等或更好的版本，不重复打扰
            pushes.append((best, prev_tier is not None))

    sent = 0
    gap = max(0, int(settings.get("push_min_interval_seconds", 13)))
    for idx, (best, is_upgrade) in enumerate(pushes):
        rule = best["_rule"]
        title, body = build_push_content(best, rule, prefix, is_upgrade)
        log.info("%s -> %s", "补推" if is_upgrade else "命中", best["title"])
        if dry_run:
            print("\n" + "=" * 70)
            print(title)
            print(body)
        else:
            if idx > 0 and gap:
                # PushPlus 消息接口限「1 分钟最多 5 次」，集中补推时把请求摊开
                log.info("等待 %d 秒再发下一条（避开平台频率限制）…", gap)
                time.sleep(gap)
            send_notification(title, body, settings)
        sent += 1

    if not dry_run:
        # 给每个 (规则, 集数) 记下当前最优档位，等更优版本出现再补推
        for (rid, ep), items in groups.items():
            items.sort(key=sort_key)
            state["episodes"].setdefault(f"{rid}|{ep}", items[0]["_tier"])
        state["initialized"] = True
        save_state(state)

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
        if os.path.exists(STATE_PATH):
            os.remove(STATE_PATH)
        log.info("已清空去重记录：%s", STATE_PATH)
        return

    if args.test:
        s = cfg["settings"]
        chans = available_channels(s)
        title = f"{s.get('title_prefix') or '【新番速递】'}这是一条测试消息"
        body = ("如果你在微信上看到它，说明推送通路已经打通 ✅\n"
                f"已配置的渠道：{'、'.join(CHANNEL_LABEL[c] for c in chans) if chans else '（一个都没配）'}\n"
                f"强制指定：{s.get('push_channel')}\n"
                f"时间：{datetime.now(TZ8):%Y-%m-%d %H:%M:%S}")
        ok = send_notification(title, body, s)
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
