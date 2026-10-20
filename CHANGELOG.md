# Changelog

All notable changes to LSCW are listed here. Versions follow [Semantic Versioning](https://semver.org/).

## [1.2.0] — 2026-10-20

### Fixed
- **Sites without LiteSpeed Guest Mode no longer get uncached page renders.** `guest.vary.php` hands out a guest vary cookie even when Guest Mode is off, but LiteSpeed never stores requests carrying it — so on those sites (LiteSpeed's default) every desktop and mobile "full" request was a full, uncached page render that warmed nothing. Measured on a shared host: 6 requests and ~34 s per URL before, 1–2 requests and ~1.5 s per URL now.
- LSCW no longer crashes on start when `rich` isn't installed (`ImportError: cannot import name 'Live'`, present since 1.1.0). `rich` is optional again, as documented.
- `Accept-Encoding` advertises `br` only when `brotli` or `brotlicffi` is installed. Without a decoder a brotli-compressed body couldn't be read, which silently broke the HTML inspection (inline vary value, Guest Mode check).

### Added
- `--vary {auto,on,off}` (default `auto`): the vary-keyed phases run only when the page carries LiteSpeed's Guest Mode script — the same script that makes a real browser call `guest.vary.php`. `on` forces them, `off` disables them. Skipped phases are reported as `off`.
- Guest Mode detection is reported per run (`guest mode  detected on X · not on Y`).
- Shared mobile copy detection: when the desktop request was a `MISS` and the mobile request right after it is already a `HIT`, the URL is reported as sharing one copy (LiteSpeed "Cache Mobile" off), with a hint to use `--no-mobile`.
- `requirements.txt`; offline unit tests (`python -m unittest discover -s tests -t .`); GitHub Actions workflow running them on Linux and Windows.
- Banner rows for `vary` and `brotli`.

### Changed
- `--sitemap-ua` now defaults to `browser`. A spoofed Googlebot is blocked wherever Googlebot is verified (reverse DNS, CDN verified-bot rules); `--sitemap-ua googlebot` is still available.
- README: what each cache bucket depends on (Guest Mode, Cache Mobile), Windows commands (`python` / `py` instead of `python3`), virtual environment setup, new troubleshooting entries.

## [1.1.0] — 2026-07-18

### Architecture
- Project restructured from a single script into a Python package: `config`, `utils`, `ui`, `state`, `network`, `warmer`, `runner`, `cli`.
- Two entry points: `python3 lscw.py ...` or `python3 -m lscw ...`.

### Fixed
- Checkpoint records every completed URL; file writes are atomic.
- Ctrl+C is handled gracefully: progress saved, exit code 130, continue with `--resume`.
- Adaptive delay is enforced in parallel mode (`--workers > 1`).
- One HTTP session per worker thread; all sessions are closed on exit.
- HTTP 4xx/5xx responses (including 429) are treated as failures and sent to the auto-retry pass.
- Retry results replace the originals, so summary stats are not double-counted.
- Crashed worker threads are recorded as errors.

### Security
- URLs outside the `--site` host are filtered out; off-site redirects are detected and skipped.
- Rich markup / terminal escape injection via URLs is prevented.
- Self-defeating `Cache-Control: no-cache` request headers removed.

### Added
- `--sitemap-ua {googlebot,browser}`; gzip-compressed sitemaps, CDATA and XML-entity handling, a 200-document crawl cap.
- Pre-flight check aborts on any 4xx with a WAF hint on 401/403.
- Summary reports mobile buckets separately and counts HTTP 429 responses.

### UI
- Redesigned terminal interface with titled panels, live desktop + mobile counters and per-URL five-phase result columns.

## [1.0.0] — 2026-05-18

- Initial release.

[1.2.0]: https://github.com/omerdocs/lscw/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/omerdocs/lscw/releases/tag/v1.1.0
