"""Core warming logic: vary-cookie handling and per-URL cache warming."""

from __future__ import annotations

import re
import time
from urllib.parse import urljoin, urlparse

import requests

from .config import DESKTOP_HEADERS, GUEST_VARY_PATH, MOBILE_HEADERS
from .utils import same_host


def check_cache_status(headers: dict) -> str:
    lsc = headers.get("X-LiteSpeed-Cache", "").lower()
    if "hit" in lsc:       return "HIT"
    if "miss" in lsc:      return "MISS"
    if "no-cache" in lsc:  return "NO-CACHE"
    return "UNKNOWN"


def http_error_label(status_code: int) -> str | None:
    return f"E{status_code}" if status_code >= 400 else None


def get_vary_cookie_from_page(html_text: str) -> str | None:
    for pattern in [
        r"_lscache_vary\s*[=:]\s*['\"]([^'\"]+)['\"]",
        r"lscacheVary\s*=\s*['\"]([^'\"]+)['\"]",
        r"\"vary\"\s*:\s*\"([^\"]+)\"",
    ]:
        m = re.search(pattern, html_text)
        if m:
            return m.group(1)
    return None


def looks_like_vary_hash(value: str | None) -> bool:
    # Real _lscache_vary values are opaque hashes; plaintext like
    # "device:desktop" is a placeholder and would warm a phantom bucket.
    if not value:
        return False
    v = value.strip()
    if len(v) < 8 or ":" in v or " " in v or "," in v:
        return False
    return re.fullmatch(r"[A-Za-z0-9_\-]+", v) is not None


def clear_vary_cookie(session: requests.Session) -> None:
    # The guest phase must be cookie-less; a vary cookie left in the session
    # jar from the previous URL would silently invalidate it.
    jar = session.cookies
    for c in list(jar):
        if c.name == "_lscache_vary":
            try:
                jar.clear(c.domain, c.path, c.name)
            except KeyError:
                pass


def fetch_live_vary(
    session: requests.Session,
    site_base: str,
    referer_url: str,
    base_headers: dict,
    timeout: int = 10,
    inline_vary: str | None = None,
) -> tuple[str | None, str | None]:
    """
    Asks guest.vary.php for the current vary cookie, mirroring the plugin's own
    JS. Returns (value, source) or (None, None) — never a hardcoded fallback.
    base_headers decides the variant: MOBILE_HEADERS yields the mobile key.
    """
    guest_vary_url = urljoin(site_base + "/", GUEST_VARY_PATH.lstrip("/"))
    value: str | None = None
    source: str | None = None

    try:
        r_vary = session.post(
            guest_vary_url,
            headers={
                **base_headers,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": referer_url,
                "Origin": site_base,
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
            },
            data={
                "LSCWP_CTRL": "before_cloud_init",
                "action": "vary_update",
                "referrer": referer_url,
            },
            timeout=timeout,
        )
        if "_lscache_vary" in r_vary.cookies:
            value, source = r_vary.cookies["_lscache_vary"], "vary.php"
        elif "Set-Cookie" in r_vary.headers:
            m = re.search(r"_lscache_vary=([^;]+)", r_vary.headers.get("Set-Cookie", ""))
            if m:
                value, source = m.group(1), "set-cookie"
    except requests.RequestException:
        pass

    if not looks_like_vary_hash(value) and inline_vary:
        value, source = inline_vary, "inline"

    if not looks_like_vary_hash(value):
        return None, None
    return value, source


def empty_result(url: str) -> dict:
    return {
        "url": url,
        "phase1_status": "SKIP",
        "guest_vary_ok": False,
        "phase2_status": "SKIP",
        "mobile_guest_status": "SKIP",
        "phase3_status": "SKIP",
        "vary_cookie": None,
        "vary_source": None,
        "mobile_vary_ok": False,
        "has_error": False,
        "got_429": False,
        "no_vary": False,
    }


def crashed_result(url: str) -> dict:
    r = empty_result(url)
    r["phase1_status"] = "ERROR"
    r["has_error"] = True
    return r


