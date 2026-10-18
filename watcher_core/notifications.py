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
from .network import UA
TZ8 = timezone(timedelta(hours=8))
log = logging.getLogger("acgnx_watch")


def _post_json(url: str, payload: dict, timeout: int = 15,
               headers: dict | None = None) -> str:
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        h = {"Content-Type": "application/json; charset=utf-8", "User-Agent": UA}
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, data=data, headers=h)
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


# tenant_access_token 有效期约 2 小时，缓存起来别每次都换
_FEISHU_TOK: dict = {"token": "", "exp": 0.0}


def _feishu_tenant_token(s: dict) -> str:
    now = time.time()
    if _FEISHU_TOK["token"] and now < _FEISHU_TOK["exp"] - 120:
        return _FEISHU_TOK["token"]
    app_id = (s.get("feishu_app_id") or "").strip()
    app_secret = (s.get("feishu_app_secret") or "").strip()
    if not app_id or not app_secret:
        return ""
    resp = _post_json(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        {"app_id": app_id, "app_secret": app_secret})
    try:
        j = json.loads(resp)
    except Exception:  # noqa: BLE001
        j = {}
    tok = j.get("tenant_access_token") or ""
    if not tok:
        log.error("取 tenant_access_token 失败 | %s", resp[:200])
        return ""
    _FEISHU_TOK["token"] = tok
    _FEISHU_TOK["exp"] = now + int(j.get("expire", 7200))
    return tok


def _send_feishu_app(title: str, body: str, s: dict) -> tuple[bool, str]:
    """用飞书应用给用户发**私聊**消息。

    为什么必须是私聊：`im.message.message_read_v1`（已读回执）只在单聊时触发，
    群消息飞书不会告诉任何人「这条被读了」。

    返回 (是否成功, message_id)。message_id 是已读回执关联到具体哪一集的钥匙，
    Worker 收到回执后靠它去 state.json 里找并删除对应项。
    """
    chat_id = (s.get("feishu_chat_id") or "").strip()
    if not chat_id:
        log.error("没配 feishu_chat_id，跳过飞书应用推送")
        return False, ""
    tok = _feishu_tenant_token(s)
    if not tok:
        return False, ""

    text = truncate_utf8(f"{title}\n{body}", 18000)
    resp = _post_json(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        {"receive_id": chat_id, "msg_type": "text",
         "content": json.dumps({"text": text}, ensure_ascii=False)},
        headers={"Authorization": f"Bearer {tok}"})
    try:
        j = json.loads(resp)
    except Exception:  # noqa: BLE001
        j = {}
    if j.get("code") == 0:
        mid = ((j.get("data") or {}).get("message_id")) or ""
        log.info("飞书应用私聊推送成功 message_id=%s", mid)
        return True, mid
    log.error("飞书应用推送失败 code=%s msg=%s | %s",
              j.get("code"), j.get("msg"), resp[:180])
    return False, ""


CHANNEL_FIELDS = {
    "pushplus": "pushplus_token",
    "serverchan": "serverchan_sendkey",
    "webhook": "webhook_url",
    "feishu_app": "feishu_chat_id",
}
CHANNEL_LABEL = {
    "pushplus": "PushPlus（微信）",
    "serverchan": "Server酱（微信）",
    "webhook": "群机器人（企业微信/钉钉/飞书）",
    "feishu_app": "飞书应用私聊",
}
# feishu_app 放最前：配了就优先用它（需要企业账号，见下面那段注释）
# ⚠️ 2026-10-16：这条渠道需要**企业/组织账号**。个人「豆包账号」调
#    tenant_access_token 会返回 21001 Access denied by access control policy，
#    所以 config.json 里 push_channel 保持 "webhook"（群机器人），不会用到它。
#    将来换成企业账号，只要把 ACGNX_FEISHU_APP_* 三个 Secret 填上就自动生效。
CHANNEL_ORDER = ["feishu_app", "pushplus", "serverchan", "webhook"]
CHANNEL_SENDER = {
    "pushplus": _send_pushplus,
    "serverchan": _send_serverchan,
    "webhook": _send_webhook,
    "feishu_app": _send_feishu_app,
}

def available_channels(s: dict) -> list[str]:
    return [ch for ch in CHANNEL_ORDER if (s.get(CHANNEL_FIELDS[ch]) or "").strip()]


def send_notification(title: str, body: str, s: dict) -> tuple[bool, str]:
    """按 push_channel 推送。auto = 按顺序挑第一个配好的渠道。

    返回 (是否成功, message_id)。message_id 只有飞书应用渠道会有，
    用来把「已读回执」关联到具体哪一集（其它渠道返回空串）。
    """
    channel = (s.get("push_channel") or "auto").lower()
    order = CHANNEL_ORDER if channel == "auto" else [channel]

    targets = [ch for ch in order if ch in CHANNEL_SENDER and (s.get(CHANNEL_FIELDS[ch]) or "").strip()]
    if not targets:
        log.warning("没有配置任何推送渠道，本条消息只打印到控制台：\n%s\n%s\n%s",
                    title, body, "-" * 60)
        return False, ""

    for ch in targets:
        r = CHANNEL_SENDER[ch](title, body, s)
        if isinstance(r, tuple):        # 新渠道返回 (ok, message_id)
            ok, mid = r
        else:                            # 老渠道返回 bool
            ok, mid = bool(r), ""
        if ok:
            return True, mid
        log.warning("渠道「%s」没成功，尝试下一个…", CHANNEL_LABEL.get(ch, ch))
    return False, ""


__all__ = ['_post_json', '_post_form', 'truncate_utf8', '_send_pushplus', 'serverchan_url', '_send_serverchan', '_webhook_result', '_send_webhook', '_feishu_tenant_token', '_send_feishu_app', 'CHANNEL_FIELDS', 'CHANNEL_LABEL', 'CHANNEL_ORDER', 'CHANNEL_SENDER', 'available_channels', 'send_notification']
