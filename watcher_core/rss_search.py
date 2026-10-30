from __future__ import annotations


def fetch_search_feed(base, keyword, timeout=15, retry=2, proxy='', *, api):
    mirrors = list(dict.fromkeys([base, *api.BASES]))
    query = api.urllib.parse.quote(keyword, safe='')
    for path in ['/rss.xml?keyword=', '/topics/rss/rss.xml?keyword=']:
        for mirror in mirrors:
            attempts = retry if mirror == base and path == '/rss.xml?keyword=' else 1
            body = api.http_get(mirror + path + query, timeout=timeout, retry=attempts, proxy=proxy)
            if api.valid_feed(body):
                return body
    return None
