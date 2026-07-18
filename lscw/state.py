"""Persistent state: checkpoint file and adaptive delay."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from threading import Lock
from urllib.parse import urlparse

from .ui import esc, log


def checkpoint_path(site_base: str) -> Path:
    domain = urlparse(site_base).netloc.replace(".", "_").replace(":", "_")
    return Path(f".lscache_{domain}.checkpoint.json")


def load_checkpoint(path: Path) -> set:
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            completed = data.get("completed", [])
            if isinstance(completed, list):
                return {u for u in completed if isinstance(u, str)}
        except (OSError, ValueError):
            log("WARN", f"Checkpoint file is corrupt, ignoring: {esc(str(path))}")
    return set()


def save_checkpoint(path: Path, completed: set) -> None:
    # Atomic write: a crash mid-write must not corrupt the existing checkpoint.
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(
            json.dumps(
                {"completed": sorted(completed), "saved_at": datetime.now().isoformat()},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError as e:
        if not getattr(save_checkpoint, "_warned", False):
            save_checkpoint._warned = True  # type: ignore[attr-defined]
            log("WARN", f"Checkpoint could not be written ({e}). --resume will not work!")


class AdaptiveDelay:
    """Dynamically adjusts request delays based on success/failure streaks."""

    def __init__(self, base: float, max_factor: float = 4.0):
        self.base = base
        self.current = base
        self.max = base * max_factor
        self._ok_streak = 0
        self._err_streak = 0
        self._lock = Lock()

    @property
    def value(self) -> float:
        with self._lock:
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
