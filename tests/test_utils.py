import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from utils.config import Config, parse_ids
from utils.dates import human_date, plural
from utils.lock import AlreadyRunningError, SingleInstanceLock
from utils.logger import SessionFileHandler, setup_logging
from utils.store import DEFAULT_HASHTAGS, Store
from utils.vk_links import community_ref, community_refs, find_community_ref, mentions_vk

from tests.fakes import TempDirTestCase


class ConfigTests(unittest.TestCase):
    def test_env_overrides_dotenv_and_paths_resolve_to_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / ".env").write_text("TELEGRAM_BOT_TOKEN=file\nVK_TOKEN=vk\nTELEGRAM_ADMIN_IDS=1, 2\n", encoding="utf-8")
            config = Config.load(base, env={"TELEGRAM_BOT_TOKEN": "env", "AUTOFILL_INTERVAL_MIN": "1"})
        self.assertEqual(config.telegram_token, "env")
        self.assertEqual(config.vk_token, "vk")
        self.assertEqual(config.admin_ids, [1, 2])
        self.assertEqual(config.db_path, base / "data" / "app.db")
        self.assertEqual(config.images_dir, base / "data" / "images")
        self.assertEqual(config.autofill_interval_min, 5)

    def test_absolute_data_dir_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Config.load(Path(tmp), env={"DATA_DIR": tmp})
        self.assertEqual(config.data_dir, Path(tmp))

    def test_parse_ids(self):
        self.assertEqual(parse_ids("1;2, 3,,"), [1, 2, 3])
        self.assertEqual(parse_ids(""), [])


class VkLinksTests(unittest.TestCase):
    def test_community_ref_variants(self):
        cases = {
            "https://vk.com/club123": "123",
            "https://vk.ru/public456/": "456",
            "vk.com/event7": "7",
            "https://m.vk.com/flowers_daily?from=feed": "flowers_daily",
            "https://vk.com/wall-36599648_10": "36599648",
            "https://vk.com/flowers?w=wall-55_3": "55",
            "@cats": "cats",
            "36599648": "36599648",
            "club99": "99",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(community_ref(text), expected)

    def test_community_ref_rejects_non_communities(self):
        for text in ["", "https://google.com/club1", "https://vk.com/id123", "https://vk.com/feed", "привет", "https://vk.com/"]:
            with self.subTest(text=text):
                self.assertIsNone(community_ref(text))

    def test_community_refs_dedupes_and_keeps_order(self):
        text = "https://vk.com/club1\nhttps://vk.com/club2, https://vk.ru/club1 мусор"
        self.assertEqual(community_refs(text), ["1", "2"])

    def test_find_community_ref_in_free_text(self):
        self.assertEqual(find_community_ref("flowers"), "flowers")
        self.assertEqual(find_community_ref("смотри вот эту https://vk.com/flowers, классная"), "flowers")
        self.assertEqual(find_community_ref("look at club77"), "77")
        self.assertIsNone(find_community_ref("просто слова без ссылок"))

    def test_mentions_vk(self):
        self.assertTrue(mentions_vk("посмотри https://vk.com/abc"))
        self.assertTrue(mentions_vk("club123"))
        self.assertTrue(mentions_vk("@cats"))
        self.assertFalse(mentions_vk("привет, как дела"))


class DatesTests(unittest.TestCase):
    def test_human_date_uses_timezone(self):
        self.assertEqual(human_date(0, "Europe/Moscow"), "1 января в 03:00")
        self.assertEqual(human_date(0, "UTC"), "1 января в 00:00")

    def test_plural(self):
        forms = ("пост", "поста", "постов")
        expected = {1: "1 пост", 2: "2 поста", 5: "5 постов", 11: "11 постов", 21: "21 пост", 104: "104 поста", 0: "0 постов"}
        for count, text in expected.items():
            self.assertEqual(plural(count, *forms), text)


class LockTests(unittest.TestCase):
    def test_second_process_cannot_take_the_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bot.lock"
            with SingleInstanceLock(path):
                code = (
                    "import sys; from utils.lock import SingleInstanceLock, AlreadyRunningError\n"
                    f"lock = SingleInstanceLock(r'{path}')\n"
                    "try:\n    lock.acquire()\nexcept AlreadyRunningError:\n    sys.exit(3)\nsys.exit(0)\n"
                )
                result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parent.parent)
                self.assertEqual(result.returncode, 3)
                self.assertEqual(path.read_text(encoding="utf-8"), str(os.getpid()))
            lock = SingleInstanceLock(path)
            lock.acquire()
            lock.release()
            lock.release()

    def test_already_running_error_is_runtime_error(self):
        self.assertTrue(issubclass(AlreadyRunningError, RuntimeError))


