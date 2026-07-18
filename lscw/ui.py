"""Presentation layer: logging, Rich rendering, plain-text fallback."""

from __future__ import annotations

from datetime import datetime

from .utils import short_url

try:
    from rich.console import Console, Group
    from rich.live import Live
    from rich.markup import escape as rich_escape
    from rich.panel import Panel
    from rich.progress import (
        Progress, SpinnerColumn, BarColumn,
        TextColumn, TimeElapsedColumn, TimeRemainingColumn, MofNCompleteColumn,
    )
    from rich.table import Table
    from rich.text import Text
    from rich import box
    RICH_AVAILABLE = True
    console = Console(highlight=False)
except ImportError:
    RICH_AVAILABLE = False
    console = None  # type: ignore[assignment]

ACCENT = "cyan"
BORDER = "bright_black"

__all__ = [
    "RICH_AVAILABLE", "console", "esc", "log", "fmt_duration",
    "print_banner", "build_progress", "build_live_view",
    "plain_result_line", "print_summary", "Live",
]


def esc(text: str) -> str:
    return rich_escape(text) if RICH_AVAILABLE else text


def fmt_duration(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def log(level: str, msg: str) -> None:
    if RICH_AVAILABLE:
        icons = {
            "INFO": ("·", "dim"),
            "OK":   ("✓", "green"),
            "WARN": ("!", "yellow"),
            "ERR":  ("✗", "bold red"),
        }
        icon, style = icons.get(level, ("·", "white"))
        console.print(f"  [{style}]{icon}[/]  {msg}")
    else:
        ts = datetime.now().strftime("%H:%M:%S")
        icons = {"INFO": "·", "OK": "✓", "WARN": "!", "ERR": "✗"}
        print(f"[{ts}] {icons.get(level, '·')} {msg}", flush=True)


# ─── Banner ──────────────────────────────────────────────────────────────────

def print_banner(version: str, rows: list[tuple[str, str]]) -> None:
    if RICH_AVAILABLE:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="dim", min_width=12)
        grid.add_column(min_width=28)
        grid.add_column(style="dim", min_width=12)
        grid.add_column()
        for i in range(0, len(rows), 2):
            left = rows[i]
            right = rows[i + 1] if i + 1 < len(rows) else ("", "")
            grid.add_row(left[0], esc(left[1]), right[0], esc(right[1]))
        console.print()
        console.print(Panel(
            grid,
            title=f"[bold]LSCW[/bold] [dim]· LiteSpeed Cache Warmer v{version}[/dim]",
            title_align="left",
            border_style=BORDER,
            box=box.ROUNDED,
            padding=(1, 2),
        ))
        console.print()
    else:
        print(f"\n{'─'*65}")
        print(f"  LSCW · LiteSpeed Cache Warmer v{version}")
        print(f"{'─'*65}")
        for k, v in rows:
            print(f"  {k:<12} {v}")
        print(f"{'─'*65}\n")


# ─── Live view ───────────────────────────────────────────────────────────────

def build_progress(total: int) -> tuple["Progress", int]:
    progress = Progress(
        SpinnerColumn(style=ACCENT),
        BarColumn(bar_width=None, style="grey35", complete_style=ACCENT, finished_style="green"),
        MofNCompleteColumn(),
        TextColumn("[dim]{task.percentage:>3.0f}%[/]"),
        TimeElapsedColumn(),
        TextColumn("[dim]eta[/]"),
        TimeRemainingColumn(),
        console=console,
        expand=True,
    )
    task = progress.add_task("", total=total)
    return progress, task


def _status_cell(s: str) -> "Text":
    if s == "HIT":        return Text("HIT",      style="bold green")
    if s == "MISS":       return Text("MISS",     style="yellow")
    if s == "SKIP":       return Text("–",        style="dim")
    if s == "NO-CACHE":   return Text("no-cache", style="dim red")
    if s == "NO-VARY":    return Text("no-vary",  style="dim yellow")
    if s == "EXT-REDIR":  return Text("ext →",    style="dim")
    if s == "ERROR":      return Text("error",    style="bold red")
    if s.startswith("E") and s[1:].isdigit():
        return Text(s[1:], style="bold red")
    return Text(s.lower()[:8], style="dim")


def _results_table(rows: list[dict]) -> "Table":
    t = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="dim",
        expand=True,
        padding=(0, 1),
        pad_edge=False,
    )
    t.add_column("#",        width=5,  justify="right", style="dim")
    t.add_column("url",      min_width=30, no_wrap=True)
    t.add_column("guest",    width=8,  justify="center")
    t.add_column("full",     width=8,  justify="center")
    t.add_column("m·guest",  width=8,  justify="center")
    t.add_column("m·full",   width=8,  justify="center")
    t.add_column("vary",     width=4,  justify="center")

    for r in rows:
        vary = Text("✓", style="green") if r["guest_vary_ok"] else Text("✗", style="dim red")
        t.add_row(
            str(r.get("_idx", "?")),
            Text(short_url(r["url"])),
            _status_cell(r["phase1_status"]),
            _status_cell(r["phase2_status"]),
            _status_cell(r["mobile_guest_status"]),
            _status_cell(r["phase3_status"]),
            vary,
        )
    return t


