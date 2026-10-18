from __future__ import annotations
import base64
import gzip
import hashlib
import hmac
import html
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
TZ8 = timezone(timedelta(hours=8))
log = logging.getLogger("acgnx")


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


RE_SIZE = re.compile(r"\|\s*([0-9.]+\s*[KMGTP]?i?B)\s*\|", re.I)
RE_BTIH = re.compile(r"urn:btih:([0-9a-zA-Z]{32,40})", re.I)

EP_PATTERNS = [
    re.compile(r"\[(\d{1,3})(?:v\d)?\]"),                    # [02] [01v2]
    # [01-26] [01-12合集] [01-12完] [01-28+SPx11]
    # （后面跟 +SPx11 / SP 之类能有十几个字符，原来限死 10 个字符就漏了 —— 2026-10-16 用户反馈）
    re.compile(r"\[(\d{1,3})\s*[-~～]\s*(\d{1,3})[^\]]{0,20}\]"),
    re.compile(r"[-–—]\s*(\d{1,3})(?:v\d)?\s*(?:[\[\(（]|$)"),  # - 03 [
    re.compile(r"\bS\d{1,2}E(\d{1,2})\b", re.I),             # S01E03
    re.compile(r"第\s*(\d{1,3})\s*[话話集話]"),                 # 第12集 / 第12话
    re.compile(r"(?<![a-z])ep?\.?\s*(\d{1,3})(?![0-9])", re.I),  # EP10 / E10
    # 「蓝色监狱 / Blue Lock 26 [WebRip ...]」——番名后直接跟数字再跟画质标签。
    # 数字前必须是非字母（否则 "Lock 26" 里的字母会挡住），数字后必须紧跟画质标签，
    # 这样「1080」「2024」这类不会被误当集数。
    re.compile(r"(?<![0-9A-Za-z.])(\d{1,3})\s*[\[\(（]\s*"
               r"(?:Web|WEB|web|Blu|BluRay|BD|BDRip|BDTV|BDRemux|1080|2160|720|4K|4k|HDR)",
               re.I),
]
# 合集的其它常见写法
EP_RANGE_WORD = re.compile(r"(?:Vol\.?\s*)?(\d{1,3})\s*[-~～]\s*(\d{1,3})\s*[话話集]")
# 「S01 | 01-28+SPx11」这种**不在方括号里**的裸区间。
# 注意：区间有两个捕获组，必须放在区间解析区，不能混进 EP_PATTERNS 的单集循环
#（那个循环只读 group(1)，会把 01-28 误当成第 01 集）。2026-10-16 踩过。
EP_RANGE_BARE = re.compile(
    r"(?<![0-9])(?:^|[^\d.])(\d{1,2})\s*[-~～]\s*(\d{1,2})"
    r"\s*(?:\+\s*SP|\bSP\b|[话話集])")
# 「[03(51)]」—— 括号里是总集数，取前面那个
EP_TOTAL_IN_PAREN = re.compile(r"[\[【]\s*(\d{1,3})\s*[\(（]\s*\d{1,4}\s*[\)）]")
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
    m = EP_RANGE_BARE.search(title)           # S01 | 01-28+SPx11（不在方括号里的裸区间）
    if m:
        return f"{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = EP_COMPLETE.search(title)             # 全12话
    if m:
        return f"01-{int(m.group(1)):02d}"
    # 「[03(51)]」—— 括号里是总集数，要取前面那个（2026-10-17 药屋实测）
    m = EP_TOTAL_IN_PAREN.search(title)
    if m:
        return f"{int(m.group(1)):02d}"

    for pat in (EP_PATTERNS[0], EP_PATTERNS[2], EP_PATTERNS[3],
                EP_PATTERNS[4], EP_PATTERNS[5], EP_PATTERNS[6]):
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


def _pref_key(item: dict) -> tuple[int, int]:
    """条目的「该不该留下」权重，越小越优先：
    先看是不是用户指定字幕组发的（指定 > 兜底），再看字幕形式档位。"""
    return (1 if item.get("_fallback") else 0, item.get("_tier", 0))


def group_eq(a: str, b: str) -> bool:
    """两个字幕组名算不算同一个（「绿茶字幕组」vs「綠茶字幕組」要能对上）。"""
    na = norm(a or "")
    nb = norm(b or "")
    if not na or not nb:
        return False
    if na == nb:
        return True
    return na in nb or nb in na


def group_key(item: dict, want_groups: list[str] | None = None) -> str:
    """这条算哪个字幕组的**分组键**。

    ⭐ 标题里的组名和 RSS 的 author 字段**经常不一致**（2026-10-17 用户实测）：
    绿茶发布机发的条目，标题开头写的是 `[绿茶字幕组]`，author 才是 `绿茶发布机`。
    只认标题的话，用户在规则里选的组名一条都匹配不上，
    最后全被兜底组顶掉 —— 表现就是「我明明要了两个组，只拿到一个」。

    所以：**规则里选过的组名，能对上标题或 author 任意一个就算命中**，
    并且分组时统一用「用户认识的那个名字」，保证同一组不会被拆成两个。"""
    names = [(actual_group(item) or "").strip(), (item.get("author") or "").strip()]
    for w in (want_groups or []):
        if not w:
            continue
        for n in names:
            if group_eq(n, w):
                return w
    return next((n for n in names if n), "")


