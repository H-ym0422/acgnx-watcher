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
#
#   ⚠️ 每一项都写成「繁简」两个字。之前这里是一长串字符按位置两两配对，
#      只要有一处错位后面全乱（出过「风→飛」「绿→維」这种事故，直接毁掉繁简匹配）。
#      两个字一组的写法从根上杜绝错位，模块加载时还会做一致性检查。
# --------------------------------------------------------------------------
_T2S_PAIRS = [
    # —— 高频常用字 ——
    "學学", "會会", "組组", "綠绿", "戰战", "國国", "語语", "動动", "畫画", "劇剧",
    "場场", "樂乐", "點点", "實实", "東东", "灣湾", "臺台", "區区", "體体", "產产",
    "專专", "業业", "開开", "關关", "門门", "問问", "間间", "時时", "現现", "對对",
    "這这", "來来", "個个", "們们", "為为", "與与", "說说", "話话", "聽听", "見见",
    "覺觉", "愛爱", "戀恋", "夢梦", "聲声", "線线", "級级", "號号", "題题", "準准",
    "確确", "認认", "識识", "記记", "錄录", "報报", "變变", "樣样", "種种", "數数",
    "電电", "無无", "萬万", "蒼苍", "藍蓝", "紅红", "黃黄", "貓猫", "馬马", "鳥鸟",
    "魚鱼", "龍龙", "風风", "雲云", "陽阳", "陰阴", "劍剑", "槍枪", "軍军", "將将",
    "師师", "傳传", "傷伤", "聖圣", "復复", "獨独", "員员", "銀银", "鐵铁", "鏡镜",
    "島岛", "鄉乡", "約约", "純纯", "網网", "絡络", "標标", "選选", "擇择", "錯错",
    "誤误", "鍵键", "藥药", "醫医", "護护", "書书", "讀读", "寫写", "課课", "習习",
    "試试", "驗验", "檢检", "測测", "設设", "備备", "裝装", "載载", "訊讯", "頻频",
    "視视", "導导", "監监", "編编", "響响", "訪访", "談谈", "華华", "亞亚", "歐欧",
    "韓韩", "蘇苏", "羅罗", "蘭兰", "薩萨", "爾尔", "賴赖", "買买", "賣卖", "費费",
    "貴贵", "價价", "財财", "貨货", "賺赚", "贏赢", "輸输", "賽赛", "敗败", "勝胜",
    "奪夺", "擊击", "殺杀", "滅灭", "絕绝", "斷断", "續续", "結结", "終终", "當当",
    "權权", "勢势", "態态", "條条", "範范", "圍围", "繞绕", "轉转", "換换", "適适",
    "應应", "該该", "讓让", "給给", "尋寻", "幫帮", "於于", "並并", "麼么", "蓋盖",
    "燈灯", "麗丽", "靈灵", "輝辉", "爛烂", "穩稳", "積积", "極极", "藝艺", "術术",
    "慣惯", "憶忆", "願愿", "驚惊", "歡欢", "慶庆", "賀贺", "憂忧", "憤愤", "憐怜",
    "惡恶", "醜丑", "賠赔", "贈赠", "貸贷", "帳账", "單单", "據据", "證证", "憑凭",
    "檔档", "頁页", "鏈链", "籤签", "譯译", "辭辞", "詞词", "詩诗", "講讲", "評评",
    "論论", "議议", "詢询", "謎谜", "診诊", "療疗", "瘋疯", "癢痒", "發发", "臟脏",
    "腦脑", "腸肠", "膽胆", "膚肤", "臉脸", "頸颈", "髮发", "齒齿", "齡龄", "龜龟",
    # —— 番名 / 剧名 / 字幕组名里常见的 ——
    "傑杰", "屬属", "慘惨", "還还", "進进", "遠远", "邊边", "過过", "達达", "運运",
    "連连", "週周", "遲迟", "遺遗", "舉举", "興兴", "舊旧", "潛潜", "陣阵", "隊队",
    "階阶", "際际", "隨随", "隱隐", "難难", "雖虽", "雙双", "雜杂", "離离", "預预",
    "領领", "頭头", "顧顾", "顯显", "類类", "飛飞", "館馆", "駛驶", "騎骑", "鬥斗",
    "麥麦", "遊游", "戲戏", "襲袭", "墮堕", "樹树", "總总", "縮缩", "簡简", "曆历",
    "歷历", "層层", "縱纵", "橫横", "織织", "纖纤", "細细", "綁绑", "縫缝", "統统",
    "絲丝", "維维", "綱纲", "緊紧", "緒绪", "緣缘", "緩缓", "練练", "績绩", "繡绣",
    "繼继", "纏缠", "纜缆", "罰罚", "罷罢", "羈羁", "羨羡", "義义", "翹翘", "聯联",
    "聰聪", "職职", "肅肃", "腎肾", "脅胁", "脈脉", "腳脚", "脫脱", "腫肿", "臘腊",
    "臨临", "艙舱", "艱艰", "蟄蛰", "蟲虫", "衛卫", "衝冲", "補补", "製制", "複复",
    "規规", "親亲", "觀观", "計计", "討讨", "訓训", "許许", "訴诉", "註注", "誌志",
    "誠诚", "誰谁", "調调", "請请", "諒谅", "諷讽", "謝谢", "譜谱", "讚赞", "貝贝",
    "貞贞", "負负", "貢贡", "貧贫", "販贩", "責责", "貿贸", "貼贴", "資资", "賊贼",
    "賓宾", "賞赏", "賤贱", "賦赋", "質质", "賭赌", "購购", "贓赃", "贖赎", "贛赣",
    "趕赶", "趙赵", "跡迹", "踐践", "車车", "軌轨", "軒轩", "軟软", "較较", "輔辅",
    "輕轻", "輛辆", "輩辈", "輪轮", "轄辖", "轎轿", "轟轰", "農农", "迴回", "違违",
    "遜逊", "遞递", "邁迈", "邏逻", "釀酿", "釋释", "針针", "釣钓", "鈔钞", "鈴铃",
    "銅铜", "銘铭", "錢钱", "鋼钢", "錦锦", "鐘钟", "鑄铸", "鑑鉴", "長长", "閉闭",
    "閒闲", "闡阐", "陸陆", "雛雏", "雞鸡", "韌韧", "頂顶", "項项", "順顺", "須须",
    "頑顽", "頒颁", "頓顿", "頗颇", "頰颊", "顆颗", "顏颜", "飽饱", "餅饼", "養养",
    "餘余", "魯鲁", "鮮鲜", "鳳凤", "鳴鸣", "鴻鸿", "鵬鹏", "鶴鹤", "齊齐", "齋斋",
    "龔龚", "懲惩", "彌弥", "懸悬", "懷怀", "懶懒", "擾扰", "攤摊", "楊杨", "楓枫",
    "橋桥", "機机", "櫃柜", "殼壳", "氣气", "決决", "況况", "淚泪", "淨净", "滿满",
    "瀉泻", "災灾", "烏乌", "煩烦", "熱热", "營营", "牆墙", "獸兽", "獻献", "環环",
    "畢毕", "異异", "疇畴", "癒愈", "盃杯", "盡尽", "盤盘", "瞞瞒", "矇蒙", "礎础",
    "祿禄", "禍祸", "禪禅", "禮礼", "禱祷", "穀谷", "窮穷", "窯窑", "竄窜", "競竞",
    "筆笔", "節节", "築筑", "籃篮", "籌筹", "粵粤", "糧粮", "糾纠", "紀纪", "紋纹",
    "納纳", "紐纽", "紙纸", "經经", "緯纬", "縣县", "繕缮", "繪绘", "繫系", "繳缴",
    "聞闻", "聳耸", "艦舰", "葉叶", "葦苇", "蓮莲", "蔭荫", "蕎荞", "蘿萝", "處处",
    "虛虚", "虧亏", "褲裤", "訂订", "詰诘", "詳详", "謙谦", "謹谨", "豐丰", "豬猪",
    "貪贪", "賜赐", "賬账", "贊赞", "轍辙", "鄧邓", "鄭郑", "鄰邻", "釘钉", "鉛铅",
    "銷销", "銳锐", "鋒锋", "鋪铺", "錘锤", "錠锭", "錫锡", "鍋锅", "鍾钟", "鎊镑",
    "鎖锁", "閃闪", "閏闰", "閑闲", "閘闸", "閣阁", "閥阀", "閱阅", "闌阑", "闔阖",
    "陝陕", "陳陈", "險险", "隸隶", "霧雾", "鞏巩", "額额", "顫颤", "飲饮", "飾饰",
    "饅馒", "馭驭", "馮冯", "馳驰", "駐驻", "駕驾", "騷骚", "驅驱", "驕骄", "驟骤",
    "髒脏", "鬧闹", "鬱郁", "鯨鲸", "鵰雕", "鷹鹰", "麵面", "點点", "黨党", "靜静",
    "躍跃", "躰体", "躋跻", "轟轰", "辨辨", "農农", "遙遥", "遼辽", "鄰邻", "醃腌",
    "釀酿", "鑰钥", "長长", "閉闭", "陞升", "陳陈", "難难", "雋隽", "鞦秋", "韁缰",
    "顫颤", "馬马", "騖骛", "體体", "髮发", "鬍胡", "鳳凤", "鴻鸿", "麋麋", "黃黄",
    "黴霉", "黲惨", "鼕冬", "齊齐", "齣出", "龍龙", "龜龟",
    # —— 补充：日常用字里遗漏的 ——
    "內内", "兩两", "壓压", "幀帧", "墊垫", "壘垒", "廳厅", "戶户", "拋抛", "捨舍",
    "搖摇", "撈捞", "撐撑", "撲扑", "擋挡", "擔担", "擴扩", "擺摆", "攏拢", "攔拦",
    "攜携", "攝摄", "攢攒", "攪搅", "敵敌", "斂敛", "晝昼", "朧胧", "檻槛", "櫻樱",
    "歲岁", "歸归", "殘残", "毀毁", "裡里", "郵邮", "妳你", "慾欲", "懇恳", "鬚须",
    "鬆松", "颳刮", "種种", "競竞", "膽胆", "捨舍", "與与", "塚冢", "屍尸", "攙搀",
    "攔拦", "擬拟", "挾挟", "掙挣", "摻掺", "撫抚", "樸朴", "橢椭", "櫥橱", "歐欧",
    "殤殇", "毆殴", "氈毡", "汙污", "決决", "沒没", "潑泼", "潤润", "澀涩", "瀋沈",
    "爐炉", "犧牺", "狹狭", "獰狞", "獻献", "璽玺", "甯宁", "畝亩", "痠酸", "癟瘪",
    "皚皑", "眥眦", "睜睁", "睞睐", "矯矫", "砲炮", "碼码", "稈秆", "稜棱", "穌稣",
    "竊窃", "筍笋", "箋笺", "篩筛", "篤笃", "簾帘", "籐藤", "綁绑", "罈坛", "羥羟",
    "耬耧", "聶聂", "膚肤", "脣唇", "腌腌", "臍脐", "舅舅", "舖铺", "艫舻", "苧苎",
    "菸烟", "萵莴", "蔣蒋", "薯薯", "藹蔼", "蘆芦", "蜆蚬", "蝨虱", "蟬蝉", "蠍蝎",
    "衊蔑", "襖袄", "覬觊", "訃讣", "詛诅", "誕诞", "詼诙", "誇夸", "誅诛", "誘诱",
    "誦诵", "誨诲", "諂谄", "諮谘", "謊谎", "謗谤", "譏讥", "譚谭", "讎雠", "豈岂",
    "豎竖", "貳贰", "賅赅", "賑赈", "賙赒", "賺赚", "贍赡", "軋轧", "軻轲", "逕径",
    "遑遑", "遢遢", "鄒邹", "醞酝", "鈍钝", "鈺钰", "鉗钳", "鉤钩", "銬铐", "銜衔",
    "鋁铝", "鋸锯", "鋼钢", "鋯锆", "錳锰", "鍍镀", "鍾钟", "鎂镁", "鎊镑", "鎢钨",
    "鏢镖", "鐮镰", "鑲镶", "閨闺", "閩闽", "閻阎", "陲陲", "隅隅", "隊队", "隕陨",
    "隕陨", "雞鸡", "鞽桥", "韃鞑", "頑顽", "頡颉", "頤颐", "颯飒", "飢饥", "飩饨",
    "餃饺", "餌饵", "餚肴", "餞饯", "餡馅", "餵喂", "饃馍", "馱驮", "駁驳", "駝驼",
    "駭骇", "駿骏", "騁骋", "騅骓", "驃骠", "驊骅", "驛驿", "髏髅", "髖髋", "鬃鬃",
    "鬢鬓", "魎魉", "鯉鲤", "鯊鲨", "鰓鳃", "鰭鳍", "鱉鳖", "鳴鸣", "鵝鹅", "鶩鹜",
    "鷗鸥", "鷺鹭", "鹽盐", "麗丽", "麥麦", "麵面", "麼么", "點点", "黷黩", "黿鼋",
]

