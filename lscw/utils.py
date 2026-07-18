"""Pure helpers with no project dependencies."""

from __future__ import annotations

import re
from urllib.parse import urlparse


def sanitize_url(url: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", "", url.strip())


def is_valid_http_url(url: str) -> bool:
    p = urlparse(url)
    return p.scheme in ("http", "https") and bool(p.netloc)


def same_host(url: str, site_host: str) -> bool:
    h = urlparse(url).netloc.lower()
    s = site_host.lower()
    return h == s or h == f"www.{s}" or s == f"www.{h}"


def short_url(url: str, maxlen: int = 56) -> str:
    short = url.split("//", 1)[-1]
    return short[:maxlen - 3] + "…" if len(short) > maxlen else short
