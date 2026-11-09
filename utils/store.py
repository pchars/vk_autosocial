import json
import os
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set

SCHEMA = """
CREATE TABLE IF NOT EXISTS communities (
    vk_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    autofill INTEGER NOT NULL DEFAULT 0,
    posts_per_day INTEGER NOT NULL DEFAULT 3,
    queue_days INTEGER NOT NULL DEFAULT 90,
    photos_per_post INTEGER NOT NULL DEFAULT 6,
    hashtags TEXT NOT NULL DEFAULT '',
    chat_link TEXT NOT NULL DEFAULT '',
    chat_text TEXT NOT NULL DEFAULT '',
    chat_every INTEGER NOT NULL DEFAULT 0,
    posts_made INTEGER NOT NULL DEFAULT 0,
    post_hours TEXT NOT NULL DEFAULT '',
    post_days TEXT NOT NULL DEFAULT '',
    use_quotes INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
    community_id INTEGER NOT NULL REFERENCES communities(vk_id) ON DELETE CASCADE,
    vk_id INTEGER NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (community_id, vk_id)
);
CREATE TABLE IF NOT EXISTS seen_photos (
    community_id INTEGER NOT NULL,
    photo_key TEXT NOT NULL,
    PRIMARY KEY (community_id, photo_key)
);
CREATE TABLE IF NOT EXISTS posted_hashes (
    community_id INTEGER NOT NULL,
    hash INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS posted_hashes_community ON posted_hashes(community_id);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    community_id INTEGER,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    stats_json TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS admins (
    tg_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

SETTINGS_LIMITS = {
    "posts_per_day": (1, 24),
    "queue_days": (1, 120),
    "photos_per_post": (1, 10),
    "chat_every": (0, 50),
}
EDITABLE = set(SETTINGS_LIMITS) | {
    "autofill", "use_quotes", "hashtags", "chat_link", "chat_text", "name", "url", "post_hours", "post_days",
}
FLAGS = {"autofill", "use_quotes"}
INT64 = 1 << 63
DEFAULT_QUEUE_DAYS = 90
DEFAULT_HASHTAGS = "#эстетика #aesthetics #вдохновение #состояние_души #cute"
ADDED_COMMUNITY_COLUMNS = {
    "post_hours": "TEXT NOT NULL DEFAULT ''",
    "post_days": "TEXT NOT NULL DEFAULT ''",
    "use_quotes": "INTEGER NOT NULL DEFAULT 0",
}


def parse_numbers(raw: Any, low: int, high: int) -> List[int]:
    """'9, 14,20' or [9, 14, 20] -> sorted unique numbers within [low, high]."""
    chunks = raw if isinstance(raw, (list, tuple, set)) else str(raw or "").replace(";", ",").split(",")
    numbers = set()
    for chunk in chunks:
        text = str(chunk).strip()
        if text.lstrip("-").isdigit() and low <= int(text) <= high:
            numbers.add(int(text))
    return sorted(numbers)


@dataclass
class Community:
    vk_id: int
    name: str = ""
    url: str = ""
    autofill: bool = False
    posts_per_day: int = 3
    queue_days: int = DEFAULT_QUEUE_DAYS
    photos_per_post: int = 6
    hashtags: str = ""
    chat_link: str = ""
    chat_text: str = ""
    chat_every: int = 0
    posts_made: int = 0
    # Local hours (0-23) and weekdays (0 = Monday). Empty hours: evenly spaced posts; empty days: every day.
    post_hours: str = ""
    post_days: str = ""
    # The quotes file fits only some communities, so posts go without a caption unless it is switched on.
    use_quotes: bool = False

    @property
    def hours(self) -> List[int]:
        return parse_numbers(self.post_hours, 0, 23)

    @property
    def days(self) -> List[int]:
        return parse_numbers(self.post_days, 0, 6) or list(range(7))

    @property
    def queue_target(self) -> int:
        return max(self.posts_per_day, self.posts_per_day * self.queue_days * len(self.days) // 7)

    @property
    def title(self) -> str:
        return self.name or f"club{self.vk_id}"


@dataclass
class Source:
    community_id: int
    vk_id: int
    name: str = ""
    url: str = ""

    @property
    def title(self) -> str:
        return self.name or f"club{self.vk_id}"


_COMMUNITY_COLUMNS = [f.name for f in fields(Community)]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._tx() as conn:
            conn.executescript(SCHEMA)
            self._migrate_community_columns(conn)
            self._migrate_legacy_groups(conn)
            self._migrate_legacy_jobs(conn)
            # Only one bot runs at a time, so anything still "running" was cut off by a restart.
            conn.execute("UPDATE jobs SET status = 'interrupted', finished_at = ? WHERE status = 'running'", (_now(),))
        # The database holds the VK token: keep it away from other users of the server.
        if os.name != "nt":
            os.chmod(self.db_path, 0o600)

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                yield conn

    def _migrate_legacy_jobs(self, conn: sqlite3.Connection) -> None:
        """The old jobs table was global and had no community column."""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        if "community_id" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN community_id INTEGER")

    def _migrate_community_columns(self, conn: sqlite3.Connection) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(communities)")}
        for name, definition in ADDED_COMMUNITY_COLUMNS.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE communities ADD COLUMN {name} {definition}")

    def _migrate_legacy_groups(self, conn: sqlite3.Connection) -> None:
        """The old schema had one global target and global sources in table `groups`."""
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'groups'"
        ).fetchone()
        if not exists:
            return
        rows = conn.execute("SELECT vk_id, role, screen_name, url, enabled FROM groups").fetchall()
        target = next((r for r in rows if r["role"] == "target" and r["enabled"]), None)
        if target:
            conn.execute(
                "INSERT OR IGNORE INTO communities (vk_id, name, url, created_at) VALUES (?, ?, ?, ?)",
                (target["vk_id"], target["screen_name"] or "", target["url"] or "", _now()),
            )
            for row in rows:
                if row["role"] == "source" and row["enabled"]:
                    conn.execute(
                        "INSERT OR IGNORE INTO sources (community_id, vk_id, name, url, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (target["vk_id"], row["vk_id"], row["screen_name"] or "", row["url"] or "", _now()),
                    )
        conn.execute("DROP TABLE groups")

    # Communities

    def add_community(self, vk_id: int, name: str = "", url: str = "") -> Community:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO communities (vk_id, name, url, autofill, queue_days, hashtags, created_at) "
                "VALUES (?, ?, ?, 0, ?, ?, ?) "
                "ON CONFLICT(vk_id) DO UPDATE SET name = excluded.name, url = excluded.url",
                (int(vk_id), name, url, DEFAULT_QUEUE_DAYS, DEFAULT_HASHTAGS, _now()),
            )
        return self.get_community(vk_id)

    def get_community(self, vk_id: int) -> Optional[Community]:
        with self._tx() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_COMMUNITY_COLUMNS)} FROM communities WHERE vk_id = ?", (int(vk_id),)
            ).fetchone()
        return _community(row) if row else None

    def list_communities(self) -> List[Community]:
        with self._tx() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(_COMMUNITY_COLUMNS)} FROM communities ORDER BY created_at, vk_id"
            ).fetchall()
        return [_community(row) for row in rows]

    def update_community(self, vk_id: int, **values: Any) -> Community:
        unknown = set(values) - EDITABLE
        if unknown:
            raise KeyError(f"Not editable: {sorted(unknown)}")
        clean: Dict[str, Any] = {}
        for key, value in values.items():
            if key in SETTINGS_LIMITS:
                low, high = SETTINGS_LIMITS[key]
                value = min(high, max(low, int(value)))
            elif key in FLAGS:
                value = 1 if value else 0
            elif key == "post_hours":
                value = ",".join(str(h) for h in parse_numbers(value, 0, 23))
            elif key == "post_days":
                days = parse_numbers(value, 0, 6)
                value = "" if len(days) == 7 else ",".join(str(d) for d in days)
            else:
                value = str(value or "").strip()
            clean[key] = value
        if clean.get("post_hours"):
            clean["posts_per_day"] = len(clean["post_hours"].split(","))
        elif "posts_per_day" in clean and "post_hours" not in clean:
            current = self.get_community(vk_id)
            if current and current.hours and len(current.hours) != clean["posts_per_day"]:
                # The new frequency no longer matches the chosen hours: back to evenly spaced posts.
                clean["post_hours"] = ""
        if clean:
            assignments = ", ".join(f"{key} = ?" for key in clean)
            with self._tx() as conn:
                conn.execute(
                    f"UPDATE communities SET {assignments} WHERE vk_id = ?",
                    [*clean.values(), int(vk_id)],
                )
        community = self.get_community(vk_id)
        if community is None:
            raise KeyError(f"Community {vk_id} not found")
        return community

    def increment_posts_made(self, vk_id: int) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE communities SET posts_made = posts_made + 1 WHERE vk_id = ?", (int(vk_id),))

    def delete_community(self, vk_id: int) -> bool:
        with self._tx() as conn:
            cursor = conn.execute("DELETE FROM communities WHERE vk_id = ?", (int(vk_id),))
            conn.execute("DELETE FROM seen_photos WHERE community_id = ?", (int(vk_id),))
            conn.execute("DELETE FROM posted_hashes WHERE community_id = ?", (int(vk_id),))
        return cursor.rowcount > 0

    # Sources

    def add_source(self, community_id: int, vk_id: int, name: str = "", url: str = "") -> bool:
        """Returns False when the source was already attached."""
        with self._tx() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO sources (community_id, vk_id, name, url, created_at) VALUES (?, ?, ?, ?, ?)",
                (int(community_id), int(vk_id), name, url, _now()),
            )
        return cursor.rowcount > 0

    def remove_source(self, community_id: int, vk_id: int) -> bool:
        with self._tx() as conn:
            cursor = conn.execute(
                "DELETE FROM sources WHERE community_id = ? AND vk_id = ?", (int(community_id), int(vk_id))
            )
        return cursor.rowcount > 0

    def list_sources(self, community_id: int) -> List[Source]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT community_id, vk_id, name, url FROM sources WHERE community_id = ? ORDER BY created_at, vk_id",
                (int(community_id),),
            ).fetchall()
        return [Source(**dict(row)) for row in rows]

    # Duplicate tracking

    def seen_keys(self, community_id: int) -> Set[str]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT photo_key FROM seen_photos WHERE community_id = ?", (int(community_id),)
            ).fetchall()
        return {row["photo_key"] for row in rows}

    def mark_seen(self, community_id: int, keys: Iterable[str]) -> None:
        with self._tx() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO seen_photos (community_id, photo_key) VALUES (?, ?)",
                [(int(community_id), key) for key in keys],
            )

    def posted_hashes(self, community_id: int) -> List[int]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT hash FROM posted_hashes WHERE community_id = ?", (int(community_id),)
            ).fetchall()
        return [row["hash"] % (1 << 64) for row in rows]

    def add_posted_hashes(self, community_id: int, hashes: Iterable[int]) -> None:
        # SQLite INTEGER is signed 64-bit; image hashes use the full unsigned range.
        with self._tx() as conn:
            conn.executemany(
                "INSERT INTO posted_hashes (community_id, hash) VALUES (?, ?)",
                [(int(community_id), h - (1 << 64) if h >= INT64 else h) for h in hashes],
            )

    # Jobs

    def start_job(self, job_type: str, community_id: Optional[int] = None) -> int:
        with self._tx() as conn:
            cursor = conn.execute(
                "INSERT INTO jobs (community_id, type, status, started_at) VALUES (?, ?, 'running', ?)",
                (community_id, job_type, _now()),
            )
        return int(cursor.lastrowid)

    def finish_job(self, job_id: int, status: str, stats: Optional[Dict[str, Any]] = None, error: str = "") -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, finished_at = ?, stats_json = ?, error = ? WHERE id = ?",
                (status, _now(), json.dumps(stats, default=str) if stats else None, error, int(job_id)),
            )

    def last_job(self, community_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM jobs"
        params: List[Any] = []
        if community_id is not None:
            query += " WHERE community_id = ?"
            params.append(int(community_id))
        with self._tx() as conn:
            row = conn.execute(query + " ORDER BY id DESC LIMIT 1", params).fetchone()
        return dict(row) if row else None

    # Admins added from Telegram

    def add_admin(self, tg_id: int, name: str = "") -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO admins (tg_id, name, created_at) VALUES (?, ?, ?)",
                (int(tg_id), name, _now()),
            )

    def remove_admin(self, tg_id: int) -> bool:
        with self._tx() as conn:
            cursor = conn.execute("DELETE FROM admins WHERE tg_id = ?", (int(tg_id),))
        return cursor.rowcount > 0

    def list_admins(self) -> List[Dict[str, Any]]:
        with self._tx() as conn:
            rows = conn.execute("SELECT tg_id, name FROM admins ORDER BY created_at").fetchall()
        return [dict(row) for row in rows]

    # Key-value settings

    def get_setting(self, key: str, default: str = "") -> str:
        with self._tx() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )


def _community(row: sqlite3.Row) -> Community:
    data = dict(row)
    for flag in FLAGS:
        data[flag] = bool(data[flag])
    return Community(**data)
