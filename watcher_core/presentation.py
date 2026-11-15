"""Build user-facing release titles and notification bodies."""
from __future__ import annotations
from .rules import display_name


def build_push_content(item: dict, rule: dict, prefix: str, upgrade: bool=False, *, api) -> tuple[str, str]:
    """返回 (标题, 正文)。
    标题保证单行 —— Server酱 / PushPlus 的标题都不允许出现换行。"""
    name = display_name(rule, item['title'][:60])
    src = api.actual_group(item) or '未识别'
    ep = item.get('episode') or ''
    rev = item.get('_rev', 1)
    if upgrade:
        prefix = '【资源更新】'
        tag = '（修正版）' if rev > 1 else '（更优版本）'
    else:
        tag = ''
    episode = f' 第{ep}集' if ep and ep != '合集' else (' 合集' if ep else '')
    title = ' '.join(f'{prefix}{tag}{name}{episode}（{src}）'.split())[:120]
    lines = []
    want_list = [g for g in rule.get('group') or [] if not g.endswith('*')]
    if item.get('_variant'):
        lines.append(f"字幕形式：{item['_variant']}")
    else:
        lines.append('字幕形式：未标注，请查看发布说明')
    if rev > 1:
        lines.append(f'版本：v{rev}（修正版）')
    if not ep:
        lines.append('集数：合集（标题有合集标记，未识别范围）' if api.RE_BATCH_HINT.search(item['title']) else '集数：暂未识别，请查看发布标题')
    elif '-' in ep:
        lines.append(f'集数：第 {ep} 集（合集/整季）')
    elif ep == '合集':
        lines.append('集数：合集（标题里没写具体范围）')
    else:
        lines.append(f'集数：第 {ep} 集')
    if item.get('size'):
        lines.append(f"大小：{item['size']}")
    if item.get('pub_dt'):
        lines.append(f"发布：{item['pub_dt']:%Y-%m-%d %H:%M}")
    lines.append(f'推送：{api.datetime.now(api.TZ8):%Y-%m-%d %H:%M}（北京时间）')
    if item.get('_fallback') and want_list:
        lines += ['', f"兜底资源：已等满 {api.fallback_wait.WAIT_DAYS} 天，仍未检测到「{'、'.join(want_list)}」的对应版本；不代表指定组停更。"]
    lines += ['', f"发布标题：{item['title']}"]
    if item.get('link'):
        lines.append(f"详情：{item['link']}")
    if item.get('magnet'):
        lines.append('磁力：')
        lines.append(item['magnet'])
    else:
        lines.append('（本条 RSS 未附带磁力，请点详情页获取）')
    if rule.get('note'):
        lines.append(f"备注：{rule['note']}")
    return (title, '\n'.join(lines))
