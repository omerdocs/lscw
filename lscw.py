#!/usr/bin/env python3
"""
LiteSpeed Cache Warmer | LSCW 
==================================================
Usage:
    python3 lscw.py --site https://yoursite.com
    python3 lscw.py --site https://yoursite.com --resume
    python3 lscw.py --site https://yoursite.com --delay X --workers X
    python3 lscw.py --site https://yoursite.com --sitemap https://yoursite.com/sitemap.xml
    python3 lscw.py --site https://yoursite.com --urls-file urls.txt --dry-run

Requirements:
-- pip install requests lxml rich
"""

import argparse
import time
import sys
import re
import json
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from threading import Lock

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    print("❌ 'requests' library not found. To install: pip install requests")
    sys.exit(1)

try:
    from lxml import etree  # noqa: F401
    LXML_AVAILABLE = True
except ImportError:
    LXML_AVAILABLE = False

try:
    from rich.console import Console, Group
    from rich.live import Live
    from rich.table import Table
    from rich.panel import Panel
    from rich.progress import (
        Progress, SpinnerColumn, BarColumn,
        TextColumn, TimeElapsedColumn, TimeRemainingColumn, MofNCompleteColumn,
    )
    from rich.text import Text
    from rich.rule import Rule
    from rich import box
    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    RICH_AVAILABLE = False
    console = None  # type: ignore[assignment]


# ─── Constants ───────────────────────────────────────────────────────────────

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
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}
MOBILE_HEADERS = {**DESKTOP_HEADERS, "User-Agent": MOBILE_UA}
GUEST_VARY_PATH = "/wp-content/plugins/litespeed-cache/guest.vary.php"

MAX_TABLE_ROWS = 16
CHECKPOINT_SAVE_EVERY = 10


# ─── State Management ────────────────────────────────────────────────────────

def checkpoint_path(site_base: str) -> Path:
    domain = urlparse(site_base).netloc.replace(".", "_").replace(":", "_")
    return Path(f".lscache_{domain}.checkpoint.json")

def load_checkpoint(path: Path) -> set:
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return set(data.get("completed", []))
        except Exception:
            pass
    return set()

