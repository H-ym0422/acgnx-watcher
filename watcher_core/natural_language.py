"""Deterministic Chinese requests; reject ambiguous commands before mutation."""
from dataclasses import dataclass, field
import re

NUM = r'[0-9零〇一二两三四五六七八九十百千]+'


def number(value):
    if value.isascii() and value.isdigit():
        result = int(value)
    else:
        digits = dict(zip('零〇一二两三四五六七八九', (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
        units = {'十': 10, '百': 100, '千': 1000}
        result, pending, last = 0, None, 10000
        for char in value:
            if char in digits:
                if pending and digits[char]:
                    raise ValueError('中文数字不明确，请使用阿拉伯数字')
                pending = digits[char]
            elif char in units and units[char] < last:
                last = units[char]
                result += (pending if pending is not None else 1) * last
                pending = None
            else:
                raise ValueError('数字不明确，请使用阿拉伯数字')
        result += pending or 0
    if not 1 <= result <= 9999:
        raise ValueError('季号和集数必须在 1 到 9999 之间')
    return result


@dataclass
class Request:
    mode: str
    term: str = ''
    season: int | None = None
    floor: int | None = None
    end: int | None = None
    groups: list[str] = field(default_factory=list)
    any_group: bool = False
    strict_group: bool = False
    backfill: str | None = None

    def summary(self):
        labels = dict(search='仅查询', add='持续追番', pause='暂停', resume='恢复',
                      edit='修改', **{'del': '删除', 'list': '列表'})
        out = [f'识别结果：{labels[self.mode]}', f'番名/规则：{self.term}']
        if self.season is not None:
            out.append(f'季号：第 {self.season} 季')
        if self.floor is not None:
            out.append(f'集数：第 {self.floor} 集' + (f'至第 {self.end} 集' if self.end is not None else '起（含该集）'))
        if self.groups or self.any_group:
            out.append('字幕组：' + ('任意' if self.any_group else '、'.join(self.groups)))
        if self.mode == 'add':
            out.append('回溯：' + (self.backfill or 'all'))
            out.append('其他组兜底：' + ('关闭' if self.strict_group else '按原策略逐集等待 7 天'))
        return '\n'.join(out)


PREFIXES = {
    'search': ('给我找一下', '帮我找一下', '给我找', '帮我找', '我想找', '帮我查', '查找', '查询', '查看', '搜索', '找一下', '找找', '查一下', '帮我搜', '搜一下'),
    'add': ('帮我追', '给我追', '帮我加番', '帮我添加', '给我加番', '我想追', '我要追', '帮我订阅', '订阅', '加入监听', '添加监听', '帮我监听', '开始追'),
    'del': ('帮我删掉', '帮我删除', '给我删除', '删掉', '取消订阅', '取消监听'),
    'pause': ('帮我暂停监听', '帮我暂停推送', '帮我暂停', '暂停推送', '暂停监听', '先别推', '先暂停', '不要再推送', '别再推'),
    'resume': ('帮我恢复监听', '帮我恢复推送', '帮我恢复', '继续推送', '恢复监听', '恢复推送', '继续追'),
    'edit': ('帮我修改', '帮我调整', '修改监听', '调整监听'),
}


def normalize_title(title):
    text = title.strip()
    match = re.match(r'^(删番|删除|暂停|恢复|修改)\s*[:：]\s*(.*)$', text)
    if match:
        rest = match[2].strip()
        if re.match(r'^(?:帮我|给我)(?:删除|删掉|暂停|恢复|修改)', rest):
            return rest
        return match[1] + ' ' + rest
    return text


def parse(title):
    text = normalize_title(title)
    polite = text.startswith('请')
    text = re.sub(r'^请(?:帮我)?', '帮我' if text.startswith('请帮我') else '', text)
    if text in ('我在追哪些番', '查看我的追番', '我的追番列表'):
        return Request('list')
    mode = None
    for action, prefixes in PREFIXES.items():
        prefix = next((p for p in prefixes if text.startswith(p)), None)
        if prefix:
            mode, text = action, text[len(prefix):]
            break
    if mode is None:
        legacy = re.match(r'^(加番|添加|新增|追番|删番|删除|移除|取消|暂停|恢复|修改)\s*(.*)$', text)
        if legacy and (polite or re.search(r'第' + NUM + r'[季期集话話]|字幕组', legacy[2])):
            mode = {'删番': 'del', '删除': 'del', '移除': 'del', '取消': 'del', '暂停': 'pause', '恢复': 'resume', '修改': 'edit'}.get(legacy[1], 'add')
            text = legacy[2]
        elif re.match(r'^(?:帮我)?把.+(?:改为|改成|换为|换成)', text):
            mode = 'edit'
            text = re.sub(r'^(?:帮我)?把', '', text)
            text = re.sub(r'(?:的)?字幕组(?:改为|改成|换为|换成)', '，字幕组为', text)
        else:
            return None
    if re.search(r'(?:然后|并且|顺便|同时|并)\s*(?:帮我|给我)?(?:追|找|删除|暂停|恢复|订阅)', text):
        raise ValueError('每个 Issue 请只写一个操作')
    if re.search(r'字幕(?:格式|形式|类型)', text):
        raise ValueError('暂不支持在标题中设置字幕格式筛选，未添加或修改规则；请先查看逐集字幕类型再选组')
    request = Request(mode)
    text = text.strip(' ：:,，')
    # Extract explicit group clause before removing season and episode numbers.
    gm = re.search(r'字幕组\s*(?:改为|换成|为|是|用|选|优先|[:：])\s*([^，,。；;]+)', text)
    if gm:
        value = gm[1].strip()
        text = text[:gm.start()] + text[gm.end():]
        request.any_group = value.lower() in ('任意', '不限', 'any', '*', '任意字幕组')
        request.groups = [] if request.any_group else [v.strip() for v in re.split(r'、|/|和|及|&', value) if v.strip()]
    strict = r'不兜底|不要其他组|不接受其他组|只收|仅收'
    request.strict_group = bool(re.search(strict, text))
    if not gm:
        gm = re.search(r'(?:只收|仅收)(.+?)(?:的资源|的版本|发布的|制作的)$', text)
        if gm:
            request.groups = [gm[1].strip()]
            text = text[:gm.start()]
    text = re.sub(strict, '', text)
    for pattern, value in ((r'只(?:要|推)(?:最新一集|最新集)|只看最新', 'latest'),
                           (r'不补旧集|不要历史|只推新集|不回溯', 'none'),
                           (r'补齐历史|全部补齐|全部推送', 'all')):
        if re.search(pattern, text):
            if request.backfill and request.backfill != value:
                raise ValueError('回溯要求冲突')
            request.backfill = value
            text = re.sub(pattern, '', text)
    seasons = list(re.finditer(r'第\s*(' + NUM + r')\s*[季期]|\bS(\d{1,2})\b', text, re.I))
    if len(seasons) > 1:
        raise ValueError('请只指定一个季度')
    if seasons:
        sm = seasons[0]
        request.season = number(sm[1] or sm[2])
        text = text[:sm.start()] + text[sm.end():]
    if re.search(r'第\s*[-负]|第\s*\d+\.\d+\s*[集季]', text):
        raise ValueError('季号和集数必须是正整数')
    pattern = (r'(?:从)?第\s*(' + NUM + r')\s*(?:集|话|話)?\s*'
               r'(?:[到至~～—-]\s*第?\s*(' + NUM + r')\s*[集话話]|[集话話])'
               r'\s*(及以后|及之后|以后|之后|开始|起)?')
    episodes = list(re.finditer(pattern, text))
    if len(episodes) > 1:
        raise ValueError('集数条件不明确，请只写一个集数或区间')
    if episodes:
        em = episodes[0]
        request.floor = number(em[1])
        request.end = number(em[2]) if em[2] else (None if em[3] else request.floor)
        if request.end is not None and request.end < request.floor:
            raise ValueError('结束集数不能小于起始集数')
        text = text[:em.start()] + text[em.end():]
    text = re.sub(r'(?:的)?(?:资源|版本|监听|订阅)\s*$', '', text.strip(' ,，。；;'))
    request.term = text.strip(' 《》“”\"\'，,。；;：:')
    if not request.term:
        raise ValueError('请填写番名或唯一规则 ID')
    if re.search(r'字幕组|第' + NUM + r'[集话話季期]|改成|改为|换成|换为', request.term):
        raise ValueError('有条件未能识别，请改写为示例中的表达；未修改规则')
    if request.strict_group and not request.groups and mode != 'edit':
        raise ValueError('请明确只收哪个字幕组')
    if mode == 'add' and request.end is not None:
        raise ValueError('单集和固定区间请用“给我找”；追番请写“从第 3 集起”')
    if mode in ('del', 'pause', 'resume') and (request.floor is not None or request.groups or request.any_group or request.backfill):
        raise ValueError('此操作作用于整条追番规则，请只写番名、季号或唯一 ID')
    return request
