"""Command-line interface and top-level flow."""

from __future__ import annotations

import argparse
import sys
import time
from urllib.parse import urlparse

import requests

from . import __version__
from .config import GUEST_VARY_PATH, MAX_WORKERS, SITEMAP_UAS
from .network import make_session, parse_sitemap, pre_run_check
from .runner import run_warming
from .state import AdaptiveDelay, checkpoint_path, load_checkpoint, save_checkpoint
from .ui import RICH_AVAILABLE, console, esc, log, print_banner, print_summary
from .utils import is_valid_http_url, sanitize_url, same_host, short_url
from .warmer import compute_stats, warm_url


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LiteSpeed Cache Warmer | LSCW",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--site",        required=True,                    help="Site URL (e.g., https://mysite.com)")
    parser.add_argument("--sitemap",                                       help="Sitemap URL. If not specified, <site>/sitemap.xml is tried")
    parser.add_argument("--sitemap-ua",  choices=list(SITEMAP_UAS), default="googlebot",
                                                                       help="UA for sitemap fetching. 'googlebot' bypasses some bot protections, 'browser' uses a normal Chrome UA (default: googlebot)")
    parser.add_argument("--urls-file",                                     help="Text file containing URLs line by line")
    parser.add_argument("--delay",       type=float, default=1.0,          help="Delay between URLs in seconds (default: 1.0)")
    parser.add_argument("--phase-delay", type=float, default=0.3,          help="Delay between phases in seconds (default: 0.3)")
    parser.add_argument("--no-mobile",   dest="mobile", action="store_false", default=True,
                                                                       help="Skip mobile cache warming (mobile is on by default)")
    parser.add_argument("--workers",     type=int,   default=1,            help=f"Number of parallel workers, max {MAX_WORKERS} (shared hosting: 1-2)")
    parser.add_argument("--timeout",     type=int,   default=30,           help="HTTP timeout in seconds (default: 30)")
    parser.add_argument("--start-from",  type=int,   default=1,            help="URL index to start from (default: 1)")
    parser.add_argument("--limit",       type=int,                         help="Maximum number of URLs to process")
    parser.add_argument("--resume",      action="store_true",              help="Resume from where it left off using the checkpoint file")
    parser.add_argument("--dry-run",     action="store_true",              help="List URLs and exit without making requests")
    args = parser.parse_args()

    if args.workers < 1 or args.workers > MAX_WORKERS:
        log("WARN", f"--workers clamped to range 1-{MAX_WORKERS}.")
        args.workers = max(1, min(args.workers, MAX_WORKERS))
    return args


def banner_rows(args, site_base: str) -> list[tuple[str, str]]:
    return [
        ("site",        site_base),
        ("sitemap ua",  args.sitemap_ua),
        ("delay",       f"{args.delay}s (adaptive)"),
        ("workers",     str(args.workers)),
        ("phase delay", f"{args.phase_delay}s"),
        ("timeout",     f"{args.timeout}s"),
        ("mobile",      "on" if args.mobile else "off"),
        ("resume",      "on" if args.resume else "off"),
    ]


def collect_urls(args, session: requests.Session, sitemap_url: str, site_host: str) -> list[str]:
    if args.urls_file:
        log("INFO", f"Reading URL file: {esc(args.urls_file)}")
        try:
            with open(args.urls_file, "r", encoding="utf-8") as f:
                raw = [sanitize_url(ln) for ln in f]
        except OSError as e:
            log("ERR", f"Could not read file: {esc(args.urls_file)} → {e}")
            sys.exit(1)
        urls, seen = [], set()
        for u in raw:
            if u and is_valid_http_url(u) and u not in seen:
                seen.add(u)
                urls.append(u)
    else:
        log("INFO", f"Fetching sitemap: {esc(sitemap_url)}")
        urls = parse_sitemap(session, sitemap_url, args.sitemap_ua)
        if not urls:
            log("ERR", "Could not fetch URLs from sitemap! Check --urls-file or --sitemap option.")
            sys.exit(1)

    before = len(urls)
    urls = [u for u in urls if same_host(u, site_host)]
    external = before - len(urls)
    if external:
        log("WARN", f"{external} URLs skipped: they do not belong to {esc(site_host)}.")
    return urls