def save_checkpoint(path: Path, completed: set) -> None:
    try:
        path.write_text(
            json.dumps(
                {"completed": list(completed), "saved_at": datetime.now().isoformat()},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


class AdaptiveDelay:
    """Dynamically adjusts request delays based on HTTP response success/failure streaks."""
    def __init__(self, base: float, max_factor: float = 4.0):
        self.base = base
        self.current = base
        self.max = base * max_factor
        self._ok_streak = 0
        self._err_streak = 0
        self._lock = Lock()

    @property
    def value(self) -> float:
        return self.current

    def on_success(self) -> None:
        with self._lock:
            self._err_streak = 0
            self._ok_streak += 1
            if self._ok_streak >= 10 and self.current > self.base:
                self.current = max(self.base, round(self.current * 0.75, 2))
                self._ok_streak = 0

    def on_problem(self) -> None:
        with self._lock:
            self._ok_streak = 0
            self._err_streak += 1
            if self._err_streak >= 2:
                self.current = min(self.max, round(self.current * 2.0, 2))
                self._err_streak = 0


# ─── Network Utilities ───────────────────────────────────────────────────────

def plain_log(level: str, msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    icons = {"INFO": "ℹ️ ", "OK": "✅", "WARN": "⚠️ ", "ERR": "❌"}
    print(f"[{ts}] {icons.get(level, '•')} {msg}", flush=True)

def rich_log(level: str, msg: str) -> None:
    styles = {"INFO": "cyan", "OK": "bold green", "WARN": "yellow", "ERR": "bold red"}
    icons  = {"INFO": "ℹ", "OK": "✓", "WARN": "⚠", "ERR": "✗"}
    style = styles.get(level, "white")
    icon  = icons.get(level, "•")
    console.print(f"  [{style}]{icon}[/{style}]  {msg}")

def log(level: str, msg: str) -> None:
    if RICH_AVAILABLE:
        rich_log(level, msg)
    else:
        plain_log(level, msg)

def make_session(retries: int = 3, backoff: float = 0.5) -> requests.Session:
    """Creates a requests session with robust retry logic for 5xx errors."""
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

def pre_run_check(site_base: str, timeout: int = 10) -> bool:
    try:
        r = requests.get(site_base, headers=DESKTOP_HEADERS, timeout=timeout, allow_redirects=True)
        return r.status_code < 500
    except Exception:
        return False

def parse_sitemap(session: requests.Session, sitemap_url: str) -> list[str]:
    urls: list[str] = []
    visited: set[str] = set()
    queue = [sitemap_url]
    googlebot = {"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"}

    while queue:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)

        try:
            r = session.get(url, headers=googlebot, timeout=20)
            r.raise_for_status()
        except Exception as e:
            plain_log("WARN", f"Failed to fetch sitemap: {url} → {e}")
            continue

        for loc in re.findall(r'<loc>(.*?)</loc>', r.text, re.IGNORECASE):
            loc = loc.strip()
            if loc.endswith(".xml"):
                if loc not in visited:
                    queue.append(loc)
            elif loc not in urls:
                urls.append(loc)

    return urls

def get_vary_cookie_from_page(html: str) -> str | None:
    for pattern in [
        r"_lscache_vary\s*[=:]\s*['\"]([^'\"]+)['\"]",
        r"lscacheVary\s*=\s*['\"]([^'\"]+)['\"]",
        r"\"vary\"\s*:\s*\"([^\"]+)\"",
    ]:
        m = re.search(pattern, html)
        if m:
            return m.group(1)
    return None

def check_cache_status(headers: dict) -> str:
    lsc = headers.get("X-LiteSpeed-Cache", "").lower()
    if "hit" in lsc:       return "HIT"
    if "miss" in lsc:      return "MISS"
    if "no-cache" in lsc:  return "NO-CACHE"
    return "UNKNOWN"


# ─── Core Logic ──────────────────────────────────────────────────────────────

def warm_url(
    session: requests.Session,
    url: str,
    site_base: str,
    warm_mobile: bool = True,
    timeout: int = 30,
    delay_between_phases: float = 0.3,
) -> dict:
    """
    Executes the 3-phase LiteSpeed cache warming process:
    1. Initial Guest request (MISS)
    2. AJAX request to guest.vary.php to acquire the privilege/vary cookie
    3. Secondary request using the vary cookie to generate the full cached page
    4. Optional mobile request using the mobile vary cookie
    """
    result: dict = {
        "url": url,
        "phase1_status": "SKIP",
        "guest_vary_ok": False,
        "phase2_status": "SKIP",
        "phase3_status": "SKIP",
        "vary_cookie": None,
        "has_error": False,
        "got_429": False,
    }

    guest_vary_url = urljoin(site_base, GUEST_VARY_PATH)

    # Phase 1: Standard Guest Request
    try:
        r1 = session.get(url, headers=DESKTOP_HEADERS, timeout=timeout, allow_redirects=True)
        if r1.status_code == 429:
            result["got_429"] = True
        result["phase1_status"] = check_cache_status(r1.headers)
        inline_vary = get_vary_cookie_from_page(r1.text)
    except Exception:
        result["phase1_status"] = "ERROR"
        result["has_error"] = True
        return result

    time.sleep(delay_between_phases)

    # Phase 2: Vary Cookie Update via AJAX
    vary_cookie_value: str | None = None
    try:
        r_vary = session.post(
            guest_vary_url,
            headers={
                **DESKTOP_HEADERS,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": url,
                "Origin": site_base,
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
            },
            data={
                "LSCWP_CTRL": "before_cloud_init",
                "action": "vary_update",
                "referrer": url,
            },
            timeout=10,
        )
        if "_lscache_vary" in r_vary.cookies:
            vary_cookie_value = r_vary.cookies["_lscache_vary"]
            result["guest_vary_ok"] = True
        elif "Set-Cookie" in r_vary.headers:
            m = re.search(r"_lscache_vary=([^;]+)", r_vary.headers.get("Set-Cookie", ""))
            if m:
                vary_cookie_value = m.group(1)
                result["guest_vary_ok"] = True

        if not vary_cookie_value and inline_vary:
            vary_cookie_value = inline_vary
            result["guest_vary_ok"] = True

        if not vary_cookie_value:
            vary_cookie_value = "78af7c1384f93507c535076013a0b18d" # Fallback hash
    except Exception:
        vary_cookie_value = "device:desktop"

    result["vary_cookie"] = vary_cookie_value
    time.sleep(delay_between_phases)

    # Phase 3: Full Cache Generation using Vary Cookie
    try:
        r2 = session.get(
            url,
            headers={**DESKTOP_HEADERS},
            cookies={"_lscache_vary": vary_cookie_value},
            timeout=timeout,
            allow_redirects=True,
        )
        if r2.status_code == 429:
            result["got_429"] = True
        result["phase2_status"] = check_cache_status(r2.headers)
    except Exception:
        result["phase2_status"] = "ERROR"
        result["has_error"] = True

    # Optional: Mobile Cache Generation
    if warm_mobile:
        time.sleep(delay_between_phases)
        try:
            mob_cookie = vary_cookie_value.replace("device:desktop", "device:mobile")
            if mob_cookie == vary_cookie_value:
                mob_cookie = "device:mobile"
            r3 = session.get(
                url,
                headers=MOBILE_HEADERS,
                cookies={"_lscache_vary": mob_cookie},
                timeout=timeout,
                allow_redirects=True,
            )
            if r3.status_code == 429:
                result["got_429"] = True
            result["phase3_status"] = check_cache_status(r3.headers)
        except Exception:
            result["phase3_status"] = "ERROR"
            result["has_error"] = True

    return result


# ─── CLI Rendering ───────────────────────────────────────────────────────────

def _status_cell(s: str) -> "Text":
    if s == "HIT":       return Text("🔥 HIT",    style="bold green")
    if s == "MISS":      return Text("📝 MISS",   style="yellow")
    if s == "SKIP":      return Text("─",          style="dim")
    if s == "NO-CACHE":  return Text("🚫 N/C",    style="dim red")
    if "ERROR" in s:     return Text("❌ ERR",    style="bold red")
    return Text(s[:8],                              style="dim")

def _build_results_table(rows: list[dict]) -> "Table":
    t = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold cyan",
        expand=True,
        show_edge=False,
        padding=(0, 1),
    )
    t.add_column("#",        width=6,  justify="right", style="dim")
    t.add_column("URL",      min_width=38, no_wrap=True)
    t.add_column("Guest",    width=10, justify="center")
    t.add_column("vary.php", width=9,  justify="center")
    t.add_column("Full",     width=10, justify="center")
    t.add_column("Mobile",   width=10, justify="center")

    for r in rows:
        short = r["url"].split("//", 1)[-1]
        if len(short) > 56:
            short = short[:53] + "…"
        gv = Text("✓", style="bold green") if r["guest_vary_ok"] else Text("✗", style="red")
        t.add_row(
            str(r.get("_idx", "?")),
            short,
            _status_cell(r["phase1_status"]),
            gv,
            _status_cell(r["phase2_status"]),
            _status_cell(r["phase3_status"]),
        )
    return t

def _build_stats_panel(stats: dict, elapsed: float, delay: float) -> "Panel":
    done = stats["guest_hit"] + stats["guest_miss"] + stats["errors"]
    ppu  = elapsed / done if done else 0.0
    txt = (
        f"[green]🔥 HIT[/green]  "
        f"Guest [bold]{stats['guest_hit']}[/bold]  Full [bold]{stats['full_hit']}[/bold]"
        f"   [yellow]📝 MISS[/yellow]  "
        f"Guest [bold]{stats['guest_miss']}[/bold]  Full [bold]{stats['full_miss']}[/bold]"
        f"   [cyan]vary✓ [bold]{stats['vary_ok']}[/bold][/cyan]"
        f"   [red]Error [bold]{stats['errors']}[/bold][/red]"
        f"   [dim]⏱ {ppu:.1f}s/url  delay {delay:.1f}s[/dim]"
    )
    return Panel(txt, border_style="bright_black", padding=(0, 1))

def run_warming(
    urls: list[str],
    session: requests.Session,
    site_base: str,
    args,
    adaptive: AdaptiveDelay,
    checkpoint_set: set,
    cp_path: Path | None,
) -> tuple[list[dict], dict]:

    total   = len(urls)
    stats   = {"guest_hit": 0, "guest_miss": 0, "full_hit": 0, "full_miss": 0,
               "errors": 0, "vary_ok": 0}
    all_results: list[dict] = []
    recent_rows: list[dict] = []
    start_time = time.time()
    lock = Lock()
    done_count = [0]

    def _update_stats(result: dict) -> None:
        p1, p2 = result["phase1_status"], result["phase2_status"]
        with lock:
            if p1 == "HIT":           stats["guest_hit"] += 1
            elif p1 == "MISS":        stats["guest_miss"] += 1
            if p2 == "HIT":           stats["full_hit"] += 1
            elif p2 == "MISS":        stats["full_miss"] += 1
            if result["guest_vary_ok"]: stats["vary_ok"] += 1
            if result["has_error"]:   stats["errors"] += 1
            all_results.append(result)
            recent_rows.append(result)
            if len(recent_rows) > MAX_TABLE_ROWS:
                recent_rows.pop(0)
            done_count[0] += 1
            if cp_path and done_count[0] % CHECKPOINT_SAVE_EVERY == 0:
                checkpoint_set.add(result["url"])
                save_checkpoint(cp_path, checkpoint_set)

        if result.get("got_429") or result["has_error"]:
            adaptive.on_problem()
        else:
            adaptive.on_success()

    if RICH_AVAILABLE:
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=None),
            MofNCompleteColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            TextColumn("ETA"),
            TimeRemainingColumn(),
            console=console,
            expand=True,
        )
        task = progress.add_task("🔥 Warming up cache...", total=total)

        def _renderable():
            return Group(
                Panel(progress, title="[bold blue]🚀 LiteSpeed Cache Warmer v2[/bold blue]",
                      border_style="blue"),
                _build_results_table(recent_rows),
                _build_stats_panel(stats, time.time() - start_time, adaptive.value),
            )

        with Live(_renderable(), console=console, refresh_per_second=4) as live:
            if args.workers == 1:
                for i, url in enumerate(urls, 1):
                    result = warm_url(
                        session, url, site_base,
                        warm_mobile=args.mobile,
                        timeout=args.timeout,
                        delay_between_phases=args.phase_delay,
                    )
                    result["_idx"] = i
                    _update_stats(result)
                    progress.update(task, advance=1)
                    live.update(_renderable())
                    if i < total:
                        time.sleep(adaptive.value)
            else:
                with ThreadPoolExecutor(max_workers=args.workers) as executor:
                    futures = {
                        executor.submit(
                            warm_url, make_session(), url, site_base,
                            args.mobile, args.timeout, args.phase_delay,
                        ): (i, url)
                        for i, url in enumerate(urls, 1)
                    }
                    for future in as_completed(futures):
                        idx, _ = futures[future]
                        try:
                            result = future.result()
                            result["_idx"] = idx
                            _update_stats(result)
                            progress.update(task, advance=1)
                            live.update(_renderable())
                        except Exception:
                            with lock:
                                stats["errors"] += 1

    else:
        print(f"\n  {'URL':<62} | {'Guest':<9} | {'vary':^7} | {'Full':<9} | {'Mobile':<9}")
        print("  " + "─" * 110)

        if args.workers == 1:
            for i, url in enumerate(urls, 1):
                result = warm_url(
                    session, url, site_base,
                    warm_mobile=args.mobile,
                    timeout=args.timeout,
                    delay_between_phases=args.phase_delay,
                )
                result["_idx"] = i
                _update_stats(result)
                short = url.split("//", 1)[-1][:60]
                gv = "✓" if result["guest_vary_ok"] else "✗"
                print(
                    f"  [{i:4d}/{total}] {short:<54} "
                    f"| {result['phase1_status']:<9} | {gv:^7} "
                    f"| {result['phase2_status']:<9} | {result['phase3_status']:<9}"
                )
                if i < total:
                    time.sleep(adaptive.value)
        else:
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = {
                    executor.submit(
                        warm_url, make_session(), url, site_base,
                        args.mobile, args.timeout, args.phase_delay,
                    ): (i, url)
                    for i, url in enumerate(urls, 1)
                }
                done = 0
                for future in as_completed(futures):
                    idx, _ = futures[future]
                    try:
                        result = future.result()
                        done += 1
                        result["_idx"] = idx
                        _update_stats(result)
                        short = result["url"].split("//", 1)[-1][:60]
                        print(
                            f"  [{done:4d}/{total}] {short:<54} "
                            f"| {result['phase1_status']:<9} | {'✓' if result['guest_vary_ok'] else '✗':^7} "
                            f"| {result['phase2_status']:<9} | {result['phase3_status']:<9}"
                        )
                    except Exception:
                        with lock:
                            stats["errors"] += 1

    return all_results, stats

