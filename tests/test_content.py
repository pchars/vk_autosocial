import io
import os
import time
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image

from api.vk import VkAuthError, VkError, VkFloodError
from services import publisher as publisher_module
from services.collector import (
    Collector, CollectStats, best_photo_url, extract_photos, interleave, is_ad, popularity,
)
from services.images import image_hash, is_duplicate, list_images, story_jpeg, wall_jpeg
from services.publisher import (
    MAX_POSTPONED, MIN_PUBLISH_LEAD_SEC, Publisher, QueueInfo, build_message, chat_message, load_quotes,
    next_publish_date, next_slot, posts_needed, schedule_base, slot_step,
)
from services.stories import StoryPublisher
from utils.store import Community, Source

from tests.fakes import AsyncTempDirTestCase, FakeVk, TempDirTestCase, counter, make_jpeg, photo


async def no_sleep(_seconds: float) -> None:
    return None


def write_images(folder: Path, count: int, start: int = 0) -> list:
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for index in range(start, start + count):
        path = folder / f"img_{index:03}.jpg"
        path.write_bytes(make_jpeg(index))
        os.utime(path, (time.time() + index, time.time() + index))
        paths.append(path)
    return paths


class ImagesTests(TempDirTestCase):
    def test_hash_finds_recompressed_copy_but_not_other_picture(self):
        original = make_jpeg(1, size=(300, 260))
        with Image.open(io.BytesIO(original)) as image:
            buffer = io.BytesIO()
            image.resize((150, 130)).save(buffer, format="JPEG", quality=60)
        copy_hash = image_hash(buffer.getvalue())
        self.assertTrue(is_duplicate(copy_hash, [image_hash(original)]))
        self.assertFalse(is_duplicate(image_hash(make_jpeg(2)), [image_hash(original)]))

    def test_hash_of_broken_file_is_none(self):
        broken = self.tmp / "broken.jpg"
        broken.write_bytes(b"not an image")
        self.assertIsNone(image_hash(broken))
        self.assertIsNone(image_hash(b"\x00\x01"))

    def test_wall_jpeg_downscales_and_converts(self):
        path = self.tmp / "big.png"
        Image.new("RGBA", (4000, 1000), (255, 0, 0, 128)).save(path)
        with Image.open(io.BytesIO(wall_jpeg(path))) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (2560, 640))
            self.assertEqual(image.mode, "RGB")

    def test_story_jpeg_is_vertical_full_hd(self):
        path = self.tmp / "wide.jpg"
        path.write_bytes(make_jpeg(3, size=(800, 400)))
        with Image.open(io.BytesIO(story_jpeg(path))) as image:
            self.assertEqual(image.size, (1080, 1920))

    def test_list_images_filters_and_orders_by_time(self):
        late, early = self.tmp / "b.jpg", self.tmp / "a.png"
        late.write_bytes(b"1")
        early.write_bytes(b"1")
        (self.tmp / "notes.txt").write_text("x")
        (self.tmp / "sub").mkdir()
        os.utime(early, (100, 100))
        os.utime(late, (200, 200))
        self.assertEqual(list_images(self.tmp), [early, late])
        self.assertEqual(list_images(self.tmp / "missing"), [])


