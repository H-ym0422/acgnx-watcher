import base64
import hashlib
import json
import re
from urllib.parse import urlencode

from .rules import target_label

CONFIRM = '我确认删除这条追番规则，并清理其待发送消息和去重记录'
MARKER = re.compile(r'<!-- acgnx-delete:v1:([A-Za-z0-9_=-]+) -->')


def snapshot(rule):
    signature = hashlib.sha256(json.dumps(rule, ensure_ascii=False, sort_keys=True).encode('utf8')).hexdigest()
    return {'version': 1, 'id': rule['id'], 'target': target_label(rule), 'signature': signature}


def marker(rule):
    data = base64.urlsafe_b64encode(json.dumps(snapshot(rule), ensure_ascii=False).encode('utf8')).decode()
    return '<!-- acgnx-delete:v1:' + data + ' -->'


def delete_url(rule, repository):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository or ''):
        return ''
    query = urlencode({'template': 'confirm-delete.yml', 'title': '确认删除 ' + target_label(rule),
                       'target': target_label(rule), 'snapshot': marker(rule)})
    return 'https://github.com/' + repository + '/issues/new?' + query


def handle(event, event_name, api):
    if event_name != 'issues' or event.get('action', 'opened') != 'opened':
        return {'ok': True, 'step': 'done', 'text': '确认删除仅在首次提交时执行，未重复删除。'}
    issue = event['issue']
    title_target = issue['title'].removeprefix('确认删除 ').strip()
    body = issue.get('body') or ''
    found = MARKER.findall(body)
    if len(found) != 1:
        raise ValueError('缺少有效的删除确认信息，请从“我的番剧.md”重新进入')
    try:
        value = json.loads(base64.urlsafe_b64decode(found[0] + '=' * (-len(found[0]) % 4)))
    except (ValueError, UnicodeError, TypeError):
        raise ValueError('删除确认信息无效，请从追番列表重新进入') from None
    if not isinstance(value, dict) or not isinstance(value.get('id'), str):
        raise ValueError('删除确认信息无效，请从追番列表重新进入')
    rid = value['id']
    cfg = api.ar.aw.load_config()
    matches = [r for r in cfg['rules'] if r['id'] == rid]
    if len(matches) != 1:
        raise ValueError('这条规则已不存在，未删除其他规则；请刷新追番列表')
    rule = matches[0]
    if title_target not in (rid, target_label(rule)):
        raise ValueError('标题与删除对象不符，未删除；请从追番列表重新进入')
    if value != snapshot(rule) or api.fields(body).get('删除对象') != target_label(rule):
        raise ValueError('规则或确认内容已经变化，未删除；请刷新追番列表重新确认')
    checked = re.findall(r'^\s*- \[[xX]\] (.+?)\s*$', body, re.M)
    if checked != [CONFIRM]:
        raise ValueError('尚未勾选确认删除，未修改规则')
    result = api.run_cli(['--del-id', rid, '--no-push'])
    result['text'] = '删除对象：' + target_label(rule) + '\n\n' + result['text']
    if result.get('ok'):
        result['step'] = 'deleted'
    return result