def _print_summary(stats: dict, total: int, elapsed: float, retried: int = 0) -> None:
    if RICH_AVAILABLE:
        console.print()
        t = Table(
            box=box.ROUNDED,
            title="📊  Result Summary",
            title_style="bold",
            show_header=False,
            border_style="blue",
            padding=(0, 2),
        )
        t.add_column("Metric", style="cyan",       min_width=22)
        t.add_column("Value",  style="bold white", min_width=12)
        t.add_row("Total URLs",        str(total))
        t.add_row("Total time",        f"{elapsed:.1f}s  ({elapsed/max(total,1):.1f}s/url)")
        t.add_row("vary.php success",  str(stats["vary_ok"]))
        t.add_row("Guest cache HIT",   f"[green]{stats['guest_hit']}[/green]")
        t.add_row("Guest cache MISS",  f"[yellow]{stats['guest_miss']}[/yellow]")
        t.add_row("Full cache HIT",    f"[green]{stats['full_hit']}[/green]")
        t.add_row("Full cache MISS",   f"[yellow]{stats['full_miss']}[/yellow]")
        if retried:
            t.add_row("Auto-retried",  str(retried))
        t.add_row("Errors",            f"[red]{stats['errors']}[/red]" if stats["errors"] else "[green]0[/green]")
        console.print(t)
        console.print()
    else:
        print(f"\n{'='*60}")
        print("  RESULT SUMMARY")
        print(f"{'='*60}")
        print(f"  Total    : {total}    Time: {elapsed:.1f}s ({elapsed/max(total,1):.1f}s/url)")
        print(f"  vary.php : {stats['vary_ok']}")
        print(f"  Guest    : HIT {stats['guest_hit']}  MISS {stats['guest_miss']}")
        print(f"  Full     : HIT {stats['full_hit']}  MISS {stats['full_miss']}")
        if retried:
            print(f"  Retries  : {retried}")
        print(f"  Errors   : {stats['errors']}")
        print(f"{'='*60}\n")