# 建表，并做一致性检查：每项必须是两个字，且不能自相矛盾
_T2S_TABLE: dict[int, str] = {}
_src: dict[str, str] = {}
for _p in _T2S_PAIRS:
    if len(_p) != 2:
        raise ValueError(f"繁简表格式错误，每一项必须是「繁简」两个字：{_p!r}")
    _t, _s = _p
    if _t == _s:
        continue
    if _src.get(_t) not in (None, _s):
        raise ValueError(f"繁简表冲突：{_t} 被映射到多个结果")
    _src[_t] = _s
    _T2S_TABLE[ord(_t)] = _s

# 反向检查：简化字不应该再被当成繁体字映射出去（写反了就会出现这种情况）
_bad = [t for t in _src if t in set(_src.values()) and _src.get(t) != t]
if _bad:
    raise ValueError(f"繁简表疑似写反：{_bad[:10]}")


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
    re.compile(r"\[(\d{1,3})\s*[-~～]\s*(\d{1,3})[^\]]{0,10}\]"),  # [01-26] [01-12合集] [01-12完]
    re.compile(r"[-–—]\s*(\d{1,3})(?:v\d)?\s*(?:[\[\(（]|$)"),  # - 03 [
    re.compile(r"\bS\d{1,2}E(\d{1,2})\b", re.I),             # S01E03
    re.compile(r"第\s*(\d{1,3})\s*[话話集話]"),                 # 第12集 / 第12话
]
# 合集的其它常见写法
EP_RANGE_WORD = re.compile(r"(?:Vol\.?\s*)?(\d{1,3})\s*[-~～]\s*(\d{1,3})\s*[话話集]")
EP_COMPLETE = re.compile(r"全\s*(\d{1,3})\s*[话話集]")
RE_BATCH_HINT = re.compile(r"合集|全集|完結|完结|\bBatch\b|\bFin\b", re.I)