def retry_failed(args, site_base: str, results_by_url: dict, checkpoint_set: set) -> tuple[int, bool]:
    """Retries failed URLs with a higher timeout; returns (retried_count, interrupted)."""
    failed_urls = [u for u, r in results_by_url.items() if r["has_error"]]
    if not failed_urls:
        return 0, False

    if RICH_AVAILABLE:
        console.print(f"\n  [yellow]![/]  retrying [bold]{len(failed_urls)}[/] failed urls "
                      f"[dim]· timeout ×2 · slower delay[/]\n")
    else:
        print(f"\n! Retrying {len(failed_urls)} failed URLs...")

    retry_session = make_session(retries=2, backoff=1.5)
    retry_delay   = min(args.delay * 2, 5.0)
    retried_count = 0
    interrupted   = False

    try:
        for url in failed_urls:
            time.sleep(retry_delay)
            r = warm_url(
                retry_session, url, site_base,
                warm_mobile=args.mobile,
                timeout=args.timeout * 2,
                delay_between_phases=args.phase_delay,
            )
            retried_count += 1
            results_by_url[url] = r
            if not r["has_error"]:
                checkpoint_set.add(url)

            label = short_url(url, 60)
            p1, p2 = r["phase1_status"], r["phase2_status"]
            if RICH_AVAILABLE:
                icon = "[green]✓[/]" if not r["has_error"] else "[red]✗[/]"
                console.print(f"  {icon}  {esc(label)}  [dim]guest[/] {p1}  [dim]full[/] {p2}")
            else:
                print(f"  {'✓' if not r['has_error'] else '✗'} {label}  guest:{p1}  full:{p2}")
    except KeyboardInterrupt:
        interrupted = True
        log("WARN", "Retry pass interrupted — progress saved to checkpoint.")
    finally:
        retry_session.close()

    return retried_count, interrupted


def main() -> None:
    args = parse_args()

    site_base = args.site.rstrip("/")
    if not is_valid_http_url(site_base):
        log("ERR", f"Invalid site URL: {esc(args.site)}")
        sys.exit(1)
    site_host   = urlparse(site_base).netloc
    sitemap_url = args.sitemap or f"{site_base}/sitemap.xml"
    cp_path     = checkpoint_path(site_base)

    print_banner(__version__, banner_rows(args, site_base))

    session = make_session()

    log("INFO", "Checking connection...")
    status = pre_run_check(session, site_base, timeout=args.timeout)
    if status is None:
        log("ERR", f"Site is unreachable: {esc(site_base)} — Stopping script.")
        sys.exit(1)
    if status in (401, 403):
        log("ERR", f"Site returned HTTP {status} — a WAF/bot protection is likely blocking the script. Stopping.")
        sys.exit(1)
    if status >= 400:
        log("ERR", f"Site returned HTTP {status} — Stopping script.")
        sys.exit(1)
    log("OK", "Connection successful.")

    urls = collect_urls(args, session, sitemap_url, site_host)

    start = max(0, args.start_from - 1)
    urls = urls[start:]
    if args.limit:
        urls = urls[:args.limit]

    checkpoint_set: set = set()
    if args.resume and cp_path.exists():
        checkpoint_set = load_checkpoint(cp_path)
        before = len(urls)
        urls = [u for u in urls if u not in checkpoint_set]
        log("INFO", f"Checkpoint: {before - len(urls)} URLs skipped, {len(urls)} URLs remaining.")
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

    adaptive    = AdaptiveDelay(args.delay)
    start_time  = time.time()
    all_results: list[dict] = []
    interrupted = False

    try:
        run_warming(urls, session, site_base, args, adaptive, checkpoint_set, cp_path, all_results)
    except KeyboardInterrupt:
        interrupted = True
        log("WARN", "Interrupted (Ctrl+C) — progress saved to checkpoint. Continue with --resume.")
    finally:
        save_checkpoint(cp_path, checkpoint_set)
        session.close()

    # Retry pass: each retry result replaces the original so stats stay accurate.
    results_by_url = {r["url"]: r for r in all_results}
    retried_count = 0

    if not interrupted:
        retried_count, interrupted = retry_failed(args, site_base, results_by_url, checkpoint_set)
        if retried_count:
            save_checkpoint(cp_path, checkpoint_set)

    elapsed = time.time() - start_time
    stats = compute_stats(results_by_url.values())

    if stats["errors"] == 0 and not interrupted:
        try:
            cp_path.unlink(missing_ok=True)
        except OSError:
            pass

    print_summary(stats, len(results_by_url), elapsed, retried=retried_count)

    if stats["no_vary"]:
        log("WARN",
            f"{stats['no_vary']} URL could not obtain a live vary key; their vary-keyed bucket was "
            f"left cold on purpose. Guest bucket is still warm. Check that Guest Mode "
            f"and guest.vary.php are reachable ({GUEST_VARY_PATH}).")

    if interrupted:
        sys.exit(130)
    if stats["errors"] == 0:
        log("OK", "Completed — all reachable buckets warmed.")
    else:
        log("WARN", f"Completed with {stats['errors']} failed URLs. Run again with --resume to retry them.")
