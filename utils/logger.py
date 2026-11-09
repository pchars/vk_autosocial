import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}
LOG_PREFIX = "app_"
KEEP_DAYS = 30


class SessionFileHandler(logging.FileHandler):
    """A new file on every start and every midnight (app_2026-11-08_21-58-31.log).

    Files older than keep_days are deleted.
    """

    def __init__(self, folder: Path, keep_days: int = KEEP_DAYS, now: Callable[[], datetime] = datetime.now):
        self.folder = Path(folder)
        self.keep_days = keep_days
        self.now = now
        started = now()
        self.day = started.date()
        super().__init__(self._path(started), encoding="utf-8", delay=True)
        self._remove_old(started)

    def emit(self, record: logging.LogRecord) -> None:
        current = self.now()
        if current.date() != self.day:
            if self.stream:
                self.stream.close()
                self.stream = None  # type: ignore[assignment]
            self.day = current.date()
            self.baseFilename = os.path.abspath(self._path(current))
            self._remove_old(current)
        super().emit(record)

    def _path(self, moment: datetime) -> Path:
        path = self.folder / f"{LOG_PREFIX}{moment:%Y-%m-%d_%H-%M-%S}.log"
        suffix = 1
        while path.exists():
            path = self.folder / f"{LOG_PREFIX}{moment:%Y-%m-%d_%H-%M-%S}_{suffix}.log"
            suffix += 1
        return path

    def _remove_old(self, moment: datetime) -> None:
        oldest = (moment - timedelta(days=self.keep_days)).timestamp()
        for path in self.folder.glob(f"{LOG_PREFIX}*.log"):
            try:
                if path.stat().st_mtime < oldest:
                    path.unlink()
            except OSError:
                continue


def setup_logging(level: str = "info", log_dir: Optional[Path] = None) -> None:
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(LEVELS.get(level.strip().lower(), logging.INFO))

    # Third-party DEBUG output contains request URLs with tokens.
    for noisy in ("aiohttp", "aiogram.event", "asyncio", "PIL", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = SessionFileHandler(log_dir)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
