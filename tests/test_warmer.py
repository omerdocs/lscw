"""Offline tests for the warming flow. No network access is needed."""

from __future__ import annotations

import unittest
from unittest import mock

import requests

from lscw import config, warmer

SITE = "https://example.com"
URL = SITE + "/post/"
VARY_URL = SITE + config.GUEST_VARY_PATH
VARY_HASH = "78af7c1384f93507c535076013a0b18d"

# The script LiteSpeed Cache appends to the page while Guest Mode is on
# (assets/js/guest.min.js with the endpoint path substituted in).
GUEST_SCRIPT = (
    '<script data-no-optimize="1">var litespeed_vary=document.cookie.replace('
    '/(?:(?:^|.*;\\s*)_lscache_vary\\s*\\=\\s*([^;]*).*$)|^.*$/,"$1");'
    'litespeed_vary||fetch("/wp-content/plugins/litespeed-cache/guest.vary.php",'
    '{method:"POST",cache:"no-cache",redirect:"follow"})</script>'
)
PLAIN_PAGE = "<html><body><h1>Post</h1></body></html>"
GUEST_PAGE = "<html><body><h1>Post</h1>" + GUEST_SCRIPT + "</body></html>"


class FakeResponse:
    def __init__(self, url, status=200, text="", headers=None, cookies=None):
        self.url = url
        self.status_code = status
        self.text = text
        self.headers = requests.structures.CaseInsensitiveDict(headers or {})
        self.cookies = cookies or {}


class FakeSession:
    """Records every request and answers like LiteSpeed would.

    `cache` maps a bucket key ("plain" or "vary") to whether it is stored;
    `cacheable_vary` decides whether vary-keyed requests are ever stored.
    """

    def __init__(self, page=PLAIN_PAGE, cacheable_vary=True, shared_mobile=True):
        self.page = page
        self.cacheable_vary = cacheable_vary
        self.shared_mobile = shared_mobile
        self.cache: set = set()
        self.calls: list = []
        self.cookies = requests.cookies.RequestsCookieJar()

    def _bucket(self, headers, cookies):
        mobile = headers.get("User-Agent") == config.MOBILE_UA and not self.shared_mobile
        vary = bool(cookies and cookies.get("_lscache_vary"))
        return ("mobile" if mobile else "desktop") + (":vary" if vary else "")

    def get(self, url, headers=None, cookies=None, **_):
        headers = headers or {}
        bucket = self._bucket(headers, cookies)
        self.calls.append(("GET", bucket))
        if bucket.endswith(":vary") and not self.cacheable_vary:
            status = "miss"
        elif bucket in self.cache:
            status = "hit"
        else:
            self.cache.add(bucket)
            status = "miss"
        return FakeResponse(url, text=self.page, headers={"X-LiteSpeed-Cache": status})

    def post(self, url, **_):
        self.calls.append(("POST", url))
        return FakeResponse(url, text='{"reload":"yes"}', cookies={"_lscache_vary": VARY_HASH})


def run(session, **kw):
    kw.setdefault("delay_between_phases", 0)
    with mock.patch.object(warmer.time, "sleep"):
        return warmer.warm_url(session, URL, SITE, **kw)


class DetectGuestModeTests(unittest.TestCase):
    def test_guest_script_detected(self):
        self.assertTrue(warmer.detect_guest_mode(GUEST_PAGE))

    def test_plain_page_not_detected(self):
        self.assertFalse(warmer.detect_guest_mode(PLAIN_PAGE))

    def test_empty_or_missing_body(self):
        self.assertFalse(warmer.detect_guest_mode(""))
        self.assertFalse(warmer.detect_guest_mode(None))

    def test_should_vary(self):
        self.assertTrue(warmer.should_vary("auto", True))
        self.assertFalse(warmer.should_vary("auto", False))
        self.assertTrue(warmer.should_vary("on", False))
        self.assertFalse(warmer.should_vary("off", True))