def extract_episode(title: str) -> str:
    """抽出集数标识，用来把同一集的不同版本归并成一组。

    单集返回 '02'，合集/整季打包返回 '01-12' 这样的范围。
    合集必须跟单集区分开 —— 否则第 12 集出了合集，会被当成"第 12 集已推过"而漏掉。"""
    m = EP_PATTERNS[1].search(title)          # [01-12] / [01-12合集]
    if m:
        return f"{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = EP_RANGE_WORD.search(title)           # 01-12话 / Vol.1-6
    if m:
        return f"{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = EP_COMPLETE.search(title)             # 全12话
    if m:
        return f"01-{int(m.group(1)):02d}"

    for pat in (EP_PATTERNS[0], EP_PATTERNS[2], EP_PATTERNS[3], EP_PATTERNS[4]):
        m = pat.search(title)
        if m:
            return f"{int(m.group(1)):02d}"

    # 明说了是合集但没给范围 —— 单独归一类，别跟单集混
    if RE_BATCH_HINT.search(title):
        return "合集"
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
RE_BRACKET = re.compile(r"[\[【]([^\]】]{0,60})[\]】]")
RE_VARIANT = re.compile(r"简|繁|内封|内嵌|双语|chs|cht", re.I)
# 括号里出现这些词，说明那是组名/来源，不是字幕形式
RE_NOT_VARIANT = re.compile(
    r"字幕组|字幕社|漢化|汉化|發布|发布|壓制|压制|搬運|搬运|raws|vcb|baha|bilibili",
    re.I)