class ExtractTests(unittest.TestCase):
    def test_extract_photos_skips_ads_reposts_and_pinned(self):
        posts = [
            {"attachments": [photo(-1, 1), {"type": "video"}]},
            {"marked_as_ads": 1, "attachments": [photo(-1, 2)]},
            {"is_pinned": 1, "attachments": [photo(-1, 3)]},
            {"copy_history": [{"attachments": [photo(-2, 4)]}]},
            {"attachments": [{"type": "photo", "photo": {"owner_id": -1, "id": 5, "sizes": []}}]},
            {"text": "Розыгрыш! Подпишись", "attachments": [photo(-1, 6)]},
        ]
        self.assertEqual([key for key, _url in extract_photos(posts)], ["-1_1"])

    def test_hidden_ads_are_recognised(self):
        ads = [
            "Реклама. ООО «Цветы», ИНН 7700000000. erid: 2VtzqvXyz",
            "мой новый канал https://t.me/sentakunokurushimi приходите в гости",
            "Заходите на flowers-shop.ru",
            "Привет! [club231927277|арт-маркет «Почта»] пройдет уже 27 сентября",
            "🐾 Настало время РОЗЫГРЫША!!! 🐾",
            "Броши ручной работы в наличии, цена 900₽",
            "Подписывайтесь на наш паблик",
            "Миниатюрные броши и сумочки", "Колечки с настоящими лесными грибочками", "#хендмейд",
            "handmade с любовью", "Изделия ручной работы", "сделано вручную", "Добро пожаловать в мою мастерскую",
            "Создаю уникальные украшения для волос", "Всё сделано своими руками",
        ]
        for text in ads:
            with self.subTest(text=text):
                self.assertTrue(is_ad({"text": text}))
        self.assertTrue(is_ad({"attachments": [{"type": "link", "link": {}}]}))
        self.assertTrue(is_ad({"copy_history": [{"text": "просто цитата"}]}))
        for text in ["", "о чем нам говорят цветы? 🌸", "Автор: [club233752635|Tony ReVision]",
                     "Рекордно тёплая осень", "Рецепт пирога с тыквой",
                     "в ноябрьских сценах Часослова", "постельное бельё изготавливались на заказ",
                     "Это были не просто украшения.", "птица, которую Диккенс кормил своими руками"]:
            with self.subTest(text=text):
                self.assertFalse(is_ad({"text": text}))

    def test_best_photo_url_prefers_original(self):
        self.assertEqual(best_photo_url({"orig_photo": {"url": "orig"}, "sizes": [{"width": 1, "height": 1, "url": "s"}]}), "orig")
        self.assertEqual(best_photo_url({"sizes": [{"width": 1, "height": 1, "url": "s"},
                                                   {"width": 5, "height": 5, "url": "l"}]}), "l")
        self.assertEqual(best_photo_url({}), "")

    def test_interleave(self):
        self.assertEqual(interleave([[1, 2, 3], [4], [5, 6]]), [1, 4, 5, 2, 6, 3])
        self.assertEqual(interleave([]), [])

    def test_popularity_is_likes_per_view(self):
        self.assertEqual(popularity({"likes": {"count": 5}, "views": {"count": 50}}), 0.1)
        self.assertEqual(popularity({"likes": {"count": 5}}), 0.0)


