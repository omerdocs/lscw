# LSCW — LiteSpeed Cache Warmer

A command-line cache warming tool purpose-built for WordPress sites running the LiteSpeed Cache plugin. Rather than firing dumb HTTP requests and hoping for the best, LSCW replicates the exact warming sequence LiteSpeed itself performs: guest request → vary cookie acquisition → full privileged cache generation, with an optional mobile pass on top.

---

## Why This Exists

LiteSpeed Cache distinguishes between a generic guest cache and a "varied" full cache keyed to a cookie (`_lscache_vary`). A naive warmer that simply GETs your URLs will populate the guest layer but never trigger the full cache — the one that actually serves your logged-out visitors at maximum speed. LSCW handles the full sequence correctly.

---

## Requirements

```
Python 3.10+
pip install requests lxml rich
```

`lxml` and `rich` are optional but strongly recommended. Without `lxml`, sitemap parsing falls back to regex. Without `rich`, the live dashboard is replaced by plain-text output. `requests` is mandatory.

---

## Installation

```bash
git clone https://github.com/yourusername/lscw.git
cd lscw
pip install requests lxml rich
```

No virtual environment required for typical use, but you can use one if you prefer.

---

## Quick Start

```bash
# Minimal — discovers your sitemap automatically
python3 lscw.py --site https://yoursite.com

# With explicit sitemap
python3 lscw.py --site https://yoursite.com --sitemap https://yoursite.com/sitemap_index.xml

# Resume an interrupted run
python3 lscw.py --site https://yoursite.com --resume

# Preview URLs without touching the server
python3 lscw.py --site https://yoursite.com --dry-run

# Parallel workers on a VPS (not shared hosting)
python3 lscw.py --site https://yoursite.com --workers 4 --delay 0.3
```

---

## How the Warming Works

Each URL is processed in three consecutive phases. Understanding this is key to interpreting the output.

**Phase 1 — Guest Request**
A standard GET request using a full desktop browser User-Agent and headers (Chrome 124 on Windows). This hits the page cold, records whether it was a cache HIT or MISS, and extracts any inline `_lscache_vary` value embedded in the page source.

**Phase 2 — Vary Cookie Acquisition**
An AJAX POST is sent to `/wp-content/plugins/litespeed-cache/guest.vary.php` with `LSCWP_CTRL=before_cloud_init` and `action=vary_update`. This is exactly what LiteSpeed's frontend JavaScript does. The response sets the `_lscache_vary` cookie. LSCW checks three places for this cookie value: the response cookies, the `Set-Cookie` header, and the inline value extracted in Phase 1. If all three fail, it falls back to a static hash so the warming can continue.

**Phase 3 — Full Cache Generation**
The same URL is requested again, this time with the `_lscache_vary` cookie attached. This is what instructs LiteSpeed to store the page in the full, varied cache bucket. The response header `X-LiteSpeed-Cache` is read to determine HIT or MISS.

**Phase 3b — Mobile Cache (optional)**
If `--mobile` is active (the default), a fourth request is made using an iPhone User-Agent with the vary cookie modified to `device:mobile`. This populates the separate mobile cache variant that LiteSpeed maintains when mobile detection is enabled.

A configurable pause (`--phase-delay`, default 0.3s) is inserted between each phase to avoid triggering rate limits on the same URL.

---