# v2 / 修正版 这类"同一集的修订版本"
RE_REVISION = re.compile(r"(?<![a-za-z])v(?:er\.?\s*)?(\d{1,2})(?![a-za-z0-9])", re.I)
RE_REVISION_WORD = re.compile(r"修正版|改訂版|改订版|修正|repack|重新压制|重製|重制", re.I)


# ---- 字幕组名识别 ----
# 看起来像字幕组名的方括号内容
GROUP_HINT = re.compile(r"字幕组|字幕社|字幕|漢化|汉化|壓制|压制|发布组|發布組|Raws|屋|組$", re.I)
BRACKET = re.compile(r"[\[【]([^\]】]{2,60})[\]】]")
# 明确不像字幕组的：画质/封装标签
RE_JUNK_TOKEN = re.compile(
    r"1080p|720p|2160p|4K|8K|HEVC|AVC|AAC|FLAC|WebRip|WEB-?DL|BDRip|BDrip|"
    r"内封|内嵌|內封|內嵌|双语|雙語|简繁|繁简|簡繁|繁體|简体|簡體|"
    r"合集|招募|无修|無修|生肉|熟肉|字幕$",
    re.I)
# 转发/镜像站，不是真正的字幕组
RE_MIRROR_TOKEN = re.compile(r"镜像|鏡像|搬运|搬運|MioBT|ANiTorrent|動漫花園|动漫花园", re.I)
CJK = re.compile(r"[\u4e00-\u9fff]")


