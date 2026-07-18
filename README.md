# LSCW — LiteSpeed Cache Warmer

A command-line cache warming tool purpose-built for WordPress sites running the **LiteSpeed Cache** plugin. It doesn't just fire GET requests at your URLs and hope for the best — it replicates the exact request sequence LiteSpeed Cache's own frontend JavaScript performs to populate every cache bucket a real visitor can land in: the guest bucket, the vary-keyed "full" bucket, and the mobile variant of both.

---

## Table of Contents

- [Why This Exists](#why-this-exists)
- [Architecture: How Warming Actually Works](#architecture-how-warming-actually-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [All Parameters](#all-parameters)
- [URL Discovery](#url-discovery)
- [Adaptive Delay](#adaptive-delay)
- [Checkpoint & Resume](#checkpoint--resume)
- [Auto-Retry](#auto-retry)
- [Concurrency](#concurrency)
- [Output & Live Dashboard](#output--live-dashboard)
- [Reading the Summary Report](#reading-the-summary-report)
- [Scenarios & Troubleshooting](#scenarios--troubleshooting)
- [Source-Level Configuration](#source-level-configuration)
- [License](#license)
- [Contributing](#contributing)

---

## Why This Exists

LiteSpeed Cache doesn't store one copy of a page. It stores at least two, keyed differently:

1. **The guest bucket** — the generic, anonymous version of a page. This is what a naive crawler or a dumb warming script populates just by requesting the URL.
2. **The vary bucket** — a version keyed to a cookie, `_lscache_vary`, whose value LiteSpeed derives server-side from your site's configured "vary" factors (device type, currency, logged-in state, A/B groups, etc.) plus a site-specific salt. This is the bucket that actually serves most real logged-out visitors once the plugin's own JS has run in their browser.

A script that only GETs URLs warms bucket #1 and never touches bucket #2 — which means the first real visitor to hit any given page after a naive "warm-up" still eats a full cold-cache page generation. LSCW exists to warm both buckets correctly, using the same handshake the plugin itself performs, and — just as importantly — to **not** warm a bucket under a guessed or fabricated key that no real visitor will ever present. A vary-keyed cache entry stored under the wrong hash is worse than no entry at all: it silently occupies cache storage without ever being served.

---

## Architecture: How Warming Actually Works

Each URL goes through an independent desktop pass and, optionally, an independent mobile pass. Both passes follow the same two-step shape: **cookie-less guest request → live vary key lookup → conditional full-cache request.**

### Desktop pass

**Step 1 — Cookie-less guest request**

Before anything else, LSCW explicitly strips any `_lscache_vary` cookie from the session's cookie jar (`clear_vary_cookie`). This matters because `requests.Session` keeps a jar for the whole run — without this step, a vary cookie picked up while warming URL N would silently leak into the "guest" request for URL N+1, and that request would no longer be a genuine cookie-less guest hit. Every guest request in every phase starts from a verified-clean jar.

With a clean jar, LSCW sends a standard `GET` using a desktop Chrome 124 / Windows User-Agent and a realistic header set (`Sec-Fetch-*`, `Accept`, `Accept-Encoding`, etc. — the same shape a real browser's navigation request has). The response's `X-LiteSpeed-Cache` header is read to classify the result as `HIT`, `MISS`, or `NO-CACHE`. The HTML body is also scanned for an inline vary value the theme or plugin may have embedded directly in the page (`_lscache_vary = '...'`, `lscacheVary = '...'`, or a `"vary":"..."` JSON fragment), which is kept as a fallback signal for the next step.

**Step 2 — Live vary key lookup**

LSCW sends an AJAX `POST` to `/wp-content/plugins/litespeed-cache/guest.vary.php` with `LSCWP_CTRL=before_cloud_init` and `action=vary_update`, plus the correct `Referer`/`Origin`/`Sec-Fetch-*` headers for a same-origin XHR. This is exactly the call LiteSpeed's own frontend script fires after page load to determine which vary bucket the visitor belongs to. The resulting `_lscache_vary` value is read from, in order of preference: the response's cookie jar, the raw `Set-Cookie` header (regex-extracted, for cases where `requests` doesn't parse it into the jar), and finally the inline value found in Step 1.

Whatever value is found is passed through `looks_like_vary_hash()` before being trusted. LiteSpeed's real vary values are opaque hashes — no colons, no spaces, no commas, generally 8+ alphanumeric/`-`/`_` characters. Anything that doesn't match this shape (empty responses, literal placeholder strings, malformed fragments) is treated as **no key obtained**, full stop. There is no synthetic fallback value anywhere in this path — if a real key can't be confirmed, the function returns `None` and says so.

**Step 3 — Conditional full-cache generation**

If, and only if, Step 2 produced a validated key, LSCW clears the cookie jar again, attaches `_lscache_vary=<key>` to a fresh `GET`, and requests the same URL. This second hit is what actually causes LiteSpeed to generate and store the page in the vary-keyed bucket. The response's cache header is again read for `HIT`/`MISS`.

If Step 2 came back empty, this request is **skipped entirely** and the phase is reported as `NO-VARY` rather than being silently sent under a fabricated cookie. The guest bucket for that URL is still warm from Step 1 — only the vary-keyed bucket is left untouched, and it's left that way on purpose.

### Mobile pass (optional, on by default)

Mobile isn't handled by re-using the desktop vary key with a string substitution — LiteSpeed's mobile vary hash is a genuinely different value derived server-side from a mobile User-Agent, not a predictable transform of the desktop one. So the mobile pass repeats the entire two-step shape independently, end-to-end, with an iPhone Safari User-Agent on every request: its own cookie-less guest `GET`, its own inline-vary scan, its own `guest.vary.php` lookup (with mobile headers, so the server actually returns the mobile-specific hash), and — again, only if a real key comes back — its own full-cache `GET`. If no mobile vary key is obtained, the mobile guest status is reported and the vary-keyed mobile request is skipped, exactly as on desktop.

A configurable pause (`--phase-delay`, default `0.3s`) is inserted between every individual request within a URL's processing — guest → vary lookup → full → mobile guest → mobile vary lookup → mobile full — so that a single URL's traffic doesn't look like a burst to the server or trip rate-limiting.

### Result classification, per URL

| Field | Meaning |
|---|---|
| `phase1_status` | Desktop guest request result: `HIT`, `MISS`, `NO-CACHE`, or `ERROR` |
| `guest_vary_ok` | Whether a validated desktop vary key was obtained |
| `vary_source` | Where the key came from: `vary.php`, `set-cookie`, or `inline` |
| `phase2_status` | Desktop full-cache request result, or `NO-VARY` if Step 2 failed, or `SKIP` |
| `mobile_guest_status` | Mobile guest request result (only if `--mobile` mode active) |
| `mobile_vary_ok` | Whether a validated mobile vary key was obtained |
| `phase3_status` | Mobile full-cache result, `NO-VARY`-equivalent (falls back to the mobile guest status), or `SKIP` |
| `has_error` | Any hard failure (timeout, connection error) in any phase |
| `got_429` | Whether any request in the sequence was rate-limited |

---

## Requirements

```
Python 3.10+
pip install requests lxml rich
```

`requests` is mandatory — the script exits immediately with a clear message if it's missing. `lxml` and `rich` are optional but strongly recommended:

- Without `lxml`, sitemap parsing still works (it uses a regex-based `<loc>` extractor regardless), but `lxml` presence is checked and can be relied on by future stricter XML parsing.
- Without `rich`, the live dashboard, colored tables, and panels are replaced by a plain-text, line-by-line output with timestamps — fully functional in log files, cron jobs, and non-interactive shells, just less pretty.

---

## Installation

```bash
git clone https://github.com/omerdocs/lscw.git
cd lscw
pip install requests lxml rich
```

No virtual environment is required for typical use, but nothing stops you from using one.

---

## Quick Start

```bash
# Minimal — discovers your sitemap automatically at {site}/sitemap.xml
python3 lscw.py --site https://yoursite.com

# With an explicit sitemap (handles sitemap index files too)
python3 lscw.py --site https://yoursite.com --sitemap https://yoursite.com/sitemap_index.xml

# Resume an interrupted run
python3 lscw.py --site https://yoursite.com --resume

# Preview the URL list without sending a single request
python3 lscw.py --site https://yoursite.com --dry-run

# Skip mobile warming entirely
python3 lscw.py --site https://yoursite.com --no-mobile

# Parallel workers — VPS/dedicated only, see the Concurrency section
python3 lscw.py --site https://yoursite.com --workers 4 --delay 0.3
```

---

## All Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `--site` | string | **required** | Base URL of the site, e.g. `https://yoursite.com`. A trailing slash is stripped automatically. |
| `--sitemap` | string | `{site}/sitemap.xml` | Explicit sitemap URL. Sitemap index files are supported — nested `.xml` entries are followed recursively. |
| `--urls-file` | string | — | Path to a plain text file with one URL per line. When provided, `--sitemap` discovery is skipped entirely. |
| `--delay` | float | `1.0` | Base seconds to wait between URLs. This is a starting point — the adaptive system moves it up or down at runtime based on server behavior. |
| `--phase-delay` | float | `0.3` | Seconds between each individual request within a single URL's phase sequence (guest → vary lookup → full → mobile guest → mobile vary → mobile full). |
| `--no-mobile` | flag | mobile is **on** by default | Disables the mobile pass. Without this flag, every URL gets both a desktop and a mobile pass. |
| `--workers` | int | `1` | Number of parallel threads. See [Concurrency](#concurrency) — keep this at `1`–`2` on shared hosting. |
| `--timeout` | int | `30` | HTTP timeout, in seconds, for every request in the sequence. Raise this on slow or resource-constrained servers to avoid false error counts. |
| `--start-from` | int | `1` | 1-based index into the discovered URL list to start from. Useful for skipping a known-good prefix without touching the checkpoint file. |
| `--limit` | int | — | Maximum number of URLs to process, applied after `--start-from`. Handy for a quick test run before committing to the full site. |
| `--resume` | flag | — | Loads the checkpoint file for this site and filters out any URL already marked complete. See [Checkpoint & Resume](#checkpoint--resume). |
| `--dry-run` | flag | — | Discovers and prints the full numbered URL list, then exits. Zero requests are sent to the target site. |

---

## URL Discovery

**From sitemap (default)**

LSCW fetches the sitemap using a Googlebot User-Agent, since servers or security plugins that block generic scripted crawlers typically still allow Googlebot through. Any `<loc>` entry ending in `.xml` is treated as a nested sitemap and queued for recursive parsing — so pointing `--sitemap` at a sitemap *index* file works transparently, no special flag needed. URLs are deduplicated across all nested sitemaps before warming begins.

If `--sitemap` isn't given, LSCW tries `{site}/sitemap.xml`. If that request fails or returns no usable URLs, the run aborts immediately with a message pointing you to `--sitemap` or `--urls-file`.

**From a text file**

```bash
python3 lscw.py --site https://yoursite.com --urls-file urls.txt
```

One absolute URL per line. Any line that doesn't start with `http` — blank lines, comments, stray whitespace — is silently skipped. The URLs in the file don't need to belong to the `--site` domain, but `--site` is still used as the origin for the `guest.vary.php` vary-key lookup, so cross-domain warming against a *different* site's LiteSpeed install won't produce valid vary keys (you'll just see `NO-VARY` for every URL — the guest bucket still warms fine).

---

## Adaptive Delay

The pause between URLs isn't a fixed number — it's a small feedback loop (`AdaptiveDelay`) that reacts to what the server is actually telling you:

- **On problems** (a hard error, or any `429` seen anywhere in a URL's phase sequence): after **two consecutive** problem URLs, the delay doubles — capped at `4×` the base `--delay` value.
- **On success**: after **ten consecutive** clean URLs, the delay eases back toward the base value by 25%.

This means a run naturally slows down when a server shows signs of load or rate-limiting, and speeds back up on its own once things settle — no manual intervention needed mid-run. The current live delay value is always visible in the stats panel.

---

## Checkpoint & Resume

Every `CHECKPOINT_SAVE_EVERY` URLs (10 by default), LSCW writes progress to a file named `.lscache_{domain}.checkpoint.json` in the current working directory:

```json
{
  "completed": [
    "https://yoursite.com/page-one/",
    "https://yoursite.com/page-two/"
  ],
  "saved_at": "2026-07-18T14:32:07.123456"
}
```

Running with `--resume` loads this file, filters already-completed URLs out of the current URL list, and logs how many were skipped. Running without `--resume` always starts a fresh list, regardless of any existing checkpoint file on disk.

At the end of a run with **zero remaining errors**, the checkpoint file is deleted automatically — there's nothing left to resume. If errors remain, the file is kept so a subsequent `--resume` run only targets the URLs that actually failed.

---

## Auto-Retry

Any URL that ends the main pass with `has_error = True` is collected and retried once, automatically, after the main loop finishes — no separate command needed. The retry pass uses deliberately more forgiving settings than the main run:

- A fresh `requests.Session` with a higher retry `backoff_factor` (`1.5` instead of `0.5`)
- Request timeout doubled from the configured `--timeout`
- A flat delay between retries of `min(--delay × 2, 5.0)` seconds

Every successful retry is added to the checkpoint and decrements the run's error count in the final stats. The summary table reports how many URLs went through this retry pass.

---

## Concurrency

With the default `--workers 1`, LSCW processes URLs strictly sequentially in the main thread, honoring `--delay` (as adjusted by the adaptive system) between each one. With `--workers N > 1`, it switches to a `ThreadPoolExecutor` where every worker thread gets its own independent `requests.Session` — sessions are never shared across threads, which is what makes the per-URL cookie-jar isolation described in the [Architecture](#architecture-how-warming-actually-works) section safe under concurrency. Stats updates and the adaptive delay state are protected by a `threading.Lock`.

> **Shared hosting warning:** most shared hosts throttle or hard-block rapid parallel connections from a single IP. Start at `--workers 1`. A slow sequential run that finishes cleanly beats a fast parallel run that gets your IP throttled or blocked halfway through. Reserve `--workers 4`–`8` for a VPS or dedicated box you control.

The live dashboard and plain-text fallback both update correctly and thread-safely in either mode.

---

## Output & Live Dashboard

With `rich` installed, LSCW renders a live terminal UI refreshing 4 times per second, made of three stacked components:

- **Progress bar** — current/total URL count, percentage, elapsed time, and ETA
- **Results table** — a rolling window of the last `MAX_TABLE_ROWS` (16 by default) processed URLs, showing per-URL: Guest status, whether a vary key was obtained, Full-cache status, and Mobile status
- **Stats panel** — running totals for HIT/MISS on both buckets, vary-success count, no-vary count, error count, and the current live adaptive delay value

Status symbols used throughout the table and logs:

| Symbol | Meaning |
|---|---|
| `🔥 HIT` | Response was served directly from cache |
| `📝 MISS` | Page wasn't cached yet; this request causes LiteSpeed to cache it now |
| `🚫 N/C` | LiteSpeed explicitly marked this URL no-cache (e.g. excluded page, logged-in-only content) |
| `⚠ NOVARY` | No validated vary key could be obtained — the vary-keyed bucket was deliberately left untouched; the guest bucket is still warm |
| `─` | Phase not applicable/skipped (e.g. mobile phases when `--no-mobile` is set) |
| `❌ ERR` | Request failed outright — timeout, connection error, or similar |

Without `rich`, the same information prints as a plain-text table with `[HH:MM:SS]` timestamps, one line per URL — this is the mode you want for cron jobs, CI logs, or anywhere a live-redrawing terminal UI would just produce garbage output.

---

## Reading the Summary Report

At the end of every run — main pass plus any retries — a summary is printed:

```
╭──────────────────────────────────────╮
│         📊  Result Summary            │
│  Total URLs               147         │
│  Total time                212.4s (1.4s/url) │
│  vary.php success          139         │
│  No vary key (skipped)     8           │
│  Guest cache HIT            12         │
│  Guest cache MISS          135         │
│  Full cache HIT              8         │
│  Full cache MISS           131         │
│  Auto-retried                 3        │
│  Errors                       0        │
╰──────────────────────────────────────╯
```

A few things worth knowing when reading this:

- **High MISS counts on a first run are expected and correct.** That's the entire point of warming — you're populating a cold cache. Run LSCW a second time immediately after and you should see mostly HITs on both buckets.
- **`No vary key (skipped)` isn't a failure mode.** It means those URLs' guest bucket is warm, but LiteSpeed either didn't return a vary key for them (common for URLs excluded from vary-based caching, or where `guest.vary.php` itself is unreachable) or returned something that didn't validate as a real hash. If this number is a meaningful fraction of your total URL count, see [Scenarios & Troubleshooting](#scenarios--troubleshooting).
- If any URLs ended with a non-zero `no_vary` count, a final `WARN` log line explains it explicitly and points at the `guest.vary.php` path so you can verify it's reachable by hand.

---

## Scenarios & Troubleshooting

**Site returns 429 (Too Many Requests)**
Detected on any request in the phase sequence, not just the main GET. It counts toward the adaptive delay's problem streak (causing the run to slow itself down) and the affected URL is queued for the auto-retry pass with a longer timeout and slower pacing.

**A large fraction of URLs come back `NO-VARY`**
This means Step 2 (the `guest.vary.php` lookup) isn't returning a value that passes `looks_like_vary_hash()` validation. Common causes, roughly in order of likelihood:
- The LiteSpeed Cache plugin's guest mode isn't enabled on the site, or vary-based caching is off entirely — in which case `NO-VARY` is the technically correct outcome, not a bug in LSCW.
- `guest.vary.php` is blocked by a firewall/WAF rule, a security plugin, or server-level rules that reject requests without a real browser session.
- The site uses a modified or non-standard `GUEST_VARY_PATH` (rare, but some managed hosts relocate plugin asset paths).
Manually curling the endpoint with a browser's dev tools open (watch the Network tab on a real page load) is the fastest way to confirm whether the plugin is issuing a vary cookie at all before assuming LSCW is at fault.

**Sitemap requires authentication or is blocked**
Use `--urls-file` instead. Export your URL list from an SEO plugin's sitemap, WP All Export, or a crawler like Screaming Frog, save it as one URL per line, and pass it with `--urls-file`.

**Running on Windows**
Works as-is under Python 3.10+. The plain-text fallback's Unicode symbols may render oddly in older Command Prompt windows — use Windows Terminal or run inside WSL for a clean experience.

**Warming a staging or password-protected environment**
The guest bucket still warms fine as long as the base page loads without auth. The vary-key lookup will only succeed if `guest.vary.php` itself is reachable without authentication, which it usually is even behind HTTP auth on the rest of the site (it's typically excluded by the auth rule as a plugin asset) — but this varies by hosting setup.

---

## Source-Level Configuration

A handful of constants near the top of `lscw.py` tune behavior without touching the CLI:

```python
MAX_TABLE_ROWS = 16
```
Rows shown in the rolling live results table. Raise it on a tall terminal, lower it on a small one.

```python
CHECKPOINT_SAVE_EVERY = 10
```
How often, in URLs processed, the checkpoint file is written to disk. Lower values reduce potential progress loss on a crash; higher values reduce disk I/O on very large runs.

```python
GUEST_VARY_PATH = "/wp-content/plugins/litespeed-cache/guest.vary.php"
```
The LiteSpeed vary endpoint path. Standardized across all LiteSpeed Cache installations — shouldn't need changing unless a host has done something unusual with plugin asset paths.

`DESKTOP_UA` and `MOBILE_UA` at the top of the file can be updated if you need different User-Agent strings for your environment (e.g. testing how a specific bot or browser version is treated by server-side rules).

---

## Pre-Flight Check

Before any warming begins, LSCW sends a single `GET` to `--site` and verifies a sub-500 response. If the site is unreachable, times out, or returns a 5xx, the run aborts immediately — this avoids kicking off a long warming job against a server that's already down.

---

## License

GNU GPLv3

---

## Contributing

Issues and pull requests are welcome. If you're adding support for another cache plugin or a new URL-discovery method, try to keep the guest → vary-lookup → conditional-full-cache shape intact — it's what makes the output columns and the `NO-VARY` reporting meaningful across different sources of URLs.