## All Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `--site` | string | **required** | Base URL of the site, e.g. `https://yoursite.com`. Trailing slash is stripped automatically. |
| `--sitemap` | string | `{site}/sitemap.xml` | Explicit sitemap URL. Supports sitemap index files — LSCW will recursively parse all nested sitemaps. |
| `--urls-file` | string | — | Path to a plain text file containing one URL per line. Lines not starting with `http` are silently skipped. When this is provided, `--sitemap` is ignored. |
| `--delay` | float | `1.0` | Seconds to wait between URLs. This is the *base* value; the adaptive system adjusts it dynamically at runtime. |
| `--phase-delay` | float | `0.3` | Seconds to wait between the phases of a single URL (guest → vary.php → full → mobile). Keeps individual URL bursts from looking like abuse. |
| `--mobile` | flag | `True` | Warm the mobile cache variant in addition to desktop. Pass `--no-mobile` to disable if your site doesn't use mobile-specific caching. |
| `--workers` | int | `1` | Number of parallel threads. On shared hosting, keep this at `1` or `2`. On a VPS, `4`–`8` is reasonable depending on your server. See the [concurrency note](#concurrency) below. |
| `--timeout` | int | `30` | HTTP request timeout in seconds. On slow or resource-constrained servers, increase this to `60` or more to avoid false error counts. |
| `--start-from` | int | `1` | 1-based index to start from in the URL list. Useful for skipping a known-good prefix without a checkpoint file. |
| `--limit` | int | — | Maximum number of URLs to process. Applied after `--start-from`. Handy for testing on a subset before running the full list. |
| `--resume` | flag | — | Load the checkpoint file for the site and skip any URLs already marked complete. See [checkpoint system](#checkpoint--resume) below. |
| `--dry-run` | flag | — | Parse the sitemap (or URL file), print the full numbered URL list, and exit. No HTTP requests are made to the site. |

---

## URL Discovery

**From sitemap (default)**

LSCW fetches the sitemap using a Googlebot User-Agent (servers that block regular crawlers typically allow Googlebot). It detects `<loc>` entries ending in `.xml` as nested sitemaps and follows them recursively, so sitemap index files are handled transparently. Duplicate URLs across multiple sitemaps are deduplicated before warming begins.

If no `--sitemap` is given, LSCW tries `{site}/sitemap.xml`. If that returns nothing or fails, the run is aborted with a clear error — you'll be told to use `--sitemap` or `--urls-file` explicitly.

**From a text file**

```bash
python3 lscw.py --site https://yoursite.com --urls-file urls.txt
```

The file should have one absolute URL per line. Lines that don't start with `http` (blank lines, comments, etc.) are automatically skipped. URLs do not need to belong to the `--site` domain, though the vary cookie acquisition step uses `--site` as its origin.

---

## Adaptive Delay

The delay between URLs isn't fixed — it responds to what the server is telling you.

After every two consecutive errors or 429 responses, the delay doubles (up to `delay × 4`). After ten consecutive clean successes, it pulls back toward the base value. This means LSCW naturally backs off when a server is under load and speeds back up once it recovers, without you needing to intervene.

The current delay value is shown live in the stats panel at the bottom of the terminal.

---

## Checkpoint & Resume

Every 10 URLs (configurable in source via `CHECKPOINT_SAVE_EVERY`), LSCW writes a checkpoint file to disk named `.lscache_{domain}.checkpoint.json`. This file records all successfully processed URLs and a timestamp.

```json
{
  "completed": [
    "https://yoursite.com/page-one/",
    "https://yoursite.com/page-two/"
  ],
  "saved_at": "2025-01-15T14:32:07.123456"
}
```

When you run with `--resume`, LSCW loads this file, filters those URLs out of the current run, and logs how many were skipped. If you run without `--resume`, a new checkpoint is built from scratch.

At the end of a successful run (zero errors), the checkpoint file is deleted automatically. If errors remain, it's kept so you can re-run with `--resume` and attempt those URLs again.

The checkpoint file is placed in whatever directory you run the script from, not in a system location.

---

## Auto-Retry

Any URL that produces an error during the main pass is collected and retried after the main loop finishes. The retry pass uses:

- A fresh `requests.Session` with different retry settings (`backoff_factor=1.5` instead of `0.5`)
- A timeout doubled from the configured value
- A delay capped at `min(delay × 2, 5.0)` seconds between retries

Successful retries are added to the checkpoint and the error count is decremented in the summary. The final summary table shows how many URLs were auto-retried.

---

## Concurrency

With `--workers 1` (the default), LSCW processes URLs sequentially in the main thread. With `--workers N > 1`, it uses a `ThreadPoolExecutor` where each worker gets its own `requests.Session`. The adaptive delay and stats updates are protected by a thread lock.

> **Shared hosting warning:** Most shared hosts throttle or block rapid parallel requests from a single IP. Start with `--workers 1` and only increase if your host can handle it. On shared hosting, a slow sequential run that completes is always better than a fast parallel run that gets your IP blocked mid-way.

The progress bar and live table update correctly in both modes.

---

## Output & Live Dashboard

When `rich` is installed, LSCW renders a live terminal dashboard with three components that refresh four times per second:

- **Progress bar** — shows URL count (M of N), percentage, elapsed time, and ETA
- **Results table** — a rolling window of the last 16 processed URLs, showing the URL (truncated), Guest status, vary.php success, Full cache status, and Mobile status
- **Stats panel** — running totals for HIT/MISS/error counts, current delay, and per-URL timing

Cache status values in the table:

| Symbol | Meaning |
|---|---|
| `🔥 HIT` | The response was served from cache |
| `📝 MISS` | The page was not cached; LiteSpeed will now cache it |
| `🚫 N/C` | LiteSpeed explicitly set no-cache for this URL |
| `─` | Phase was skipped (mobile when `--no-mobile`) |
| `❌ ERR` | Request failed (timeout, connection error, etc.) |

Without `rich`, LSCW falls back to a plain-text table printed line by line with timestamps, which works cleanly in log files and non-interactive environments.

---

## Summary Report

At the end of every run, a summary table is printed:

```
╭─────────────────────────────────────╮
│         📊  Result Summary          │
│  Total URLs         147             │
│  Total time         212.4s (1.4s/url)│
│  vary.php success   147             │
│  Guest cache HIT    12              │
│  Guest cache MISS   135             │
│  Full cache HIT     8               │
│  Full cache MISS    139             │
│  Auto-retried       3               │
│  Errors             0               │
╰─────────────────────────────────────╯
```

High MISS counts on the first run are expected and correct — that's the point. On a second run immediately after, you should see predominantly HITs.

---

## Source-Level Configuration

A few constants near the top of `lscw.py` can be changed to tune behavior without modifying the CLI:

```python
MAX_TABLE_ROWS = 16
```
The number of rows shown in the rolling live table. Increase this on tall terminals, decrease it on small ones.

```python
CHECKPOINT_SAVE_EVERY = 10
```
How frequently (in URLs) the checkpoint is written to disk. Lower values reduce potential data loss on crashes; higher values reduce disk I/O.

```python
GUEST_VARY_PATH = "/wp-content/plugins/litespeed-cache/guest.vary.php"
```
The path to the LiteSpeed vary endpoint. This is standardized across all LiteSpeed Cache installations and should not need changing.

```python
# In warm_url(), Phase 2 fallback:
vary_cookie_value = "78af7c1384f93507c535076013a0b18d"
```
If LSCW cannot obtain a vary cookie from the server through any of the three extraction methods, it falls back to this static hash. You can replace this with the actual `_lscache_vary` value from your site's cookies if you want a more precise fallback.

The `DESKTOP_UA` and `MOBILE_UA` strings at the top of the file can be updated if you need to use different User-Agent values for your environment.

---

## Pre-Flight Check

Before any warming begins, LSCW sends a single GET to `--site` and verifies a sub-500 response. If the site is unreachable, returns a 5xx, or times out, the run is aborted immediately. This prevents starting a long warm job against a server that's already down.

---

## Edge Cases

**Site returns 429 (Too Many Requests)**
LSCW detects 429 responses and treats them the same as errors for the purpose of the adaptive delay, causing it to back off. The URL is flagged and retried in the auto-retry pass.

**Sitemap requires authentication**
Use `--urls-file` instead. Export your URLs from a sitemap plugin, WP All Export, or Screaming Frog, save them to a text file, and pass it with `--urls-file`.

**vary.php is unreachable**
If the AJAX call to `guest.vary.php` fails completely (exception, not just a bad cookie), LSCW falls back to `"device:desktop"` as the vary value and continues. Warming will still occur; it just may not key to the exact vary hash your server expects. The `vary.php` column in the table will show `✗` for those URLs.

**Running on Windows**
Works as-is. Unicode symbols in the plain-text fallback may not render correctly in older Command Prompt windows; use Windows Terminal or run inside WSL for the best experience.

---

## License

GNU GPLv3

---

## Contributing

Issues and pull requests are welcome. If you're adding support for a new cache plugin or URL discovery method, try to keep the phase structure intact so the output columns remain consistent.
