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
BASES = [
    "https://share.acgnx.cc",
    "https://share.acgnx.net",
    "https://share.acgnx.se",
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
_REQUEST_DEADLINE = None


def http_get(url: str, timeout: int = 20, retry: int = 3, proxy: str = "") -> str | None:
    global _REQUEST_DEADLINE
    seconds = int(os.environ.get('ACGNX_REQUEST_BUDGET_SECONDS', '0'))
    if seconds and _REQUEST_DEADLINE is None:
        _REQUEST_DEADLINE = time.monotonic() + seconds
    last_err = None
    for attempt in range(1, retry + 1):
        try:
            remaining = timeout if _REQUEST_DEADLINE is None else min(timeout, _REQUEST_DEADLINE - time.monotonic())
            if remaining <= 0:
                log.warning('抓取预算已用完，剩余搜索下轮重试')
                return None
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
            with opener.open(req, timeout=remaining) as resp:
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
                delay = 1.5 * attempt
                if _REQUEST_DEADLINE is not None:
                    delay = max(0, min(delay, _REQUEST_DEADLINE - time.monotonic()))
                time.sleep(delay)
    log.warning("请求失败(%s)：%s | %s", retry, url, last_err)
    return None


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


__all__ = ['BASES', 'UA', 'http_get', 'pick_base']