class LoggerTests(unittest.TestCase):
    def test_log_file_per_start_and_midnight_with_cleanup(self):
        import logging
        import os
        from datetime import datetime

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            started = datetime(2026, 11, 8, 21, 58, 31)
            old = folder / "app_2026-09-01_10-00-00.log"
            recent = folder / "app_2026-11-01_10-00-00.log"
            other = folder / "other.log"
            for path in (old, recent, other):
                path.write_text("old", encoding="utf-8")
            os.utime(old, (started.timestamp() - 40 * 86400,) * 2)
            os.utime(other, (started.timestamp() - 40 * 86400,) * 2)

            clock = [started]
            handler = SessionFileHandler(folder, keep_days=30, now=lambda: clock[0])
            logger = logging.getLogger("session-test")
            logger.propagate = False
            logger.addHandler(handler)
            try:
                logger.warning("first")
                clock[0] = datetime(2026, 11, 9, 0, 0, 5)
                logger.warning("second")
            finally:
                logger.removeHandler(handler)
                handler.close()
            self.assertFalse(old.exists())
            self.assertTrue(recent.exists() and other.exists())
            self.assertIn("first", (folder / "app_2026-11-08_21-58-31.log").read_text(encoding="utf-8"))
            self.assertIn("second", (folder / "app_2026-11-09_00-00-05.log").read_text(encoding="utf-8"))

            restarted = SessionFileHandler(folder, now=lambda: started)
            restarted.close()
            self.assertTrue(restarted.baseFilename.endswith("app_2026-11-08_21-58-31_1.log"))

    def test_setup_logging_writes_new_file(self):
        import logging

        with tempfile.TemporaryDirectory() as tmp:
            setup_logging("debug", Path(tmp))
            logging.getLogger("x").info("hello")
            root = logging.getLogger()
            for handler in root.handlers:
                handler.flush()
            files = list(Path(tmp).glob("app_*.log"))
            self.assertEqual(len(files), 1)
            self.assertIn("hello", files[0].read_text(encoding="utf-8"))
            self.assertEqual(root.level, logging.DEBUG)
            for handler in list(root.handlers):
                handler.close()
                root.removeHandler(handler)
        setup_logging("nonsense")
        self.assertEqual(logging.getLogger().level, logging.INFO)


