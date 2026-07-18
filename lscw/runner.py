"""Warming orchestration: sequential and threaded execution with live UI."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

import requests

from .config import CHECKPOINT_SAVE_EVERY, MAX_TABLE_ROWS
from .network import make_session
from .state import AdaptiveDelay, save_checkpoint
from .ui import (
    RICH_AVAILABLE, console, Live,
    build_progress, build_live_view, plain_result_line,
)
from .warmer import crashed_result, warm_url


def run_warming(
    urls: list[str],
    session: requests.Session,
    site_base: str,
    args,
    adaptive: AdaptiveDelay,
    checkpoint_set: set,
    cp_path: Path | None,
    all_results: list[dict],
) -> None:
    total = len(urls)
    live_stats = {"guest_hit": 0, "guest_miss": 0, "full_hit": 0, "full_miss": 0,
                  "m_guest_hit": 0, "m_guest_miss": 0, "m_full_hit": 0, "m_full_miss": 0,
                  "errors": 0, "vary_ok": 0, "no_vary": 0}
    recent_rows: list[dict] = []
    start_time = time.time()
    lock = Lock()
    counters = {"done": 0}

    def _record(result: dict) -> None:
        with lock:
            p1, p2 = result["phase1_status"], result["phase2_status"]
            mg, mf = result["mobile_guest_status"], result["phase3_status"]
            if p1 == "HIT":             live_stats["guest_hit"] += 1
            elif p1 == "MISS":          live_stats["guest_miss"] += 1
            if p2 == "HIT":             live_stats["full_hit"] += 1
            elif p2 == "MISS":          live_stats["full_miss"] += 1
            if mg == "HIT":             live_stats["m_guest_hit"] += 1
            elif mg == "MISS":          live_stats["m_guest_miss"] += 1
            if mf == "HIT":             live_stats["m_full_hit"] += 1
            elif mf == "MISS":          live_stats["m_full_miss"] += 1
            if result["guest_vary_ok"]: live_stats["vary_ok"] += 1
            if result["no_vary"]:       live_stats["no_vary"] += 1
            if result["has_error"]:     live_stats["errors"] += 1
            all_results.append(result)
            recent_rows.append(result)
            if len(recent_rows) > MAX_TABLE_ROWS:
                recent_rows.pop(0)
            counters["done"] += 1
            if not result["has_error"]:
                checkpoint_set.add(result["url"])
            if cp_path and counters["done"] % CHECKPOINT_SAVE_EVERY == 0:
                save_checkpoint(cp_path, checkpoint_set)

        if result["got_429"] or result["has_error"]:
            adaptive.on_problem()
        else:
            adaptive.on_success()

    def _run_sequential(show) -> None:
        for i, url in enumerate(urls, 1):
            result = warm_url(
                session, url, site_base,
                warm_mobile=args.mobile,
                timeout=args.timeout,
                delay_between_phases=args.phase_delay,
            )
            result["_idx"] = i
            _record(result)
            show(result)
            if i < total:
                time.sleep(adaptive.value)

    def _run_threaded(show) -> None:
        # One session per worker thread, throttled submission so the
        # aggregate request rate still honors the adaptive delay.
        tls = threading.local()
        sessions: list[requests.Session] = []
        s_lock = Lock()

        def _thread_session() -> requests.Session:
            s = getattr(tls, "session", None)
            if s is None:
                s = make_session()
                tls.session = s
                with s_lock:
                    sessions.append(s)
            return s

        def _task(idx: int, url: str) -> dict:
            r = warm_url(
                _thread_session(), url, site_base,
                warm_mobile=args.mobile,
                timeout=args.timeout,
                delay_between_phases=args.phase_delay,
            )
            r["_idx"] = idx
            return r

        futures: dict = {}

        def _consume(fut) -> None:
            idx, url = futures.pop(fut)
            try:
                result = fut.result()
            except Exception:
                result = crashed_result(url)
                result["_idx"] = idx
            _record(result)
            show(result)

        executor = ThreadPoolExecutor(max_workers=args.workers)
        try:
            for i, url in enumerate(urls, 1):
                futures[executor.submit(_task, i, url)] = (i, url)
                for fut in [f for f in list(futures) if f.done()]:
                    _consume(fut)
                if i < total:
                    time.sleep(max(0.05, adaptive.value / args.workers))
            for fut in as_completed(list(futures)):
                _consume(fut)
            executor.shutdown(wait=True)
        except BaseException:
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        finally:
            for s in sessions:
                s.close()

    def _run(show) -> None:
        if args.workers == 1:
            _run_sequential(show)
        else:
            _run_threaded(show)

    if RICH_AVAILABLE:
        progress, task = build_progress(total)

        def _renderable():
            return build_live_view(
                progress, recent_rows, live_stats,
                time.time() - start_time, adaptive.value,
            )

        with Live(_renderable(), console=console, refresh_per_second=4) as live:
            def show(result: dict) -> None:
                progress.update(task, advance=1)
                live.update(_renderable())
            _run(show)
    else:
        print(f"\n  {'URL':<58} | {'Guest':<8} | {'vary':^5} | {'Full':<8} | {'M-Guest':<8} | {'M-Full':<8}")
        print("  " + "─" * 112)

        def show(result: dict) -> None:
            print(plain_result_line(result, result["_idx"], total))
        _run(show)
