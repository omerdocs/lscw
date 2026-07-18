# LSCW — LiteSpeed Cache Warmer

A command-line cache warming tool purpose-built for WordPress sites running the **LiteSpeed Cache** plugin. It doesn't just fire GET requests at your URLs and hope for the best — it replicates the exact request sequence LiteSpeed Cache's own frontend JavaScript performs to populate every cache bucket a real visitor can land in: the guest bucket, the vary-keyed "full" bucket, and the mobile variant of both.

---

## Table of Contents

- [Why This Exists](#why-this-exists)
- [How Warming Actually Works](#how-warming-actually-works)
- [Project Structure](#project-structure)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [All Parameters](#all-parameters)
- [URL Discovery](#url-discovery)
- [Being a Good Citizen: Delays, 429s, and the Pre-Flight Check](#being-a-good-citizen-delays-429s-and-the-pre-flight-check)
- [Checkpoint, Resume & Interrupting Safely](#checkpoint-resume--interrupting-safely)
- [Auto-Retry](#auto-retry)
- [Concurrency](#concurrency)
- [Output & Live Dashboard](#output--live-dashboard)
- [Reading the Summary](#reading-the-summary)
- [Exit Codes](#exit-codes)
- [Troubleshooting](#troubleshooting)
- [Configuration Constants](#configuration-constants)
- [License](#license)
- [Contributing](#contributing)

---

## Why This Exists

LiteSpeed Cache doesn't store one copy of a page. It stores at least two, keyed differently:

1. **The guest bucket** — the generic, anonymous version of a page. This is what a naive crawler or a dumb warming script populates just by requesting the URL.
2. **The vary bucket** — a version keyed to a cookie, `_lscache_vary`, whose value LiteSpeed derives server-side from your site's configured "vary" factors (device type, currency, logged-in state, A/B groups, etc.) plus a site-specific salt. This is the bucket that actually serves most real logged-out visitors once the plugin's own JS has run in their browser.

A script that only GETs URLs warms bucket #1 and never touches bucket #2 — which means the first real visitor to hit any given page after a naive "warm-up" still eats a full cold-cache page generation. LSCW warms both buckets correctly, using the same handshake the plugin itself performs, and — just as importantly — **never** warms a bucket under a guessed or fabricated key that no real visitor will ever present. A vary-keyed cache entry stored under the wrong hash is worse than no entry at all: it silently occupies cache storage without ever being served.

---

## How Warming Actually Works

Each URL goes through an independent desktop pass and, optionally, an independent mobile pass. Both passes follow the same shape: **cookie-less guest request → live vary key lookup → conditional full-cache request.**

### Desktop pass

**Step 1 — Cookie-less guest request**

Before anything else, LSCW strips any `_lscache_vary` cookie from the session's cookie jar. This matters because `requests.Session` keeps a jar for the whole run — without this step, a vary cookie picked up while warming URL N would silently leak into the "guest" request for URL N+1, and that request would no longer be a genuine cookie-less guest hit.

With a clean jar, LSCW sends a standard `GET` using a desktop Chrome / Windows User-Agent and a realistic browser header set (`Sec-Fetch-*`, `Accept`, `Accept-Encoding`, etc.). Three checks happen on the response:

- **HTTP status**: any `4xx`/`5xx` (including `429`) marks the URL as failed — it is *not* recorded as completed, and it will be picked up by the auto-retry pass.
- **Off-site redirects**: if the final URL after redirects lands on a different host, the URL is reported as `ext →` and the remaining phases are skipped — LSCW never sends vary lookups or cookies to a third-party domain.
- **Cache status**: the `X-LiteSpeed-Cache` header classifies the result as `HIT`, `MISS`, or `no-cache`.

The HTML body is also scanned for an inline vary value the theme or plugin may have embedded in the page, kept as a fallback signal for the next step.

**Step 2 — Live vary key lookup**

LSCW sends an AJAX `POST` to `/wp-content/plugins/litespeed-cache/guest.vary.php` with `LSCWP_CTRL=before_cloud_init` and `action=vary_update`, plus the correct `Referer`/`Origin`/`Sec-Fetch-*` headers for a same-origin XHR. This is exactly the call LiteSpeed's own frontend script fires after page load. The resulting `_lscache_vary` value is read from, in order of preference: the response's cookie jar, the raw `Set-Cookie` header, and finally the inline value found in Step 1.

Whatever value is found is validated before being trusted. LiteSpeed's real vary values are opaque hashes — no colons, no spaces, generally 8+ alphanumeric characters. Anything that doesn't match this shape is treated as **no key obtained**, full stop. There is no synthetic fallback value anywhere in this path.

**Step 3 — Conditional full-cache generation**

If, and only if, Step 2 produced a validated key, LSCW attaches `_lscache_vary=<key>` to a fresh `GET` and requests the same URL — this is what causes LiteSpeed to generate and store the vary-keyed copy. If Step 2 came back empty, this request is **skipped entirely** and reported as `no-vary` rather than being sent under a fabricated cookie. The guest bucket is still warm from Step 1; only the vary-keyed bucket is left untouched, on purpose.

### Mobile pass (on by default)

LiteSpeed's mobile vary hash is a genuinely different value derived server-side from a mobile User-Agent — not a predictable transform of the desktop one. So the mobile pass repeats the entire flow independently with an iPhone Safari User-Agent on every request: its own cookie-less guest `GET`, its own `guest.vary.php` lookup (with mobile headers, so the server returns the mobile-specific hash), and — again, only with a real key — its own full-cache `GET`. If no mobile key is obtained, the mobile full phase is reported as `no-vary`, exactly as on desktop.

A configurable pause (`--phase-delay`, default `0.3s`) is inserted between every individual request within a URL's sequence, so a single URL's traffic doesn't look like a burst to the server.

### Result classification, per URL

| Field | Meaning |
|---|---|
| `phase1_status` | Desktop guest result: `HIT`, `MISS`, `NO-CACHE`, `EXT-REDIR`, `E<code>` (HTTP error), or `ERROR` |
| `guest_vary_ok` | Whether a validated desktop vary key was obtained |
| `vary_source` | Where the key came from: `vary.php`, `set-cookie`, or `inline` |
| `phase2_status` | Desktop full-cache result, or `NO-VARY` if no key, or `SKIP` |
| `mobile_guest_status` | Mobile guest result (when the mobile pass is active) |
| `mobile_vary_ok` | Whether a validated mobile vary key was obtained |
| `phase3_status` | Mobile full-cache result, `NO-VARY` if no mobile key, or `SKIP` |
| `has_error` | Any hard failure (timeout, connection error, HTTP 4xx/5xx) in any phase |
| `got_429` | Whether any request in the sequence was rate-limited |

---

## Project Structure

LSCW is organized as a small Python package with a thin entry point:

```
LSCW Modüler/
├── lscw.py              # entry point: python3 lscw.py --site ...
└── lscw/
    ├── __main__.py      # alternative entry: python3 -m lscw
    ├── config.py        # user agents, headers, tunable limits
    ├── utils.py         # URL validation / sanitization helpers
    ├── ui.py            # all terminal output: Rich panels + plain fallback
    ├── state.py         # checkpoint file + adaptive delay
    ├── network.py       # HTTP sessions, pre-flight check, sitemap crawling
    ├── warmer.py        # the core warming logic described above
    ├── runner.py        # sequential / threaded execution engine
    └── cli.py           # argument parsing and top-level flow
```

Dependencies flow one way (`cli` → `runner` → `warmer` → `config`); there are no circular imports, and all presentation lives in `ui.py` — the warming logic never prints anything itself.

---

## Requirements

```
Python 3.9+
pip install requests rich
```

- `requests` is **mandatory** — the tool exits immediately with a clear message if it's missing.
- `rich` is optional. Without it, the live dashboard and panels are replaced by a plain-text, line-by-line output with timestamps — fully functional in log files, cron jobs, and non-interactive shells.

---

## Installation

```bash
git clone https://github.com/omerdocs/lscw.git
cd lscw
pip install requests rich
```

No virtual environment is required for typical use, but nothing stops you from using one.

---

## Quick Start

Run from inside the project folder (the entry script imports the `lscw/` package next to it):

```bash
# Minimal — discovers your sitemap automatically at {site}/sitemap.xml
python3 lscw.py --site https://yoursite.com

# Equivalent module form
python3 -m lscw --site https://yoursite.com

# With an explicit sitemap (index files and .xml.gz are handled too)
python3 lscw.py --site https://yoursite.com --sitemap https://yoursite.com/sitemap_index.xml

# Resume an interrupted run
python3 lscw.py --site https://yoursite.com --resume

# Preview the URL list without sending a single warming request
python3 lscw.py --site https://yoursite.com --dry-run

# Fetch the sitemap with a normal browser UA instead of Googlebot
python3 lscw.py --site https://yoursite.com --sitemap-ua browser

# Skip mobile warming entirely
python3 lscw.py --site https://yoursite.com --no-mobile

# Parallel workers — VPS/dedicated only, see the Concurrency section
python3 lscw.py --site https://yoursite.com --workers 4 --delay 0.3
```

---

## All Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `--site` | string | **required** | Base URL of the site, e.g. `https://yoursite.com`. Validated; a trailing slash is stripped. |
| `--sitemap` | string | `{site}/sitemap.xml` | Explicit sitemap URL. Sitemap index files are followed recursively; gzip-compressed sitemaps (`.xml.gz`) are decompressed automatically. |
| `--sitemap-ua` | `googlebot` \| `browser` | `googlebot` | User-Agent used **only** for sitemap fetching. `googlebot` gets past bot-protection rules that block generic scripts; switch to `browser` if your server verifies Googlebot by reverse DNS. |
| `--urls-file` | string | — | Plain text file with one URL per line. Skips sitemap discovery entirely. Lines are validated as real `http(s)` URLs and deduplicated. |
| `--delay` | float | `1.0` | Base seconds between URLs. A starting point — the adaptive system moves it up and down at runtime based on server behavior. |
| `--phase-delay` | float | `0.3` | Seconds between each individual request within a single URL's sequence. |
| `--no-mobile` | flag | mobile is **on** | Disables the mobile pass. |
| `--workers` | int | `1` | Parallel threads, clamped to `1–16`. Keep at `1`–`2` on shared hosting — see [Concurrency](#concurrency). |
| `--timeout` | int | `30` | HTTP timeout in seconds for every request. Raise on slow servers to avoid false error counts. |
| `--start-from` | int | `1` | 1-based index into the discovered URL list to start from. |
| `--limit` | int | — | Maximum number of URLs to process, applied after `--start-from`. Handy for a test run. |
| `--resume` | flag | — | Loads the checkpoint file for this site and skips URLs already completed. |
| `--dry-run` | flag | — | Prints the final numbered URL list and exits. Zero warming requests are sent. |

---

## URL Discovery

**From sitemap (default)**

The sitemap is fetched with the UA selected by `--sitemap-ua`. Nested sitemaps (`.xml` and `.xml.gz` entries) are followed recursively up to a safety limit of 200 sitemap documents. `<loc>` values wrapped in CDATA and XML-escaped URLs (`&amp;` → `&`) are handled correctly, and the resulting list is deduplicated.

If `--sitemap` isn't given, LSCW tries `{site}/sitemap.xml`. If that fails or yields no usable URLs, the run aborts with a message pointing you to `--sitemap` or `--urls-file`.

**From a text file**

```bash
python3 lscw.py --site https://yoursite.com --urls-file urls.txt
```

One absolute URL per line. Lines that aren't valid `http(s)` URLs — blank lines, comments, malformed entries — are skipped, and duplicates are removed while preserving order.

**Domain filtering — applies to both sources**

Every discovered URL must belong to the `--site` host (a `www.` prefix difference is tolerated). URLs pointing anywhere else are dropped before warming begins, with a log line telling you how many were skipped. This guarantees a compromised or misconfigured sitemap can never make LSCW hammer a third-party site.

---

## Being a Good Citizen: Delays, 429s, and the Pre-Flight Check

**Pre-flight check.** Before any warming begins, LSCW sends a single `GET` to `--site`. The run aborts immediately if the site is unreachable or returns any `4xx`/`5xx` — with a specific hint when the status is `401`/`403`, which almost always means a WAF or bot-protection layer is blocking the script.

**Adaptive delay.** The pause between URLs is a feedback loop, not a fixed number:

- **On problems** (a hard failure or any `429` in a URL's sequence): after two consecutive problem URLs, the delay doubles — capped at 4× the base `--delay`.
- **On success**: after ten consecutive clean URLs, the delay eases back toward the base by 25%.

The current live delay is always visible in the Progress panel. The adaptive delay is honored in **both** sequential and parallel modes — with workers, task submission is throttled so the aggregate request rate still respects it.

**429 handling.** A rate-limited response is treated as a real failure: the URL is not marked complete, it feeds the adaptive slow-down, it goes through the auto-retry pass, and the total count of 429 responses is reported in the summary.

---

## Checkpoint, Resume & Interrupting Safely

Progress is tracked in `.lscache_{domain}.checkpoint.json`, written to the current working directory:

```json
{
  "completed": [
    "https://yoursite.com/page-one/",
    "https://yoursite.com/page-two/"
  ],
  "saved_at": "2026-07-18T14:32:07.123456"
}
```

- **Every successfully completed URL** is recorded in memory immediately; the file is flushed to disk every 10 URLs and at every exit path. Writes are atomic (temp file + rename), so a crash mid-write can never corrupt an existing checkpoint.
- **`Ctrl+C` is safe.** An interrupt at any point — including during the retry pass — saves the checkpoint, prints what was accomplished, and exits with code `130`. Continue later with `--resume`.
- `--resume` loads the file and filters out completed URLs. Running without `--resume` always starts fresh (the file on disk is ignored, then overwritten).
- After a run that ends with **zero errors**, the checkpoint file is deleted automatically — there's nothing left to resume. If errors remain, it's kept so the next `--resume` targets only what actually failed.

---

## Auto-Retry

Any URL that ends the main pass with an error — timeouts, connection failures, HTTP `4xx`/`5xx`, `429` — is retried once, automatically, after the main loop finishes. The retry pass uses deliberately more forgiving settings:

- A fresh session with a higher retry backoff (`1.5` vs `0.5`)
- Request timeout doubled from `--timeout`
- A flat delay of `min(--delay × 2, 5.0)` seconds between retries

Each retry **replaces** the original result, so the final summary reflects the true end state — nothing is double-counted. Successful retries are added to the checkpoint.

---

## Concurrency

With the default `--workers 1`, URLs are processed strictly sequentially, honoring the adaptive delay between each one.

With `--workers N` (clamped to 16), a thread pool takes over:

- **One `requests.Session` per worker thread** — sessions are never shared across threads, which keeps the per-URL cookie-jar isolation safe under concurrency. All sessions are closed on exit.
- **Throttled submission** — URLs are handed to the pool at a rate of one per `delay / N` seconds, so N workers collectively still respect the adaptive delay instead of stampeding the server.
- A crashed worker thread is recorded as an error for that URL (and retried later); it never silently disappears or stalls the progress bar.

> **Shared hosting warning:** most shared hosts throttle or block rapid parallel connections from a single IP. Start at `--workers 1`. A slow sequential run that finishes cleanly beats a fast parallel run that gets your IP blocked halfway through. Reserve `--workers 4`–`8` for a VPS or dedicated box you control.

---

## Output & Live Dashboard

With `rich` installed, LSCW renders a structured live view built from titled panels:

```
╭─ LSCW · LiteSpeed Cache Warmer v1.1.0 ─────────────────────────────╮
│  site         https://yoursite.com     sitemap ua   googlebot      │
│  delay        1.0s (adaptive)          workers      1              │
│  phase delay  0.3s                     timeout      30s            │
│  mobile       on                       resume       off            │
╰────────────────────────────────────────────────────────────────────╯

╭─ Progress ─────────────────────────────────────────────────────────╮
│  ━━━━━━━━━╸━━━━━━━━━━━  128/500  26%  0:00:42  eta 0:02:10         │
│                                                                    │
│  desktop   guest 84 hit · 44 miss   full 82 hit · 46 miss          │
│  mobile    guest 80 hit · 48 miss   full 79 hit · 49 miss          │
╰────────────────────────────────────────────────────────────────────╯
╭─ Recent results ───────────────────────────────────────────────────╮
│     #  url                     guest   full   m·guest  m·full  vary│
│  ──────────────────────────────────────────────────────────────────│
│     1  yoursite.com/page1       HIT     HIT     HIT     HIT     ✓  │
│     4  yoursite.com/missing     404      –       –       –      ✗  │
╰────────────────────────────────────────────────────────────────────╯
```

- **Progress** — bar, counts, elapsed/ETA, live per-bucket hit/miss totals for desktop *and* mobile, vary key counts, error count, average time per URL, and the current adaptive delay.
- **Recent results** — a rolling window of the last 16 URLs with all five per-URL outcomes.

Status labels used in the table:

| Label | Meaning |
|---|---|
| `HIT` (green) | Served directly from cache |
| `MISS` (yellow) | Wasn't cached; this request caused LiteSpeed to cache it now |
| `no-cache` | LiteSpeed explicitly excludes this URL from caching |
| `no-vary` | No validated vary key — the vary bucket was deliberately left untouched |
| `404`, `500`, `429`… (red) | HTTP error status returned by the server |
| `error` | Network-level failure: timeout, connection error |
| `ext →` | Redirected to another domain; skipped |
| `–` | Phase not applicable / skipped |
| `✓` / `✗` (vary column) | Whether a validated vary key was obtained |

Without `rich`, the same information prints as plain text, one line per URL, with `[HH:MM:SS]` timestamps — the mode you want for cron jobs and CI logs.

---

## Reading the Summary

Every run ends with a summary panel:

```
╭─ Summary ──────────────────────────────────────────────────────────╮
│  total urls      500                                               │
│  duration        8m 12s · 1.0s/url                                 │
│                                                                    │
│  desktop cache   guest 480 hit · 18 miss    full 492 hit · 6 miss  │
│  mobile cache    guest 479 hit · 19 miss    full 490 hit · 8 miss  │
│                                                                    │
│  vary keys       498 obtained · 2 missing                          │
│  rate limited    3 responses (HTTP 429)                            │
│  auto-retried    3                                                 │
│  errors          0                                                 │
╰────────────────────────────────────────────────────────────────────╯
```

The `rate limited` and `auto-retried` rows only appear when they're non-zero. When reading the numbers:

- **High MISS counts on a first run are expected and correct.** That's the entire point of warming — you're populating a cold cache. Run LSCW again immediately after and you should see mostly HITs on all four buckets.
- **`missing` vary keys aren't a failure mode by themselves.** Those URLs' guest buckets are warm; LiteSpeed just didn't return a validatable key for them. If the number is a meaningful fraction of your total, see [Troubleshooting](#troubleshooting).
- A closing status line states the overall outcome — including a `--resume` hint when failed URLs remain.

---

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Run completed (with or without per-URL errors — check the summary) |
| `1` | Fatal startup problem: invalid site URL, unreachable site, WAF block, no URLs found |
| `130` | Interrupted with `Ctrl+C`; progress saved, continue with `--resume` |

---

## Troubleshooting

**Site returns 429 (Too Many Requests)**
The adaptive delay slows the run down automatically, the affected URLs are retried with gentler settings, and the summary reports the total 429 count. If they persist, raise `--delay` and drop to `--workers 1`.

**Pre-flight fails with HTTP 401/403**
A WAF, security plugin, or bot-protection layer is blocking scripted requests from your IP. Options: allowlist your IP, relax the rule temporarily, or run LSCW from the server itself.

**A large fraction of URLs come back `no-vary`**
The `guest.vary.php` lookup isn't returning a validatable value. Common causes, in order of likelihood:
- LiteSpeed's guest mode is disabled, or vary-based caching is off entirely — in which case `no-vary` is the technically correct outcome, not a bug.
- `guest.vary.php` is blocked by a firewall/WAF rule or security plugin.
- The site uses a non-standard plugin path (rare; see [Configuration Constants](#configuration-constants)).

Watching the Network tab during a real page load in a browser is the fastest way to confirm whether the plugin is issuing a vary cookie at all.

**Sitemap requires authentication or is blocked**
Use `--urls-file` instead: export your URL list from an SEO plugin or a crawler like Screaming Frog, one URL per line.

**Sitemap fetch is blocked despite `--sitemap-ua googlebot`**
Some servers verify Googlebot claims via reverse DNS and treat a failed check as hostile. Try `--sitemap-ua browser`.

**Running on Windows**
Works as-is under Python 3.9+. Use Windows Terminal (or WSL) for correct Unicode rendering; older Command Prompt windows may garble the box-drawing characters.

**Warming a staging or password-protected environment**
The guest bucket still warms as long as the base page loads without auth. The vary lookup only succeeds if `guest.vary.php` is reachable without authentication — this varies by hosting setup.

---

## Configuration Constants

A handful of constants in [`lscw/config.py`](lscw/config.py) tune behavior without touching the CLI:

| Constant | Default | Purpose |
|---|---|---|
| `MAX_TABLE_ROWS` | `16` | Rows shown in the rolling live results table. |
| `CHECKPOINT_SAVE_EVERY` | `10` | How often (in URLs) the checkpoint is flushed to disk. |
| `MAX_SITEMAP_DOCS` | `200` | Safety cap on recursively crawled sitemap documents. |
| `MAX_WORKERS` | `16` | Hard upper bound for `--workers`. |
| `GUEST_VARY_PATH` | `/wp-content/plugins/litespeed-cache/guest.vary.php` | The LiteSpeed vary endpoint. Standard across installations; change only if a host relocated plugin paths. |
| `DESKTOP_UA` / `MOBILE_UA` | Chrome 124 / iPhone Safari | User-Agents used for warming requests. |

---

## License

GNU GPLv3

---

## Contributing

Issues and pull requests are welcome. If you're adding support for another cache plugin or a new URL-discovery method, keep the guest → vary-lookup → conditional-full-cache shape intact — it's what makes the output columns and the `no-vary` reporting meaningful across different sources of URLs. Presentation changes belong in `lscw/ui.py`; the warming logic in `lscw/warmer.py` should never print.
