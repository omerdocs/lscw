"""Constants: user agents, HTTP headers, limits."""

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
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}
MOBILE_HEADERS = {**DESKTOP_HEADERS, "User-Agent": MOBILE_UA}
SITEMAP_UAS = {
    "googlebot": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "browser": DESKTOP_UA,
}
GUEST_VARY_PATH = "/wp-content/plugins/litespeed-cache/guest.vary.php"

MAX_TABLE_ROWS = 16
CHECKPOINT_SAVE_EVERY = 10
MAX_SITEMAP_DOCS = 200
MAX_WORKERS = 16