def collapse_overlaps(ordered: list[tuple[str, dict]], _group_key=None) -> list[tuple[str, dict]]:
    """同一「字幕组」里，如果已经有「01-12 全集」，就把落在它区间内的单集丢掉。

    不然推给你的消息会又给一个完整合集、又给一堆 05~12 的单集，看着莫名其妙
    （用户 2026-10-16 实测反馈的问题）。

    ⭐ **只在同一个字幕组内部折叠**（2026-10-17 修）：
    绿茶字幕组发的 `01-13` 全季包，会把北宇治、TSDM、喵萌、悠哈璃羽
    在 01~13 区间内的**全部单集**吃掉 —— 实测 50 条被吃到只剩 8 条。
    你选了哪几个组，就要能同时拿到那几个组的资源，所以 A 组的合集
    不该覆盖 B 组的单集。

    字幕组认不出来（`actual_group` 为空）的条目归为同一组，避免误伤。
    ⚠️ 分组口径由 `_group_key` 决定（默认 `actual_group`）；调用方如果做过
    「标题组名 ≠ author」的归一，要把自己的口径传进来，否则折叠时又会混组
    （绿茶发布机标题写 [绿茶字幕组]，会跟真正的绿茶字幕组互相折叠 —— 踩过）。"""

    def key_of(it: dict) -> str:
        return (_group_key(it) if _group_key else actual_group(it)) or ""

    by_group: dict[str, list[tuple[str, dict]]] = {}
    for ep, it in ordered:
        by_group.setdefault(key_of(it).strip(), []).append((ep, it))

    out: list[tuple[str, dict]] = []
    for items in by_group.values():
        out.extend(_collapse_in_group(items))
    # 保持原来的排序
    order = {id(it): i for i, (_, it) in enumerate(ordered)}
    out.sort(key=lambda t: order.get(id(t[1]), 1 << 30))
    return out


def _collapse_in_group(ordered: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
    """单个字幕组内部的合集折叠（不跨组）。"""
    spans: list[tuple[int, int, tuple[int, int]]] = []      # (起, 止, 权重)
    for ep, it in ordered:
        m = re.match(r"^(\d{1,4})-(\d{1,4})$", (ep or "").strip())
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if b > a:
                spans.append((a, b, _pref_key(it)))
    if not spans:
        return ordered

    out: list[tuple[str, dict]] = []
    for ep, it in ordered:
        m = re.match(r"^(\d{1,4})$", (ep or "").strip())
        if m:
            n = int(m.group(1))
            k = _pref_key(it)
            if any(a <= n <= b and k >= sk for a, b, sk in spans):
                continue                                # 被本组合集涵盖，且合集不比它差
        out.append((ep, it))
    return out


def season_ok(title: str, want: int) -> bool:
    """标题是不是「第 want 季」。

    ⚠️ **没标季号的必须放行**（2026-10-17 修）：
    原来写的是「没标季号 = 第一季」—— 对第一季成立，对第三季就全错。
    实测「药屋少女的呢喃 第三季」里喵萌的 11 条正片**标题里一个季号都没有**，
    按老逻辑会以为它们是第一季，全部挡掉 → 整部番一条都推不出来。

    现在：标了季号就按季号比；没标就放行
    （标了别季的会被挡掉，不标的留下 —— 这才是安全的默认）。"""
    got = extract_season(title)
    if got is None:
        return True
    return got == int(want)


# 不是正片的条目：主题曲 / 片尾曲 / 插曲原声 / PV / 预告。
# 这些在站里经常跟正片混在同一部番下，标题里又没有集数，
# 会被当成「认不出集数」的新条目推给你（2026-10-17 药屋那条实测）。
RE_NOT_EPISODE = re.compile(
    r"主題歌|主题歌|オープニング|エンディング|OP主題歌|ED主題歌"
    r"|插曲|原声带|原聲帶|音集| soundtrack|\bOST\b|Drama\s*CD|Bonus\s*Track"
    r"|音樂|音乐|选曲集|選曲集|Insert\s*Song"
    # 「ED「AIYOU」」「OP主題歌」这种只在片名后带引号的写法（药屋那条实测漏网的）
    r"|\bED\s*[「『“\x22]|\bOP\s*[「『“\x22]|\bOpening\b|\bEnding\b",
    re.I)


def rule_id(rule: dict, idx: int) -> str:
    return rule.get("id") or f"rule{idx}"


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


__all__ = ['_T2S_PAIRS', '_bad', 'norm', 'RE_SIZE', 'RE_BTIH', 'EP_PATTERNS', 'EP_RANGE_WORD', 'EP_RANGE_BARE', 'EP_TOTAL_IN_PAREN', 'EP_COMPLETE', 'RE_BATCH_HINT', 'extract_episode', 'parse_feed', 'RE_BRACKET', 'RE_VARIANT', 'RE_NOT_VARIANT', 'RE_REVISION', 'RE_REVISION_WORD', 'GROUP_HINT', 'BRACKET', 'RE_JUNK_TOKEN', 'RE_MIRROR_TOKEN', 'CJK', 'is_group_like', 'extract_group_tokens', 'RE_VARIANT_INLINE', 'extract_variant', 'is_traditional_only', 'get_revision', '_seen_pair', 'get_variant_tier', 'CN_DIGITS', 'RE_SEASON_CN', 'RE_SEASON_EN', 'RE_SEASON_ORD', 'ROMAN', 'RE_SEASON_ROMAN', 'extract_season', '_pref_key', 'group_eq', 'group_key', 'collapse_overlaps', '_collapse_in_group', 'season_ok', 'RE_NOT_EPISODE', 'rule_id', 'actual_group']