class WarmUrlTests(unittest.TestCase):
    def test_auto_without_guest_mode_skips_vary_requests(self):
        s = FakeSession(page=PLAIN_PAGE, cacheable_vary=False)
        r = run(s, vary_mode="auto")
        self.assertEqual(r["phase1_status"], "MISS")
        self.assertEqual(r["phase2_status"], "OFF")
        self.assertEqual(r["phase3_status"], "OFF")
        self.assertFalse(r["guest_mode"])
        self.assertNotIn("POST", [c[0] for c in s.calls])
        self.assertEqual(s.calls, [("GET", "desktop"), ("GET", "desktop")])

    def test_auto_without_guest_mode_reports_shared_mobile_copy(self):
        r = run(FakeSession(page=PLAIN_PAGE, shared_mobile=True), vary_mode="auto")
        self.assertEqual(r["mobile_guest_status"], "HIT")
        self.assertTrue(r["mobile_shared"])

    def test_separate_mobile_copy_is_not_reported_as_shared(self):
        r = run(FakeSession(page=PLAIN_PAGE, shared_mobile=False), vary_mode="auto")
        self.assertEqual(r["mobile_guest_status"], "MISS")
        self.assertFalse(r["mobile_shared"])

    def test_auto_with_guest_mode_runs_vary_phases(self):
        s = FakeSession(page=GUEST_PAGE)
        r = run(s, vary_mode="auto", warm_mobile=False)
        self.assertTrue(r["guest_mode"])
        self.assertTrue(r["guest_vary_ok"])
        self.assertEqual(r["vary_cookie"], VARY_HASH)
        self.assertEqual(r["phase2_status"], "MISS")
        self.assertEqual([c[0] for c in s.calls], ["GET", "POST", "GET"])

    def test_vary_on_forces_lookup_even_without_guest_script(self):
        s = FakeSession(page=PLAIN_PAGE)
        r = run(s, vary_mode="on", warm_mobile=False)
        self.assertFalse(r["guest_mode"])
        self.assertIn("POST", [c[0] for c in s.calls])
        self.assertNotEqual(r["phase2_status"], "OFF")

    def test_vary_off_never_posts(self):
        s = FakeSession(page=GUEST_PAGE)
        r = run(s, vary_mode="off")
        self.assertTrue(r["guest_mode"])
        self.assertEqual(r["phase2_status"], "OFF")
        self.assertEqual(r["phase3_status"], "OFF")
        self.assertNotIn("POST", [c[0] for c in s.calls])

    def test_http_error_stops_after_first_request(self):
        s = FakeSession()
        s.get = lambda url, **_: FakeResponse(url, status=503)
        r = run(s, vary_mode="auto")
        self.assertEqual(r["phase1_status"], "E503")
        self.assertTrue(r["has_error"])
        self.assertIsNone(r["guest_mode"])


class StatsTests(unittest.TestCase):
    def test_counts_off_guest_and_shared(self):
        a = run(FakeSession(page=PLAIN_PAGE), vary_mode="auto")
        b = run(FakeSession(page=GUEST_PAGE), vary_mode="auto", warm_mobile=False)
        st = warmer.compute_stats([a, b])
        self.assertEqual(st["vary_off"], 1)
        self.assertEqual(st["guest_on"], 1)
        self.assertEqual(st["guest_off"], 1)
        self.assertEqual(st["mobile_shared"], 1)
        self.assertEqual(st["vary_ok"], 1)


class AcceptEncodingTests(unittest.TestCase):
    def test_br_only_advertised_with_a_decoder(self):
        self.assertEqual("br" in config.ACCEPT_ENCODING, config.BROTLI_AVAILABLE)
        self.assertEqual(config.DESKTOP_HEADERS["Accept-Encoding"], config.ACCEPT_ENCODING)

    def test_detection_follows_installed_modules(self):
        with mock.patch.object(config, "find_spec", return_value=None):
            self.assertFalse(config._brotli_available())
        with mock.patch.object(config, "find_spec", side_effect=lambda n: object() if n == "brotlicffi" else None):
            self.assertTrue(config._brotli_available())


if __name__ == "__main__":
    unittest.main()
