"""Constants: user agents, HTTP headers, limits."""

import re
from importlib.util import find_spec


def _brotli_available() -> bool:
    # urllib3 only decodes `br` when one of these is importable; advertising
    # `br` without them yields an undecodable body and breaks HTML inspection.
    return any(find_spec(name) is not None for name in ("brotli", "brotlicffi"))


BROTLI_AVAILABLE = _brotli_available()
ACCEPT_ENCODING = "gzip, deflate, br" if BROTLI_AVAILABLE else "gzip, deflate"

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.4 Mobile/15E148 Safari/604.1"
)
DESKTOP_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,image/apng,*/*;q=0.8"
)
DESKTOP_HEADERS = {
    "User-Agent": DESKTOP_UA,
    "Accept": DESKTOP_ACCEPT,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": ACCEPT_ENCODING,
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}
MOBILE_HEADERS = {**DESKTOP_HEADERS, "User-Agent": MOBILE_UA}
SITEMAP_UAS = {
    "browser": DESKTOP_UA,
    "googlebot": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
}
GUEST_VARY_PATH = "/wp-content/plugins/litespeed-cache/guest.vary.php"

# LiteSpeed Cache appends its guest script (assets/js/guest.min.js) to every
# page only while Guest Mode is on. That script is the one thing that makes a
# real browser call guest.vary.php, so its presence is the signal to follow.
GUEST_SCRIPT_RE = re.compile(r"guest\.vary\.php|litespeed_vary\s*=\s*document\.cookie")
VARY_MODES = ("auto", "on", "off")

MAX_TABLE_ROWS = 16
CHECKPOINT_SAVE_EVERY = 10
MAX_SITEMAP_DOCS = 200
MAX_WORKERS = 16
