"""HTTP session management, connectivity check, sitemap crawling."""

from __future__ import annotations

import gzip
import html
import re
from collections import deque
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import DESKTOP_HEADERS, MAX_SITEMAP_DOCS, SITEMAP_UAS
from .ui import esc, log
from .utils import is_valid_http_url, sanitize_url

_LOC_RE = re.compile(
    r"<loc>\s*(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?\s*</loc>",
    re.IGNORECASE | re.DOTALL,
)


def make_session(retries: int = 3, backoff: float = 0.5) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def pre_run_check(session: requests.Session, site_base: str, timeout: int = 10) -> int | None:
    try:
        r = session.get(site_base, headers=DESKTOP_HEADERS, timeout=timeout, allow_redirects=True)
        return r.status_code
    except requests.RequestException:
        return None


def parse_sitemap(session: requests.Session, sitemap_url: str, ua_key: str) -> list[str]:
    urls: list[str] = []
    seen_urls: set[str] = set()
    visited: set[str] = set()
    queue: deque[str] = deque([sitemap_url])
    headers = {"User-Agent": SITEMAP_UAS[ua_key]}

    while queue:
        if len(visited) >= MAX_SITEMAP_DOCS:
            log("WARN", f"Sitemap limit reached ({MAX_SITEMAP_DOCS} documents), stopping crawl.")
            break
        url = queue.popleft()
        if url in visited:
            continue
        visited.add(url)

        try:
            r = session.get(url, headers=headers, timeout=20)
            r.raise_for_status()
            content = r.content
            if url.endswith(".gz") or content[:2] == b"\x1f\x8b":
                content = gzip.decompress(content)
            text = content.decode("utf-8", errors="replace")
        except Exception as e:
            log("WARN", f"Failed to fetch sitemap: {esc(url)} → {e}")
            continue

        for loc in _LOC_RE.findall(text):
            loc = sanitize_url(html.unescape(loc))
            if not is_valid_http_url(loc):
                continue
            path = urlparse(loc).path
            if path.endswith((".xml", ".xml.gz")):
                if loc not in visited:
                    queue.append(loc)
            elif loc not in seen_urls:
                seen_urls.add(loc)
                urls.append(loc)

    return urls