def is_group_like(tok: str) -> bool:
    t = tok.strip()
    if not (2 <= len(t) <= 30):
        return False
    if " / " in t or "/" in t:          # 这是"番名 / 番名 / 罗马音"，不是组名
        return False
    if RE_JUNK_TOKEN.search(t):
        return False
    if RE_MIRROR_TOKEN.search(t):
        return False
    if re.fullmatch(r"[0-9.\sMBKGT]+", t, re.I):
        return False
    return True


def extract_group_tokens(title: str, author: str) -> set[str]:
    """从标题里挑出像字幕组名的片段，再并上 RSS 的 author 字段。"""
    toks: set[str] = set()
    for x in BRACKET.findall(title):
        if GROUP_HINT.search(x):
            toks.add(x.strip())
    m = re.match(r"^\s*[\[【]([^\]】]{2,60})[\]】]", title)   # 标题开头那个通常是发布组
    if m:
        toks.add(m.group(1).strip())
    if author:
        toks.add(author.strip())

    out: set[str] = set()
    for t in toks:
        if is_group_like(t):
            out.add(t)
        for part in re.split(r"[&＆]", t):                    # "A&B" 拆开，方便只挑一个
            part = part.strip()
            if is_group_like(part):
                out.add(part)
    return out


# 有些组不用方括号，而是用 ★ 分隔，或者干脆把「外掛繁體中文」直接写在标题里
RE_VARIANT_INLINE = re.compile(
    r"(?:外掛|外挂|内封|内嵌|內封|內嵌)?\s*"
    r"(?:简体中文|簡體中文|繁體中文|繁体中文|简繁|簡繁|繁简|繁體|繁体|簡體|简体|"
    r"简中|繁中|简日|繁日|簡日|雙語|双语|简繁双语|繁體字幕|简体字幕)"
    r"(?:中文|字幕|双语|雙語)?", re.I)