class StoreTests(TempDirTestCase):
    def test_community_crud_and_limits(self):
        new = self.store.add_community(10, "Цветы", "https://vk.com/flowers")
        self.assertFalse(new.autofill)
        self.assertEqual(new.hashtags, DEFAULT_HASHTAGS)
        community = self.store.update_community(10, posts_per_day=100, queue_days=0, autofill=True, hashtags="  #a ")
        self.assertEqual(community.posts_per_day, 24)
        self.assertEqual(community.queue_days, 1)
        self.assertTrue(community.autofill)
        self.assertTrue(self.store.add_community(10, "Цветы 2").autofill)
        self.assertEqual(community.hashtags, "#a")
        self.assertEqual(community.queue_target, 24)
        self.assertEqual(community.title, "Цветы")
        with self.assertRaises(KeyError):
            self.store.update_community(10, posts_made=5)
        with self.assertRaises(KeyError):
            self.store.update_community(999, hashtags="x")
        self.store.increment_posts_made(10)
        self.assertEqual(self.store.get_community(10).posts_made, 1)
        self.store.add_community(10, "Новое имя")
        self.assertEqual(self.store.get_community(10).name, "Новое имя")
        self.assertEqual([c.vk_id for c in self.store.list_communities()], [10])

    def test_posting_hours_and_days(self):
        self.store.add_community(10)
        community = self.store.update_community(10, post_hours="20, 9,9, 25, x, 14", post_days=[6, 0, 9])
        self.assertEqual((community.post_hours, community.hours, community.posts_per_day), ("9,14,20", [9, 14, 20], 3))
        self.assertEqual((community.post_days, community.days), ("0,6", [0, 6]))
        self.assertEqual(self.store.update_community(10, post_days="0,1,2,3,4,5,6").post_days, "")
        self.assertEqual(self.store.get_community(10).days, list(range(7)))
        self.assertEqual(self.store.update_community(10, posts_per_day=3).post_hours, "9,14,20")
        changed = self.store.update_community(10, posts_per_day=5)
        self.assertEqual((changed.post_hours, changed.posts_per_day), ("", 5))

    def test_old_communities_table_gets_schedule_columns(self):
        path = self.tmp / "old.db"
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("CREATE TABLE communities (vk_id INTEGER PRIMARY KEY, name TEXT NOT NULL DEFAULT '', "
                         "url TEXT NOT NULL DEFAULT '', autofill INTEGER NOT NULL DEFAULT 0, "
                         "posts_per_day INTEGER NOT NULL DEFAULT 3, queue_days INTEGER NOT NULL DEFAULT 90, "
                         "photos_per_post INTEGER NOT NULL DEFAULT 6, hashtags TEXT NOT NULL DEFAULT '', "
                         "chat_link TEXT NOT NULL DEFAULT '', chat_text TEXT NOT NULL DEFAULT '', "
                         "chat_every INTEGER NOT NULL DEFAULT 0, posts_made INTEGER NOT NULL DEFAULT 0, "
                         "created_at TEXT NOT NULL)")
            conn.execute("INSERT INTO communities (vk_id, name, created_at) VALUES (5, 'Старая', 'x')")
            conn.commit()
        store = Store(path)
        store.init()
        community = store.get_community(5)
        self.assertEqual((community.name, community.post_hours, community.days), ("Старая", "", list(range(7))))
        self.assertFalse(community.use_quotes)

    def test_sources_are_per_community_and_cascade(self):
        self.store.add_community(1)
        self.store.add_community(2)
        self.assertTrue(self.store.add_source(1, 100, "A"))
        self.assertFalse(self.store.add_source(1, 100, "A"))
        self.store.add_source(2, 200)
        self.assertEqual([s.vk_id for s in self.store.list_sources(1)], [100])
        self.assertEqual(self.store.list_sources(2)[0].title, "club200")
        self.assertTrue(self.store.remove_source(1, 100))
        self.assertFalse(self.store.remove_source(1, 100))
        self.store.mark_seen(2, ["a"])
        self.store.add_posted_hashes(2, [5])
        self.assertTrue(self.store.delete_community(2))
        self.assertEqual(self.store.list_sources(2), [])
        self.assertEqual(self.store.seen_keys(2), set())
        self.assertEqual(self.store.posted_hashes(2), [])
        self.assertIsNone(self.store.get_community(2))

    def test_hashes_keep_full_unsigned_range(self):
        big = (1 << 64) - 1
        self.store.add_posted_hashes(1, [0, 12345, big, 1 << 63])
        self.assertEqual(sorted(self.store.posted_hashes(1)), sorted([0, 12345, big, 1 << 63]))

    def test_seen_keys(self):
        self.store.mark_seen(1, ["a", "b", "a"])
        self.assertEqual(self.store.seen_keys(1), {"a", "b"})
        self.assertEqual(self.store.seen_keys(2), set())

    def test_jobs_admins_settings(self):
        job = self.store.start_job("fill", 1)
        self.store.finish_job(job, "ok", stats={"posts": 3})
        self.store.start_job("other")
        self.assertEqual(self.store.last_job(1)["status"], "ok")
        self.assertEqual(self.store.last_job()["type"], "other")
        self.store.add_admin(7, "Аня")
        self.assertEqual(self.store.list_admins(), [{"tg_id": 7, "name": "Аня"}])
        self.assertTrue(self.store.remove_admin(7))
        self.assertFalse(self.store.remove_admin(7))
        self.assertEqual(self.store.get_setting("k", "d"), "d")
        self.store.set_setting("k", "v1")
        self.store.set_setting("k", "v2")
        self.assertEqual(self.store.get_setting("k"), "v2")

    def test_legacy_groups_table_is_migrated(self):
        path = self.tmp / "legacy.db"
        with sqlite3.connect(path) as conn:
            conn.execute(
                "CREATE TABLE groups (vk_id INTEGER, role TEXT, screen_name TEXT, url TEXT, enabled INTEGER, created_at TEXT)"
            )
            conn.executemany("INSERT INTO groups VALUES (?, ?, ?, ?, ?, '')", [
                (1, "target", "old", "", 0),
                (2, "target", "main", "https://vk.ru/main", 1),
                (10, "source", "s1", "", 1),
                (11, "source", "s2", "", 0),
            ])
        store = Store(path)
        store.init()
        store.init()
        self.assertEqual([c.vk_id for c in store.list_communities()], [2])
        self.assertEqual([s.vk_id for s in store.list_sources(2)], [10])
        with sqlite3.connect(path) as conn:
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name = 'groups'").fetchone())

    def test_legacy_jobs_table_gets_community_column(self):
        path = self.tmp / "legacy_jobs.db"
        with sqlite3.connect(path) as conn:
            conn.execute(
                "CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, status TEXT NOT NULL, "
                "started_at TEXT, finished_at TEXT, stats_json TEXT, error TEXT)"
            )
            conn.execute("INSERT INTO jobs (type, status) VALUES ('old', 'ok')")
        store = Store(path)
        store.init()
        store.init()
        store.add_community(5)
        store.finish_job(store.start_job("fill", 5), "ok")
        self.assertEqual(store.last_job(5)["type"], "fill")
        store.start_job("analytics", 5)
        store.init()
        self.assertEqual(store.last_job(5)["status"], "interrupted")

    def test_legacy_without_target_just_drops_table(self):
        path = self.tmp / "legacy2.db"
        with sqlite3.connect(path) as conn:
            conn.execute(
                "CREATE TABLE groups (vk_id INTEGER, role TEXT, screen_name TEXT, url TEXT, enabled INTEGER, created_at TEXT)"
            )
        store = Store(path)
        store.init()
        self.assertEqual(store.list_communities(), [])


if __name__ == "__main__":
    unittest.main()