# ─── Main Execution ──────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="LiteSpeed Cache Warmer | LSCW",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--site",        required=True,                    help="Site URL (e.g., https://mysite.com)")
    parser.add_argument("--sitemap",                                       help="Sitemap URL. If not specified, <site>/sitemap.xml is tried")
    parser.add_argument("--urls-file",                                     help="Text file containing URLs line by line")
    parser.add_argument("--delay",       type=float, default=1.0,          help="Delay between URLs in seconds (default: 1.0)")
    parser.add_argument("--phase-delay", type=float, default=0.3,          help="Delay between phases in seconds (default: 0.3)")
    parser.add_argument("--mobile",      action="store_true", default=True, help="Also warm up mobile cache (default: True)")
    parser.add_argument("--workers",     type=int,   default=1,            help="Number of parallel workers (shared hosting: 1-2)")
    parser.add_argument("--timeout",     type=int,   default=30,           help="HTTP timeout in seconds (default: 30)")
    parser.add_argument("--start-from",  type=int,   default=1,            help="URL index to start from (default: 1)")
    parser.add_argument("--limit",       type=int,                         help="Maximum number of URLs to process")
    parser.add_argument("--resume",      action="store_true",              help="Resume from where it left off using the checkpoint file")
    parser.add_argument("--dry-run",     action="store_true",              help="List URLs and exit without making requests")
    args = parser.parse_args()

    site_base   = args.site.rstrip("/")
    sitemap_url = args.sitemap or f"{site_base}/sitemap.xml"
    cp_path     = checkpoint_path(site_base)

    if RICH_AVAILABLE:
        console.print()
        console.print(Panel.fit(
            f"[bold]Site:[/bold]        {site_base}\n"
            f"[bold]Delay:[/bold]       {args.delay}s    "
            f"[bold]Phase delay:[/bold] {args.phase_delay}s\n"
            f"[bold]Mobile:[/bold]      {'✅ Active' if args.mobile else '❌ Inactive'}    "
            f"[bold]Workers:[/bold] {args.workers}    "
            f"[bold]Timeout:[/bold] {args.timeout}s\n"
            f"[bold]Resume:[/bold]      {'✅' if args.resume else '❌'}    "
            f"[bold]Adaptive delay:[/bold] ✅",
            title="[bold blue]LiteSpeed Cache Warmer | LSCW[/bold blue]",
            border_style="blue",
        ))
        console.print()
    else:
        print(f"\n{'='*65}")
        print("  LiteSpeed Cache Warmer | LSCW")
        print(f"  Site: {site_base}")
        print(f"{'='*65}\n")

    log("INFO", "Checking connection...")
    if not pre_run_check(site_base, timeout=args.timeout):
        log("ERR", f"Site is unreachable: {site_base} — Stopping script.")
        sys.exit(1)
    log("OK", "Connection successful.")

    session = make_session()

    if args.urls_file:
        log("INFO", f"Reading URL file: {args.urls_file}")
        try:
            with open(args.urls_file, "r", encoding="utf-8") as f:
                urls = [ln.strip() for ln in f if ln.strip().startswith("http")]
        except FileNotFoundError:
            log("ERR", f"File not found: {args.urls_file}")
            sys.exit(1)
    else:
        log("INFO", f"Fetching sitemap: {sitemap_url}")
        urls = parse_sitemap(session, sitemap_url)
        if not urls:
            log("ERR", "Could not fetch URLs from sitemap! Check --urls-file or --sitemap option.")
            sys.exit(1)

    start = max(0, args.start_from - 1)
    urls = urls[start:]
    if args.limit:
        urls = urls[:args.limit]

    checkpoint_set: set = set()
    if args.resume and cp_path.exists():
        checkpoint_set = load_checkpoint(cp_path)
        before = len(urls)
        urls = [u for u in urls if u not in checkpoint_set]
        skipped = before - len(urls)
        log("INFO", f"Checkpoint: {skipped} URLs skipped, {len(urls)} URLs remaining.")
    elif args.resume:
        log("WARN", "Checkpoint file not found, starting from the beginning.")

    if not urls:
        log("OK", "No URLs to process. You can start over by deleting the checkpoint file.")
        sys.exit(0)

    log("INFO", f"Total of {len(urls)} URLs will be processed.")
    if RICH_AVAILABLE:
        console.print()

    if args.dry_run:
        print("\n  --- Dry Run: URL List ---")
        for i, u in enumerate(urls, 1):
            print(f"  {i:5d}. {u}")
        print(f"\n  Total: {len(urls)} URLs\n")
        return

    adaptive   = AdaptiveDelay(args.delay)
    start_time = time.time()

    all_results, stats = run_warming(
        urls, session, site_base, args, adaptive, checkpoint_set, cp_path,
    )

    elapsed = time.time() - start_time

    for r in all_results:
        if not r["has_error"]:
            checkpoint_set.add(r["url"])
    save_checkpoint(cp_path, checkpoint_set)

    failed_urls = [r["url"] for r in all_results if r["has_error"]]
    retried_count = 0

    if failed_urls:
        if RICH_AVAILABLE:
            console.print(f"\n[yellow]⚠️  Retrying {len(failed_urls)} failed URLs "
                          f"(higher timeout, slower delay)...[/yellow]\n")
        else:
            print(f"\n⚠️  Retrying {len(failed_urls)} failed URLs...")

        retry_session = make_session(retries=2, backoff=1.5)
        retry_delay   = min(args.delay * 2, 5.0)

        for url in failed_urls:
            time.sleep(retry_delay)
            r = warm_url(
                retry_session, url, site_base,
                warm_mobile=args.mobile,
                timeout=args.timeout * 2,
                delay_between_phases=args.phase_delay,
            )
            retried_count += 1
            p1, p2 = r["phase1_status"], r["phase2_status"]
            ok = not r["has_error"]

            if p1 == "HIT":       stats["guest_hit"] += 1
            elif p1 == "MISS":    stats["guest_miss"] += 1
            if p2 == "HIT":       stats["full_hit"] += 1
            elif p2 == "MISS":    stats["full_miss"] += 1
            if r["guest_vary_ok"]: stats["vary_ok"] += 1
            if ok:
                stats["errors"] = max(0, stats["errors"] - 1)
                checkpoint_set.add(url)

            label = url.split("//", 1)[-1][:65]
            if RICH_AVAILABLE:
                icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
                console.print(f"  {icon} Retry: {label}  Guest:{p1}  Full:{p2}")
            else:
                print(f"  {'✓' if ok else '✗'} Retry: {label}  G:{p1}  F:{p2}")

        save_checkpoint(cp_path, checkpoint_set)

    if stats["errors"] == 0:
        try:
            cp_path.unlink(missing_ok=True)
        except Exception:
            pass

    _print_summary(stats, len(urls), elapsed, retried=retried_count)


if __name__ == "__main__":
    main()