def extract_variant(title: str) -> str:
    """抽字幕形式，例如「简日双语」「简繁日内封」「简繁外挂字幕」。
    多数在标题最后一个方括号里；也有组用 ★ 分隔或直接写在标题里，所以还有一道兜底。"""
    for s in reversed(RE_BRACKET.findall(title)):
        s = s.strip()
        if len(s) <= 24 and RE_VARIANT.search(s) and not RE_NOT_VARIANT.search(s):
            return s
    m = re.search(r"[\(（]([^)）]{1,24})[\)）]", title)
    if m and RE_VARIANT.search(m.group(1)) and not RE_NOT_VARIANT.search(m.group(1)):
        return m.group(1).strip()
    m = RE_VARIANT_INLINE.search(title)
    if m:
        return m.group(0).strip()
    return ""


def is_traditional_only(variant: str) -> bool:
    """判断这个版本是不是「只有繁体字幕」。
    简繁双语的（如「简繁日内封」）会同时含简，不算。"""
    if not variant:
        return False                      # 没标形式的（生肉、搬运）不拦
    n = norm(variant)
    if "cht" in n and "chs" not in n:
        return True
    return ("繁" in n) and ("简" not in n)


def get_revision(title: str) -> int:
    """返回修订号：无标记 / v1 = 1，v2 = 2，v3 = 3……
    用来让「同一集后来出了 v2」也能再推一次。"""
    rev = 1
    for m in RE_REVISION.finditer(title):
        try:
            rev = max(rev, int(m.group(1)))
        except ValueError:
            pass
    if RE_REVISION_WORD.search(title):
        rev = max(rev, 2)
    return rev


def _seen_pair(v):
    """把 state 里存的值解析成 (最好档位, 最大修订号)。
    档位越小越优；修订号越大越新。兼容只存了档位的老格式。"""
    if v is None:
        return None
    if isinstance(v, int):
        return (v, 1)
    a, _, b = str(v).partition(":")
    try:
        return (int(a), int(b or 1))
    except ValueError:
        return None


def get_variant_tier(title: str, prefer: list[str]) -> int:
    """返回标题在偏好列表里的优先级档位，越小越优先；都没命中返回 999。"""
    if not prefer:
        return 0
    nt = norm(title)
    for idx, p in enumerate(prefer):
        if norm(p) in nt:
            return idx
    return 999


# ---- 分季识别 ----
# 只认比较明确的写法，宁可不认也别认错（比如裸的罗马数字 Ⅱ 太容易误伤）
CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
RE_SEASON_CN = re.compile(r"第\s*([一二三四五六七八九十\d]{1,2})\s*[季期部]")
RE_SEASON_EN = re.compile(r"(?:^|[\s\[【_\-])(?:s|season)\s*0?(\d{1,2})(?![0-9pPiI])", re.I)
RE_SEASON_ORD = re.compile(r"\b(\d{1,2})\s*(?:st|nd|rd|th)\s*season\b", re.I)
# 罗马数字只在「独立成词」时才认（前后是空格/括号/结尾），否则太容易误伤
ROMAN = {"Ⅱ": 2, "Ⅲ": 3, "Ⅳ": 4, "Ⅴ": 5, "Ⅵ": 6, "Ⅶ": 7, "Ⅷ": 8, "Ⅸ": 9}
RE_SEASON_ROMAN = re.compile(r"(?:^|[\s\[【\-–—~～])([ⅡⅢⅣⅤⅥⅦⅧⅨ])(?=$|[\s\]】\-–—~～])")


def extract_season(title: str) -> int | None:
    """从标题里判断这是第几季。认不出返回 None（多数第一季压根不标）。"""
    m = RE_SEASON_CN.search(title)
    if m:
        v = m.group(1)
        if v.isdigit():
            return int(v)
        # 「十二季」这种：十位先看
        if v == "十":
            return 10
        if v.startswith("十"):
            return 10 + CN_DIGITS.get(v[1:], 0)
        if v.endswith("十"):
            return CN_DIGITS.get(v[0], 0) * 10
        return CN_DIGITS.get(v)
    m = RE_SEASON_ORD.search(title)
    if m:
        return int(m.group(1))
    m = RE_SEASON_ROMAN.search(title)
    if m:
        return ROMAN.get(m.group(1))
    m = RE_SEASON_EN.search(title)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 20:
            return n
    return None