class CollectorTests(AsyncTempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.community = self.store.add_community(1, "Моя")
        self.sources = [Source(1, 10, "A"), Source(1, 20, "B")]
        self.folder = self.tmp / "images"
        self.vk = FakeVk()

    async def test_collects_new_pictures_and_never_repeats_them(self):
        walls = {
            -10: {"items": [{"attachments": [photo(-10, i)]} for i in range(1, 4)]},
            -20: {"items": [{"attachments": [photo(-20, i)]} for i in range(1, 3)]},
        }
        self.vk.handlers["wall.get"] = lambda owner_id, count, offset: walls[owner_id] if offset == 0 else {"items": []}
        seed = 100
        for owner, ids in ((10, range(1, 4)), (20, range(1, 3))):
            for i in ids:
                seed += 1
                self.vk.files[f"https://img/-{owner}_{i}"] = make_jpeg(seed)

        stats = await Collector(self.vk, self.store).collect(self.community, self.sources, self.folder, need=4)
        self.assertEqual(stats.downloaded, 4)
        names = sorted(p.stem for p in list_images(self.folder))
        self.assertEqual(names, ["-10_1", "-10_2", "-20_1", "-20_2"])
        self.assertEqual(len(self.store.seen_keys(1)), 4)

        again = await Collector(self.vk, self.store).collect(self.community, self.sources, self.folder, need=4)
        self.assertEqual(again.downloaded, 1)
        self.assertIn("-10_3", [p.stem for p in list_images(self.folder)])

    async def test_most_liked_pictures_go_first_and_sources_alternate(self):
        def post(owner, i, likes, views):
            return {"attachments": [photo(owner, i)], "likes": {"count": likes}, "views": {"count": views}}

        walls = {
            -10: {"items": [post(-10, 1, 1, 100), post(-10, 2, 30, 100), post(-10, 3, 10, 100)]},
            -20: {"items": [post(-20, 1, 5, 1000), {"attachments": [photo(-20, 2)]}, post(-20, 3, 90, 1000)]},
        }
        self.vk.handlers["wall.get"] = lambda owner_id, count, offset: walls[owner_id] if offset == 0 else {"items": []}
        self.store.mark_seen(1, ["-10_2"])
        candidates = await Collector(self.vk, self.store)._candidates(self.sources, {"-10_2"}, 2, CollectStats())
        self.assertEqual([key for key, _url in candidates], ["-10_3", "-20_3", "-10_1", "-20_1", "-20_2"])

    async def test_duplicates_broken_and_failed_sources(self):
        def wall(owner_id, count, offset):
            if owner_id == -20:
                raise VkError(15, "Access denied")
            return {"items": [{"attachments": [photo(-10, i)]} for i in range(1, 5)]} if offset == 0 else {"items": []}

        self.vk.handlers["wall.get"] = wall
        self.vk.files["https://img/-10_1"] = make_jpeg(1)
        self.vk.files["https://img/-10_2"] = make_jpeg(1)
        self.vk.files["https://img/-10_3"] = b"garbage"
        self.store.add_posted_hashes(1, [image_hash(make_jpeg(9))])
        self.vk.files["https://img/-10_4"] = make_jpeg(9)

        stats = await Collector(self.vk, self.store).collect(self.community, self.sources, self.folder, need=5)
        self.assertEqual((stats.downloaded, stats.duplicates, stats.broken), (1, 2, 1))
        self.assertEqual(stats.failed_sources, ["B"])
        self.assertEqual(self.store.seen_keys(1), {"-10_1", "-10_2", "-10_3", "-10_4"})

    async def test_missing_download_is_retried_later(self):
        self.vk.handlers["wall.get"] = lambda owner_id, count, offset: (
            {"items": [{"attachments": [photo(owner_id, 1)]}]} if offset == 0 else {"items": []})
        stats = await Collector(self.vk, self.store).collect(self.community, self.sources[:1], self.folder, need=1)
        self.assertEqual(stats.broken, 1)
        self.assertEqual(self.store.seen_keys(1), set())

    async def test_existing_folder_pictures_count_as_known(self):
        write_images(self.folder, 1, start=7)
        self.vk.handlers["wall.get"] = lambda owner_id, count, offset: (
            {"items": [{"attachments": [photo(-10, 1)]}]} if offset == 0 else {"items": []})
        self.vk.files["https://img/-10_1"] = make_jpeg(7)
        stats = await Collector(self.vk, self.store).collect(self.community, self.sources[:1], self.folder, need=1)
        self.assertEqual(stats.duplicates, 1)

    async def test_pages_deeper_when_first_page_is_already_used(self):
        first = [{"attachments": [photo(-10, i)]} for i in range(100)]
        second = [{"attachments": [photo(-10, 500)]}]
        self.store.mark_seen(1, [f"-10_{i}" for i in range(100)])
        self.vk.handlers["wall.get"] = lambda owner_id, count, offset: {"items": first if offset == 0 else second}
        self.vk.files["https://img/-10_500"] = make_jpeg(55)
        stats = await Collector(self.vk, self.store).collect(self.community, self.sources[:1], self.folder, need=1)
        self.assertEqual(stats.downloaded, 1)
        self.assertEqual([p["offset"] for p in self.vk.params("wall.get")], [0, 100])

    async def test_nothing_to_do(self):
        stats = await Collector(self.vk, self.store).collect(self.community, self.sources, self.folder, need=0)
        self.assertEqual(stats.downloaded, 0)
        stats = await Collector(self.vk, self.store).collect(self.community, [], self.folder, need=3)
        self.assertEqual(self.vk.calls, [])

    async def test_auth_and_flood_errors_stop_collection(self):
        for exc in (VkAuthError(5, "x"), VkFloodError(9, "x")):
            self.vk.handlers["wall.get"] = exc
            with self.assertRaises(type(exc)):
                await Collector(self.vk, self.store).collect(self.community, self.sources, self.folder, need=1)


class ScheduleTests(unittest.TestCase):
    def test_schedule_base_never_in_the_past(self):
        self.assertEqual(schedule_base(None, now=1000), 1000)
        self.assertEqual(schedule_base(500, now=1000), 1000)
        self.assertEqual(schedule_base(5000, now=1000), 5000)

    def test_next_publish_date_respects_lead(self):
        self.assertEqual(next_publish_date(1000, 10, now=1000), 1000 + MIN_PUBLISH_LEAD_SEC)
        self.assertEqual(next_publish_date(10_000, 28_800, now=1000), 38_800)

    def test_slot_step(self):
        self.assertEqual(slot_step(3), 28_800)
        self.assertEqual(slot_step(0), 86_400)

    def test_posts_needed(self):
        community = Community(vk_id=1, posts_per_day=3, queue_days=3)
        self.assertEqual(posts_needed(community, 2), 7)
        self.assertEqual(posts_needed(community, 20), 0)
        self.assertEqual(posts_needed(Community(vk_id=1, posts_per_day=3, queue_days=90), 0), 270)
        self.assertEqual(posts_needed(Community(vk_id=1, posts_per_day=24, queue_days=120), 0), MAX_POSTPONED)
        weekdays = Community(vk_id=1, posts_per_day=3, queue_days=70, post_days="0,1,2,3,4")
        self.assertEqual(posts_needed(weekdays, 0), 150)

    def test_slots_at_chosen_hours_and_days(self):
        moscow = ZoneInfo("Europe/Moscow")

        def at(day, hour, minute=0):
            return int(datetime(2026, 11, day, hour, minute, tzinfo=moscow).timestamp())

        # 9 November 2026 is a Monday.
        community = Community(vk_id=1, post_hours="9,14,20", post_days="0,2")
        now = at(9, 8)
        self.assertEqual(next_slot(community, now, "Europe/Moscow", now=now), at(9, 9))
        self.assertEqual(next_slot(community, at(9, 9), "Europe/Moscow", now=now), at(9, 14))
        self.assertEqual(next_slot(community, at(9, 20), "Europe/Moscow", now=now), at(11, 9))
        self.assertEqual(next_slot(community, at(11, 20), "Europe/Moscow", now=now), at(16, 9))
        # Too close to now: VK needs a lead, the next hour is taken.
        self.assertEqual(next_slot(community, at(9, 8, 50), "Europe/Moscow", now=at(9, 8, 50)), at(9, 14))

    def test_even_slots_skip_days_off(self):
        moscow = ZoneInfo("Europe/Moscow")
        friday_evening = int(datetime(2026, 11, 13, 20, tzinfo=moscow).timestamp())
        community = Community(vk_id=1, posts_per_day=3, post_days="0,1,2,3,4")
        slot = next_slot(community, friday_evening, "Europe/Moscow", now=friday_evening - 86400)
        self.assertEqual(datetime.fromtimestamp(slot, moscow).weekday(), 0)
        everyday = Community(vk_id=1, posts_per_day=3)
        self.assertEqual(next_slot(everyday, friday_evening, "Europe/Moscow", now=0), friday_evening + 28_800)


class MessageTests(TempDirTestCase):
    def test_load_quotes_dedupes_and_strips(self):
        path = self.tmp / "q.txt"
        path.write_text('"Раз"\n  Раз \n\nДва\nчаще смотри наверх\\n\\nтам звёзды\n', encoding="utf-8")
        self.assertEqual(load_quotes(path), ["Два", "Раз", "чаще смотри наверх\n\nтам звёзды"])
        self.assertEqual(load_quotes(self.tmp / "missing.txt"), [])

    def test_build_message_with_chat_reminder(self):
        community = Community(vk_id=1, hashtags="#tag", chat_link="https://vk.me/x", chat_every=3, chat_text="Заходите")
        self.assertEqual(build_message(community, "Цитата", 1), "Цитата\n\n#tag")
        self.assertEqual(build_message(community, "Цитата", 3), "Заходите\n\nhttps://vk.me/x\n\n#tag")
        community.chat_text = "Ссылка https://vk.me/x внутри"
        self.assertEqual(chat_message(community), "Ссылка https://vk.me/x внутри\n\n#tag")
        community.chat_link = ""
        self.assertEqual(build_message(community, "", 3), "#tag")
        self.assertEqual(build_message(Community(vk_id=1), "", 1), "")
        self.assertIn("чату", chat_message(Community(vk_id=1, chat_link="L")))


class PublisherTests(AsyncTempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.config.texts_file.write_text("Цитата\n", encoding="utf-8")
        self.community = self.store.add_community(1, "Моя")
        self.community = self.store.update_community(1, photos_per_post=2, posts_per_day=2, hashtags="#t")
        self.folder = self.tmp / "images"
        self.vk = FakeVk({
            "wall.get": {"count": 0, "items": []},
            "photos.getWallUploadServer": {"upload_url": "https://upload"},
            "photos.saveWallPhoto": counter(),
            "wall.post": {"post_id": 1},
        })
        self.publisher = Publisher(self.vk, self.store, self.config.texts_file, sleep=no_sleep)

    async def test_publishes_full_posts_and_deletes_files_only_after_post(self):
        write_images(self.folder, 5)
        self.community = self.store.update_community(1, use_quotes=True)
        stats = await self.publisher.publish(self.community, self.folder, max_posts=10)
        self.assertEqual(stats.posts_created, 2)
        posts = self.vk.params("wall.post")
        self.assertEqual(len(posts[0]["attachments"]), 2)
        self.assertEqual(posts[0]["from_group"], 1)
        self.assertEqual(posts[0]["message"], "Цитата\n\n#t")
        self.assertEqual(posts[1]["publish_date"] - posts[0]["publish_date"], 43_200)
        self.assertGreaterEqual(posts[0]["publish_date"], int(time.time()) + MIN_PUBLISH_LEAD_SEC)
        self.assertEqual(len(list_images(self.folder)), 1)
        self.assertEqual(self.store.get_community(1).posts_made, 2)
        self.assertEqual(len(self.store.posted_hashes(1)), 4)
        self.assertEqual(stats.last_date, posts[1]["publish_date"])

    async def test_posts_without_quotes_get_only_hashtags(self):
        write_images(self.folder, 2)
        self.assertFalse(self.community.use_quotes)
        await self.publisher.publish(self.community, self.folder, max_posts=1)
        self.assertEqual(self.vk.params("wall.post")[0]["message"], "#t")

    async def test_continues_after_last_postponed_post(self):
        later = int(time.time()) + 10 * 86400
        self.vk.handlers["wall.get"] = {"count": 1, "items": [{"date": later}]}
        write_images(self.folder, 2)
        await self.publisher.publish(self.community, self.folder, max_posts=1)
        self.assertEqual(self.vk.params("wall.post")[0]["publish_date"], later + 43_200)

    async def test_respects_max_posts_and_skips_when_not_enough_files(self):
        write_images(self.folder, 6)
        stats = await self.publisher.publish(self.community, self.folder, max_posts=1)
        self.assertEqual(stats.posts_created, 1)
        self.assertEqual(len(list_images(self.folder)), 4)
        empty = self.tmp / "empty"
        write_images(empty, 1)
        self.assertEqual((await self.publisher.publish(self.community, empty, max_posts=5)).posts_created, 0)
        self.assertEqual((await self.publisher.publish(self.community, self.folder, max_posts=0)).posts_created, 0)

    async def test_retries_upload_on_fresh_url(self):
        write_images(self.folder, 2)
        self.vk.upload_answers = [{}, {"server": 1, "photo": "[]", "hash": "h"}]
        stats = await self.publisher.publish(self.community, self.folder, max_posts=1)
        self.assertEqual(stats.posts_created, 1)
        self.assertEqual(len(self.vk.uploads), 4)
        self.assertEqual(self.vk.methods().count("photos.getWallUploadServer"), 3)

    async def test_file_rejected_by_vk_is_moved_aside(self):
        write_images(self.folder, 3)
        bad = list_images(self.folder)[0]
        self.vk.upload_failures[f"{bad.stem}.jpg"] = publisher_module.UPLOAD_ATTEMPTS
        stats = await self.publisher.publish(self.community, self.folder, max_posts=1)
        self.assertEqual((stats.posts_created, stats.upload_failed), (1, 1))
        self.assertEqual([p.name for p in (self.folder / "rejected").iterdir()], [bad.name])
        self.assertEqual(list_images(self.folder), [])

    async def test_vk_outage_keeps_files(self):
        write_images(self.folder, 4)
        self.vk.upload_answers = [{}] * 100
        stats = await self.publisher.publish(self.community, self.folder, max_posts=2)
        self.assertEqual((stats.posts_created, stats.upload_failed), (0, 4))
        self.assertEqual(len(list_images(self.folder)), 4)
        self.assertFalse((self.folder / "rejected").exists())

    async def test_many_refusals_stop_run_and_keep_files(self):
        self.store.update_community(1, photos_per_post=6)
        write_images(self.folder, 60)
        for path in list_images(self.folder)[:10]:
            self.vk.upload_failures[f"{path.stem}.jpg"] = publisher_module.UPLOAD_ATTEMPTS
        stats = await self.publisher.publish(self.store.get_community(1), self.folder, max_posts=10)
        self.assertTrue(stats.throttled)
        # One wave of 25 files: 15 uploaded make 2 posts, the 10 refused stay for the next run.
        self.assertEqual((stats.posts_created, stats.upload_failed), (2, 10))
        self.assertFalse((self.folder / "rejected").exists())
        self.assertEqual(len(list_images(self.folder)), 48)

    async def test_waves_are_paced(self):
        pauses = []

        async def sleep(seconds: float) -> None:
            pauses.append(seconds)

        self.store.update_community(1, photos_per_post=6)
        write_images(self.folder, 60)
        publisher = Publisher(self.vk, self.store, self.config.texts_file, sleep=sleep)
        stats = await publisher.publish(self.store.get_community(1), self.folder, max_posts=10)
        self.assertEqual(stats.posts_created, 10)
        self.assertEqual(len(pauses), 2)
        self.assertTrue(all(0 < p <= publisher_module.MIN_WAVE_SEC for p in pauses))

    async def test_failed_post_keeps_files_and_stops_after_limit(self):
        self.store.update_community(1, photos_per_post=10)
        write_images(self.folder, 80)
        self.vk.handlers["wall.post"] = VkError(100, "invalid publish_date")
        stats = await self.publisher.publish(self.store.get_community(1), self.folder, max_posts=8)
        self.assertEqual((stats.posts_created, stats.publish_failed), (0, 4))
        self.assertEqual(self.vk.methods().count("wall.post"), 4)
        self.assertEqual(len(list_images(self.folder)), 80)
        self.assertEqual(self.store.get_community(1).posts_made, 0)

    async def test_many_posts_use_few_requests(self):
        self.store.update_community(1, photos_per_post=6)
        write_images(self.folder, 60)
        stats = await self.publisher.publish(self.store.get_community(1), self.folder, max_posts=10)
        self.assertEqual(stats.posts_created, 10)
        self.assertEqual(len(self.vk.uploads), 60)
        # 4 posts per wave (24 photos): 3 waves, each with one execute for photos and one for posts.
        self.assertEqual(self.vk.executes, 6)
        self.assertEqual(self.vk.methods().count("photos.getWallUploadServer"), 1)
        dates = [p["publish_date"] for p in self.vk.params("wall.post")]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(len(set(dates)), 10)

    async def test_queue_reads_all_pages(self):
        def wall_get(owner_id, filter, count, offset=0):
            return {"count": 250, "items": [{"date": 1000 + offset + i} for i in range(min(count, 250 - offset))]}

        self.vk.handlers["wall.get"] = wall_get
        queue = await self.publisher.queue(1)
        self.assertEqual((queue.count, queue.last_date), (250, 1249))
        self.assertEqual(self.vk.executes, 1)
        self.assertEqual([p.get("offset", 0) for p in self.vk.params("wall.get")], [0, 100, 200])

    async def test_vk_denial_stops_run_and_keeps_files(self):
        self.store.update_community(1, photos_per_post=10)
        write_images(self.folder, 60)
        answers = iter([{"post_id": 1}])
        self.vk.handlers["wall.post"] = lambda **_p: next(answers, VkError(214, "Access to adding post denied"))
        stats = await self.publisher.publish(self.store.get_community(1), self.folder, max_posts=5)
        self.assertEqual((stats.posts_created, stats.publish_failed, stats.denied), (1, 0, True))
        # One wave of two posts, then the run stops instead of trying the remaining files.
        self.assertEqual(self.vk.methods().count("wall.post"), 2)
        self.assertEqual(len(list_images(self.folder)), 50)

    async def test_save_photo_error_rejects_only_that_file(self):
        write_images(self.folder, 3)
        calls = {"n": 0}

        def save(**_params):
            calls["n"] += 1
            if calls["n"] == 1:
                return VkError(100, "bad photo")
            return [{"owner_id": -1, "id": calls["n"]}]

        self.vk.handlers["photos.saveWallPhoto"] = save
        stats = await self.publisher.publish(self.community, self.folder, max_posts=1)
        self.assertEqual((stats.posts_created, stats.upload_failed), (1, 1))
        self.assertEqual(len(list((self.folder / "rejected").iterdir())), 1)

    async def test_unreadable_file_is_skipped(self):
        write_images(self.folder, 2)
        broken = self.folder / "zzz.jpg"
        broken.write_bytes(b"broken")
        os.utime(broken, (1, 1))
        stats = await self.publisher.publish(self.community, self.folder, max_posts=1)
        self.assertEqual(stats.posts_created, 1)

    async def test_auth_and_flood_errors_bubble_up_and_keep_files(self):
        write_images(self.folder, 2)
        for exc in (VkFloodError(9, "flood"), VkAuthError(5, "auth")):
            self.vk.handlers["wall.post"] = exc
            with self.assertRaises(type(exc)):
                await self.publisher.publish(self.community, self.folder, max_posts=1)
            self.assertEqual(len(list_images(self.folder)), 2)
        self.vk.handlers["photos.getWallUploadServer"] = VkAuthError(5, "auth")
        with self.assertRaises(VkAuthError):
            await self.publisher.publish(self.community, self.folder, max_posts=1, queue=QueueInfo())

    async def test_queue_info(self):
        self.vk.handlers["wall.get"] = {"count": 3, "items": [{"date": 5}, {"date": 9}, {}]}
        self.assertEqual(await self.publisher.queue(1), QueueInfo(count=3, last_date=9))
        self.vk.handlers["wall.get"] = {"items": []}
        self.assertEqual(await self.publisher.queue(1), QueueInfo(count=0, last_date=None))

    async def test_clear_deletes_until_empty(self):
        pages = [{"items": [{"id": 1}, {"id": 2}]}, {"items": [{"id": 3}]}, {"items": []}]
        self.vk.handlers["wall.get"] = lambda **params: pages.pop(0)
        self.vk.handlers["wall.delete"] = lambda owner_id, post_id: VkError(15, "no") if post_id == 2 else 1
        self.assertEqual(await self.publisher.clear(1), 2)
        self.assertEqual(self.vk.params("wall.get")[0]["filter"], "postponed")

    async def test_clear_stops_when_nothing_can_be_deleted(self):
        self.vk.handlers["wall.get"] = {"items": [{"id": 1}]}
        self.vk.handlers["wall.delete"] = VkError(15, "no")
        self.assertEqual(await self.publisher.clear(1, published=True), 0)
        self.assertEqual(self.vk.params("wall.get")[0]["filter"], "owner")
        self.vk.handlers["wall.delete"] = VkFloodError(9, "flood")
        with self.assertRaises(VkFloodError):
            await self.publisher.clear(1)


class StoryTests(AsyncTempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.folder = self.tmp / "images"
        self.vk = FakeVk({
            "stories.getPhotoUploadServer": {"upload_url": "https://story"},
            "stories.save": {"count": 1},
        })
        self.stories = StoryPublisher(self.vk, self.store)

    async def test_publishes_requested_number_and_removes_files(self):
        write_images(self.folder, 3)
        self.assertEqual(await self.stories.publish(1, self.folder, 2), 2)
        self.assertEqual(len(list_images(self.folder)), 1)
        self.assertEqual(self.vk.uploads[0][1], "file")
        self.assertEqual(len(self.store.posted_hashes(1)), 2)

    async def test_failures_and_broken_files(self):
        write_images(self.folder, 4)
        broken = self.folder / "a_broken.jpg"
        broken.write_bytes(b"x")
        os.utime(broken, (1, 1))
        self.vk.upload_answers = [{}] * 3
        self.assertEqual(await self.stories.publish(1, self.folder, 2), 0)
        self.assertFalse(broken.exists())
        self.assertEqual(len(list_images(self.folder)), 4)

    async def test_flood_is_raised(self):
        write_images(self.folder, 1)
        self.vk.handlers["stories.save"] = VkFloodError(9, "flood")
        with self.assertRaises(VkFloodError):
            await self.stories.publish(1, self.folder, 1)


if __name__ == "__main__":
    unittest.main()