def _hitmiss(hit: int, miss: int) -> str:
    return f"[green]{hit}[/] [dim]hit[/] · [yellow]{miss}[/] [dim]miss[/]"


def build_live_view(progress, rows: list[dict], stats: dict, elapsed: float, delay: float) -> "Group":
    done = stats["guest_hit"] + stats["guest_miss"] + stats["errors"]
    ppu  = elapsed / done if done else 0.0

    sgrid = Table.grid(padding=(0, 2))
    sgrid.add_column(style="dim", min_width=8)
    sgrid.add_column(min_width=24)
    sgrid.add_column(style="dim", min_width=8)
    sgrid.add_column()
    sgrid.add_row(
        "desktop", f"guest {_hitmiss(stats['guest_hit'], stats['guest_miss'])}"
                   f"   full {_hitmiss(stats['full_hit'], stats['full_miss'])}",
        "vary",    f"[green]{stats['vary_ok']} ✓[/]  [yellow]{stats['no_vary']} missing[/]",
    )
    sgrid.add_row(
        "mobile",  f"guest {_hitmiss(stats['m_guest_hit'], stats['m_guest_miss'])}"
                   f"   full {_hitmiss(stats['m_full_hit'], stats['m_full_miss'])}",
        "errors",  ("[red]" if stats["errors"] else "[green]") + str(stats["errors"]) + "[/]"
                   + f"   [dim]{ppu:.1f}s/url · delay {delay:.1f}s[/]",
    )

    progress_panel = Panel(
        Group(progress, Text(), sgrid),
        title="[bold]Progress[/bold]",
        title_align="left",
        border_style=BORDER,
        box=box.ROUNDED,
        padding=(0, 1),
    )

    parts: list = [progress_panel]
    if rows:
        parts.append(Panel(
            _results_table(rows),
            title="[bold]Recent results[/bold]",
            title_align="left",
            border_style=BORDER,
            box=box.ROUNDED,
            padding=(0, 1),
        ))
    return Group(*parts)


def plain_result_line(result: dict, idx: int, total: int) -> str:
    gv = "✓" if result["guest_vary_ok"] else "✗"
    return (
        f"  [{idx:4d}/{total}] {short_url(result['url'], 50):<50} "
        f"| {result['phase1_status']:<8} | {gv:^5} "
        f"| {result['phase2_status']:<8} "
        f"| {result['mobile_guest_status']:<8} | {result['phase3_status']:<8}"
    )


# ─── Summary ─────────────────────────────────────────────────────────────────

def print_summary(stats: dict, total: int, elapsed: float, retried: int = 0) -> None:
    if RICH_AVAILABLE:
        g = Table.grid(padding=(0, 3))
        g.add_column(style="dim", min_width=14)
        g.add_column()
        g.add_row("total urls",    f"[bold]{total}[/]")
        g.add_row("duration",      f"{fmt_duration(elapsed)} [dim]· {elapsed/max(total,1):.1f}s/url[/]")
        g.add_row("", "")
        g.add_row("desktop cache", f"guest {_hitmiss(stats['guest_hit'], stats['guest_miss'])}"
                                   f"     full {_hitmiss(stats['full_hit'], stats['full_miss'])}")
        g.add_row("mobile cache",  f"guest {_hitmiss(stats['mobile_guest_hit'], stats['mobile_guest_miss'])}"
                                   f"     full {_hitmiss(stats['mobile_full_hit'], stats['mobile_full_miss'])}")
        g.add_row("", "")
        nv = stats["no_vary"]
        g.add_row("vary keys",     f"[green]{stats['vary_ok']} obtained[/]"
                                   + (f" · [yellow]{nv} missing[/]" if nv else " [dim]· 0 missing[/]"))
        if stats.get("rate_limited"):
            g.add_row("rate limited", f"[yellow]{stats['rate_limited']} responses (HTTP 429)[/]")
        if retried:
            g.add_row("auto-retried", str(retried))
        g.add_row("errors",        "[green]0[/]" if stats["errors"] == 0 else f"[red]{stats['errors']}[/]")

        console.print()
        console.print(Panel(
            g,
            title="[bold]Summary[/bold]",
            title_align="left",
            border_style=BORDER,
            box=box.ROUNDED,
            padding=(1, 2),
        ))
        console.print()
    else:
        print(f"\n{'─'*60}")
        print("  SUMMARY")
        print(f"{'─'*60}")
        print(f"  total urls    {total}")
        print(f"  duration      {fmt_duration(elapsed)} · {elapsed/max(total,1):.1f}s/url")
        print(f"  desktop       guest HIT {stats['guest_hit']} MISS {stats['guest_miss']}"
              f"  ·  full HIT {stats['full_hit']} MISS {stats['full_miss']}")
        print(f"  mobile        guest HIT {stats['mobile_guest_hit']} MISS {stats['mobile_guest_miss']}"
              f"  ·  full HIT {stats['mobile_full_hit']} MISS {stats['mobile_full_miss']}")
        print(f"  vary keys     {stats['vary_ok']} obtained · {stats['no_vary']} missing")
        if stats.get("rate_limited"):
            print(f"  rate limited  {stats['rate_limited']} responses (HTTP 429)")
        if retried:
            print(f"  auto-retried  {retried}")
        print(f"  errors        {stats['errors']}")
        print(f"{'─'*60}\n")