def season_ok(title: str, want: int) -> bool:
    """标题是不是「第 want 季」。没标季号的当第一季 —— 大多数字幕组做第一季时
    压根不写「第一季」，而第二季一定会标出来。"""
    got = extract_season(title)
    if got is None:
        return int(want) == 1
    return got == int(want)


def match_rule(item: dict, rule: dict) -> bool:
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
    if sea and not season_ok(item["title"], int(sea)):
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
    rev = item.get("_rev", 1)
    if upgrade:
        tag = "（修正版）" if rev > 1 else "（更优版本）"
    else:
        tag = ""
    title = f"{prefix}{tag}{name}"[:120]

    lines = [f"标题：{item['title']}"]
    want = " / ".join(rule.get("group") or []) or "任意字幕组"
    lines.append(f"字幕组：{want}")
    if item.get("_fallback"):
        # 指定字幕组没做这一集（还没出、或者中途弃番了），这条是兜底抓来的
        src = actual_group(item) or "其它字幕组"
        lines.append(f"※ 这一集指定字幕组还没有，本条来自：{src}")

    # 字幕形式（简日双语 / 简繁日内封 …）。没标出来的就不写这一行。
    if item.get("_variant"):
        lines.append(f"字幕形式：{item['_variant']}")
    if rev > 1:
        lines.append(f"版本：v{rev}（修正版）")

    ep = item.get("episode") or ""
    if ep:
        if "-" in ep:
            lines.append(f"集数：第 {ep} 集（合集/整季）")
        elif ep == "合集":
            lines.append("集数：合集")
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
    skip_trad = s.get("skip_traditional_only", True)
    matched: list[dict] = []
    for ri, rule in enumerate(rules):
        rid = rule_id(rule, ri)
        primary = list(rule.get("group") or [])
        # 兜底：指定字幕组没做这一集（或中途弃番）时，用别的组补上。
        # 判定见 fallback_match() —— 必须标题里带完整番名，免得短关键词误伤别的番。
        use_fallback = bool(primary) and rule.get("fallback_any_group", True)

        for it in all_items.values():
            if match_rule(it, rule):
                fallback = False
            elif use_fallback and fallback_match(it, rule):
                fallback = True
            else:
                continue

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
    return list(uniq.values())


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


def actual_group(item: dict) -> str:
    """读出这一条实际是谁发的，用于兜底推送时说明来源。
    优先取标题最前面那个【…】—— 那通常是发布组。"""
    title = item.get("title") or ""
    m = re.match(r"^\s*[\[【]([^\]】]{2,60})[\]】]", title)
    if m and is_group_like(m.group(1)):
        return m.group(1).strip()
    author = (item.get("author") or "").strip()
    if author and is_group_like(author):
        return author
    toks = [t for t in extract_group_tokens(title, author)
            if "&" not in t and "＆" not in t]
    return toks[0] if toks else ""


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
            best = best_of(items)
            prev = _seen_pair(state["episodes"].get(f"{rid}|{ep}"))
            # 两种情况值得打扰你：字幕形式更好了，或者这一集出了修正版（v2 及以后）
            if prev is not None and best["_tier"] >= prev[0] and best["_rev"] <= prev[1]:
                continue
            # prev 存在 = 是"更优版本/修正版"；不存在 = 这是第一次见到这一集
            pushes.append((best, prev is not None))

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
        # 记下「见过的最好档位:见过的最大修订号」，档位和修订号各自单调，不会因为
        # 后出的版本字幕形式更差就把记录冲低（否则会出现反复补推）
        for (rid, ep), items in groups.items():
            best = best_of(items)
            key = f"{rid}|{ep}"
            prev = _seen_pair(state["episodes"].get(key))
            if prev is None:
                state["episodes"][key] = f"{best['_tier']}:{best['_rev']}"
            else:
                state["episodes"][key] = f"{min(prev[0], best['_tier'])}:{max(prev[1], best['_rev'])}"
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