def warm_url(
    session: requests.Session,
    url: str,
    site_base: str,
    warm_mobile: bool = True,
    timeout: int = 30,
    delay_between_phases: float = 0.3,
) -> dict:
    """
    Warms only cache buckets that real visitors actually land in:
      1. Cookie-less guest request  → guest bucket (always warmed).
      2. Live vary lookup via guest.vary.php.
      3. Only with a real vary key → full bucket. No hardcoded fallback:
         a missing key is reported as NO-VARY instead of warming a stale bucket.
    Mobile repeats the same flow with the mobile UA end to end.
    """
    result = empty_result(url)
    site_host = urlparse(site_base).netloc

    # ── Phase 1: cookie-less desktop guest request ───────────────────────────
    clear_vary_cookie(session)
    try:
        r1 = session.get(url, headers=DESKTOP_HEADERS, timeout=timeout, allow_redirects=True)
    except requests.RequestException:
        result["phase1_status"] = "ERROR"
        result["has_error"] = True
        return result

    err = http_error_label(r1.status_code)
    if err:
        result["phase1_status"] = err
        result["got_429"] = r1.status_code == 429
        result["has_error"] = True
        return result
    if not same_host(r1.url, site_host):
        result["phase1_status"] = "EXT-REDIR"
        return result

    result["phase1_status"] = check_cache_status(r1.headers)
    inline_vary = get_vary_cookie_from_page(r1.text)

    time.sleep(delay_between_phases)

    # ── Phase 2: live desktop vary lookup ────────────────────────────────────
    vary_value, vary_source = fetch_live_vary(
        session, site_base, url, DESKTOP_HEADERS,
        timeout=min(timeout, 15), inline_vary=inline_vary,
    )
    result["vary_cookie"] = vary_value
    result["vary_source"] = vary_source
    result["guest_vary_ok"] = vary_value is not None

    # ── Phase 3: full desktop bucket, only with a real key ───────────────────
    if vary_value is None:
        result["phase2_status"] = "NO-VARY"
        result["no_vary"] = True
    else:
        time.sleep(delay_between_phases)
        try:
            clear_vary_cookie(session)
            r2 = session.get(
                url,
                headers=DESKTOP_HEADERS,
                cookies={"_lscache_vary": vary_value},
                timeout=timeout,
                allow_redirects=True,
            )
            err = http_error_label(r2.status_code)
            if err:
                result["phase2_status"] = err
                result["got_429"] = result["got_429"] or r2.status_code == 429
                result["has_error"] = True
            else:
                result["phase2_status"] = check_cache_status(r2.headers)
        except requests.RequestException:
            result["phase2_status"] = "ERROR"
            result["has_error"] = True

    # ── Mobile: cookie-less guest request, then its own vary key ─────────────
    if warm_mobile:
        time.sleep(delay_between_phases)
        mob_inline = None
        mobile_guest_ok = False
        try:
            clear_vary_cookie(session)
            rm1 = session.get(url, headers=MOBILE_HEADERS, timeout=timeout, allow_redirects=True)
            err = http_error_label(rm1.status_code)
            if err:
                result["mobile_guest_status"] = err
                result["got_429"] = result["got_429"] or rm1.status_code == 429
                result["has_error"] = True
            else:
                result["mobile_guest_status"] = check_cache_status(rm1.headers)
                mob_inline = get_vary_cookie_from_page(rm1.text)
                mobile_guest_ok = True
        except requests.RequestException:
            result["mobile_guest_status"] = "ERROR"
            result["has_error"] = True

        if mobile_guest_ok:
            time.sleep(delay_between_phases)
            mob_vary, _ = fetch_live_vary(
                session, site_base, url, MOBILE_HEADERS,
                timeout=min(timeout, 15), inline_vary=mob_inline,
            )
            result["mobile_vary_ok"] = mob_vary is not None

            if mob_vary is None:
                result["phase3_status"] = "NO-VARY"
            else:
                try:
                    clear_vary_cookie(session)
                    r3 = session.get(
                        url,
                        headers=MOBILE_HEADERS,
                        cookies={"_lscache_vary": mob_vary},
                        timeout=timeout,
                        allow_redirects=True,
                    )
                    err = http_error_label(r3.status_code)
                    if err:
                        result["phase3_status"] = err
                        result["got_429"] = result["got_429"] or r3.status_code == 429
                        result["has_error"] = True
                    else:
                        result["phase3_status"] = check_cache_status(r3.headers)
                except requests.RequestException:
                    result["phase3_status"] = "ERROR"
                    result["has_error"] = True

    clear_vary_cookie(session)
    return result


def compute_stats(results) -> dict:
    stats = {"guest_hit": 0, "guest_miss": 0, "full_hit": 0, "full_miss": 0,
             "mobile_guest_hit": 0, "mobile_guest_miss": 0,
             "mobile_full_hit": 0, "mobile_full_miss": 0,
             "errors": 0, "vary_ok": 0, "no_vary": 0, "rate_limited": 0}
    for r in results:
        for key, prefix in (
            ("phase1_status", "guest"),
            ("phase2_status", "full"),
            ("mobile_guest_status", "mobile_guest"),
            ("phase3_status", "mobile_full"),
        ):
            if r[key] == "HIT":
                stats[f"{prefix}_hit"] += 1
            elif r[key] == "MISS":
                stats[f"{prefix}_miss"] += 1
        if r["guest_vary_ok"]:
            stats["vary_ok"] += 1
        if r["no_vary"]:
            stats["no_vary"] += 1
        if r["has_error"]:
            stats["errors"] += 1
        if r["got_429"]:
            stats["rate_limited"] += 1
    return stats
