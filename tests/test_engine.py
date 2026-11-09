import asyncio
import time
import unittest
from collections import Counter

from api.vk import VkAuthError, VkError, VkFloodError
from services.account import AccountService
from services.analytics import (
    Analytics, Comparison, MemberStats, PostStats, Recommendation, Report, comparison_rows, member_stats,
    post_stats, recommend, render_charts, render_comparison, slot_scores,
)
from services.autofill import AutoFiller, needs_refill
from services import engine as engine_module
from services.engine import BusyError, Engine, FillResult, UserError, _jsonable
from utils.store import Community

from tests.fakes import AsyncTempDirTestCase, FakeVk, TempDirTestCase, counter, make_jpeg, photo

NOW = 1_800_000_000


async def no_sleep(_seconds: float) -> None:
    return None


def group(vk_id=5, name="Цветы", screen="flowers", is_admin=1, level=3, closed=0, member=0, **extra):
    data = {"id": vk_id, "name": name, "screen_name": screen, "is_admin": is_admin,
            "admin_level": level, "is_closed": closed, "is_member": member}
    data.update(extra)
    return {"groups": [data]}


class AccountTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.vk = FakeVk()
        self.account = AccountService(self.vk, sleep=no_sleep)

    async def test_name(self):
        self.vk.handlers["users.get"] = [{"first_name": "Иван", "last_name": "Петров"}]
        self.assertEqual(await self.account.name(), "Иван Петров")

    async def test_repost_skips_ads_and_failures(self):
        self.vk.handlers.update({
            "wall.get": {"items": [{"id": 1, "is_pinned": 1}, {"id": 2}, {"id": 3, "marked_as_ads": 1}, {"id": 4}, {"id": 5}]},
            "wall.repost": lambda object: VkError(1, "x") if object.endswith("_4") else {"success": 1},
        })
        self.assertEqual(await self.account.repost(7, 2), 2)
        self.assertEqual([p["object"] for p in self.vk.params("wall.repost")], ["wall-7_2", "wall-7_4", "wall-7_5"])
        self.vk.handlers["wall.repost"] = VkAuthError(5, "x")
        with self.assertRaises(VkAuthError):
            await self.account.repost(7, 1)


def post(date, views, likes=0, reposts=0, comments=0, **extra):
    data = {"date": date, "views": {"count": views}, "likes": {"count": likes},
            "reposts": {"count": reposts}, "comments": {"count": comments}}
    data.update(extra)
    return data


def at_hour(day, hour):
    """Timestamp `day` days before NOW at `hour`:00 UTC (NOW is a Friday)."""
    midnight = NOW - NOW % 86400
    return midnight - day * 86400 + hour * 3600


class AnalyticsTests(TempDirTestCase):
    def test_member_stats_separates_dead_accounts_and_counts_online_hours(self):
        stats = member_stats([
            {"sex": 1, "last_seen": {"time": NOW - 100}, "bdate": "1.1.2000"},
            {"sex": 2, "last_seen": {"time": at_hour(3, 20)}, "bdate": "5.5"},
            {"sex": 1, "last_seen": {"time": at_hour(10, 20)}, "bdate": "1.1.2015"},
            {"bdate": "1.1.1950", "last_seen": {"time": NOW - 40 * 86400}},
            {"deactivated": "deleted", "sex": 1},
        ], total=10, now=NOW)
        self.assertEqual((stats.sampled, stats.dead, stats.alive), (5, 1, 4))
        self.assertEqual((stats.female, stats.male), (2, 1))
        self.assertEqual((stats.active_week, stats.active_month, stats.inactive), (2, 1, 1))
        self.assertEqual(stats.ages, Counter({"25–34": 1, "не указан": 1, "до 18": 1, "45+": 1}))
        # Seen a minute ago is too close to the analysis, 40 days ago is too old.
        self.assertEqual(stats.online_hours[20], 2)
        self.assertEqual(sum(stats.online_hours), 2)
        self.assertEqual(stats.online_share[20], 1.0)
        self.assertEqual(MemberStats().online_share, [])

    def test_post_stats_use_medians_and_skip_fresh_pinned_and_ads(self):
        posts = [
            post(NOW - 2 * 86400, 100, likes=10, reposts=1),
            post(NOW - 3 * 86400, 50, likes=4),
            post(NOW - 4 * 86400, 100_000, likes=900, comments=5),
            post(NOW - 3600, 1, likes=1),
            post(NOW - 5 * 86400, 7, is_pinned=1),
            post(NOW - 6 * 86400, 7, marked_as_ads=1),
            post(NOW - 60 * 86400, 9999),
        ]
        stats = post_stats(posts, now=NOW)
        self.assertEqual((stats.count, stats.median_views, stats.median_likes, stats.median_reposts), (3, 100, 10, 0))
        self.assertEqual(stats.per_day, 0.1)
        self.assertEqual(stats.engagement, 8.0)
        self.assertEqual([s[0] for s in stats.series], [NOW - 4 * 86400, NOW - 3 * 86400, NOW - 2 * 86400])
        self.assertEqual(post_stats([], now=NOW).count, 0)

    def test_slot_scores_compare_posts_with_their_neighbours(self):
        posts = []
        for day in range(1, 7):
            posts += [post(at_hour(day, 9), 100), post(at_hour(day, 18), 300)]
        hours, days = slot_scores(sorted(posts, key=lambda p: p["date"]), "UTC")
        self.assertLess(hours[9], 1)
        self.assertGreater(hours[18], 1)
        self.assertEqual(days, {})
        # One viral post is pulled towards "as usual".
        viral = [post(at_hour(day, 12), 100) for day in range(13, 2, -1)] + [post(at_hour(day, 23), 1000) for day in (2, 1, 0)]
        hours, _days = slot_scores(viral, "UTC")
        self.assertLess(hours[23], 5)
        self.assertEqual(hours[12], 1.0)

    def test_recommend_hours_from_audience_and_posts(self):
        members = MemberStats(online_hours=[1] * 24)
        members.online_hours[20] = 30
        members.online_hours[19] = 25
        members.online_hours[3] = 100
        members.online_hours[10] = 10
        rec = recommend(members, PostStats(), 2)
        # Night hours are skipped and picked hours are spread out.
        self.assertEqual((rec.hours, rec.days, rec.by_posts), ([10, 20], list(range(7)), False))
        self.assertTrue(rec.every_day)
        boosted = recommend(members, PostStats(hour_scores={19: 2.0, 10: 1.0, 15: 1.0}), 1)
        self.assertEqual(boosted.hours, [19])
        self.assertIsNone(recommend(MemberStats(), PostStats(), 3))
        self.assertIsNone(recommend(MemberStats(), PostStats(hour_scores={9: 1.0}), 2))

    def test_recommend_drops_weak_days_but_keeps_five(self):
        members = MemberStats(online_hours=[1] * 24)
        scores = {0: 0.5, 1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 0.6, 6: 0.7}
        rec = recommend(members, PostStats(day_scores=scores), 1)
        self.assertEqual(rec.days, [1, 2, 3, 4, 6])
        self.assertFalse(rec.every_day)
        partial = recommend(members, PostStats(day_scores={0: 0.1, 1: 1.0}), 1)
        self.assertEqual(partial.days, list(range(7)))

    def test_render_charts(self):
        members = MemberStats(total=3, sampled=4, dead=1, female=2, male=1, active_week=1, inactive=2,
                              ages=Counter({"18–24": 2, "не указан": 1}), online_hours=[1] * 24)
        posts = PostStats(count=2, series=[(NOW - 86400, 10, 1), (NOW, 20, 2)], hour_scores={9: 1.5})
        report = Report(Community(vk_id=1), members, posts, Recommendation([9], list(range(7)), True))
        charts = render_charts(report, self.tmp / "charts")
        self.assertEqual([p.name for p in charts], ["audience.png", "ages.png", "posts.png", "hours.png"])
        self.assertTrue(all(p.stat().st_size > 1000 for p in charts))
        only_posts = render_charts(Report(Community(vk_id=2), MemberStats(), PostStats(hour_scores={9: 1.5})),
                                   self.tmp / "charts")
        self.assertEqual([p.name for p in only_posts], ["hours.png"])
        self.assertEqual(render_charts(Report(Community(vk_id=1), MemberStats(), PostStats()), self.tmp / "e"), [])

    def test_comparison_rows_and_charts(self):
        def report(vk_id, female, week, views):
            members = MemberStats(total=100, sampled=100, female=female, male=100 - female, active_week=week,
                                  online_hours=[1] * 24)
            return Report(Community(vk_id=vk_id, name=f"g{vk_id}"), members,
                          PostStats(count=30, per_day=1.0, median_views=views, engagement=2.5))

        comparison = Comparison(report(1, 80, 50, 20), report(2, 60, 70, 40))
        self.assertEqual(comparison_rows(comparison), [
            ("Заходили за неделю, %", 50, 70), ("Удалённые и заблокированные, %", 0, 0), ("Женщин, %", 80, 60),
            ("Охват поста, % участников", 20.0, 40.0), ("Вовлечённость, %", 2.5, 2.5), ("Постов в день", 1.0, 1.0),
        ])
        charts = render_comparison(comparison, self.tmp / "cmp")
        self.assertEqual([p.name for p in charts], ["compare.png", "compare_hours.png"])
        empty = Comparison(Report(Community(vk_id=1), MemberStats(), PostStats()),
                           Report(Community(vk_id=2), MemberStats(), PostStats()))
        self.assertEqual((comparison_rows(empty), render_comparison(empty, self.tmp / "e")), ([], []))


class AnalyticsServiceTests(AsyncTempDirTestCase):
    async def test_report_pages_members_and_posts(self):
        vk = FakeVk({
            "groups.getById": {"groups": [{"id": 1, "members_count": 1001}]},
            "groups.getMembers": lambda offset, **_p: {"count": 1001, "items": [{"sex": 1}] * (1000 if offset == 0 else 1)},
            "wall.get": lambda offset, **_p: {"items": [post(NOW - 2 * 86400, 5)] * (100 if offset < 200 else 3)},
        })
        report = await Analytics(vk, "UTC").report(Community(vk_id=1), self.tmp / "charts", now=NOW)
        self.assertEqual((report.members.total, report.members.sampled, report.members.female), (1001, 1001, 1001))
        self.assertEqual(report.posts.count, 203)
        self.assertEqual([p["offset"] for p in vk.params("groups.getMembers")], [0, 1000])
        self.assertEqual(len(vk.params("wall.get")), 3)
        self.assertTrue(report.charts)

    async def test_big_group_is_sampled_over_the_whole_list(self):
        vk = FakeVk({
            "groups.getById": [{"id": 1, "members_count": 100_000}],
            "groups.getMembers": {"items": [{"sex": 2}] * 1000},
            "wall.get": {"items": []},
        })
        report = await Analytics(vk, "UTC").report(Community(vk_id=1), self.tmp / "charts", now=NOW)
        offsets = [p["offset"] for p in vk.params("groups.getMembers")]
        self.assertEqual((len(offsets), offsets[0], offsets[-1]), (20, 0, 99_000))
        self.assertEqual(report.members.sampled, 20_000)

    async def test_hidden_members_and_wall(self):
        vk = FakeVk({
            "groups.getById": [{"id": 1, "members_count": 50}],
            "groups.getMembers": VkError(15, "hidden"),
            "wall.get": VkError(15, "closed"),
        })
        report = await Analytics(vk, "UTC").report(Community(vk_id=1), self.tmp / "charts", now=NOW)
        self.assertTrue(report.members.hidden and report.posts.hidden)
        self.assertIsNone(report.recommendation)
        vk.handlers["wall.get"] = VkAuthError(5, "expired")
        with self.assertRaises(VkAuthError):
            await Analytics(vk, "UTC").report(Community(vk_id=1), self.tmp / "charts", now=NOW)
        vk.handlers["groups.getMembers"] = VkFloodError(9, "flood")
        with self.assertRaises(VkFloodError):
            await Analytics(vk, "UTC").report(Community(vk_id=1), self.tmp / "charts", now=NOW)


class EngineTests(AsyncTempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.vk = FakeVk({
            "groups.getById": group(),
            "wall.get": {"count": 0, "items": []},
            "photos.getWallUploadServer": {"upload_url": "u"},
            "photos.saveWallPhoto": counter(),
            "wall.post": {"post_id": 1},
        })
        self.engine = Engine(self.config, self.store, self.vk)
        self.engine.publisher.sleep = no_sleep

    async def test_add_community_requires_editor_rights(self):
        community = await self.engine.add_community("https://vk.com/flowers")
        self.assertEqual((community.vk_id, community.name, community.url), (5, "Цветы", "https://vk.com/flowers"))
        self.vk.handlers["groups.getById"] = group(level=1)
        with self.assertRaises(UserError) as ctx:
            await self.engine.add_community("https://vk.com/flowers")
        self.assertIn("прав редактора", str(ctx.exception))

    async def test_resolve_errors(self):
        with self.assertRaises(UserError):
            await self.engine.resolve("просто текст")
        self.vk.handlers["groups.getById"] = VkError(100, "invalid group id")
        with self.assertRaises(UserError):
            await self.engine.resolve("club1")
        self.vk.handlers["groups.getById"] = []
        with self.assertRaises(UserError):
            await self.engine.resolve("club1")
        self.vk.handlers["groups.getById"] = group(deactivated="banned")
        with self.assertRaises(UserError):
            await self.engine.resolve("club1")
        self.vk.handlers["groups.getById"] = VkError(15, "denied")
        with self.assertRaises(VkError):
            await self.engine.resolve("club1")
        self.vk.handlers["groups.getById"] = [{"id": 9, "name": "Старый ответ"}]
        info = await self.engine.resolve("club9")
        self.assertEqual((info.vk_id, info.url, info.can_post), (9, "https://vk.com/club9", False))

    async def test_add_sources_reports_every_problem(self):
        self.store.add_community(5, "Моя")
        answers = {
            "1": group(vk_id=1, name="Первая"),
            "2": group(vk_id=2, name="Закрытая", closed=1),
            "5": group(vk_id=5, name="Моя"),
            "3": VkError(100, "no"),
        }
        self.vk.handlers["groups.getById"] = lambda group_id, fields: answers[group_id]
        added, problems = await self.engine.add_sources(5, "club1 club2 club5 club3 club1")
        self.assertEqual(added, ["Первая"])
        self.assertEqual(len(problems), 3)
        added, problems = await self.engine.add_sources(5, "club1")
        self.assertEqual(added, [])
        self.assertIn("уже есть", problems[0])
        with self.assertRaises(UserError):
            await self.engine.add_sources(5, "ничего")

    async def test_fill_tops_up_queue(self):
        community = self.store.add_community(5, "Моя")
        self.store.update_community(5, photos_per_post=2, posts_per_day=1, queue_days=2)
        self.store.add_source(5, 10, "Источник")
        wall_posts = {"items": [{"attachments": [photo(-10, i)]} for i in range(10)]}
        queue = {"count": 0, "items": []}
        self.vk.handlers["wall.get"] = lambda owner_id, **p: queue if p.get("filter") == "postponed" else (
            wall_posts if p.get("offset") == 0 else {"items": []})
        for i in range(10):
            self.vk.files[f"https://img/-10_{i}"] = make_jpeg(300 + i)

        result = await self.engine.fill(5)
        self.assertEqual((result.queued_before, result.needed, result.published.posts_created), (0, 2, 2))
        self.assertEqual(result.collected.downloaded, 4)
        self.assertEqual(self.store.last_job(5)["status"], "ok")
        self.assertFalse(self.engine.busy)

        queue["count"] = 2
        result = await self.engine.fill(5)
        self.assertEqual(result.needed, 0)
        self.assertEqual(community.vk_id, 5)

    async def test_big_fill_is_split_into_runs(self):
        self.store.add_community(5, "Моя")
        self.store.add_source(5, 10)
        self.vk.handlers["wall.get"] = {"count": 0, "items": []}
        result = await self.engine.fill(5)
        self.assertEqual((result.needed, result.remaining), (engine_module.MAX_POSTS_PER_RUN, 270 - 50))
        self.assertEqual(self.engine.community(5).queue_days, 90)

    async def test_fill_without_sources_or_deleted_group(self):
        self.store.add_community(5)
        with self.assertRaises(UserError):
            await self.engine.fill(5)
        with self.assertRaises(UserError):
            await self.engine.fill(404)

    async def test_busy_engine_refuses_second_job(self):
        self.store.add_community(5, "Моя")
        release = asyncio.Event()

        async def slow():
            await release.wait()
            return 1

        first = asyncio.create_task(self.engine._job("x", "делает долгую работу", 5, slow))
        await asyncio.sleep(0)
        self.assertTrue(self.engine.busy)
        with self.assertRaises(BusyError) as ctx:
            await self.engine.repost(5, 1)
        self.assertIn("делает долгую работу", str(ctx.exception))
        release.set()
        self.assertEqual(await first, 1)

    async def test_broken_job_record_does_not_fail_the_action(self):
        def broken(*_args, **_kwargs):
            raise TypeError("not serializable")

        self.store.finish_job = broken

        async def work():
            return 7

        self.assertEqual(await self.engine._job("x", "работает", None, work), 7)

    async def test_failed_job_is_recorded(self):
        self.store.add_community(5, "Моя")
        self.vk.handlers["wall.get"] = VkAuthError(5, "expired")
        with self.assertRaises(VkAuthError):
            await self.engine.repost(5, 1)
        job = self.store.last_job()
        self.assertEqual((job["type"], job["status"]), ("repost", "error"))

    async def test_other_jobs_delegate(self):
        self.store.add_community(5, "Моя")
        self.store.add_source(5, 10)
        self.vk.handlers.update({
            "groups.getMembers": {"count": 0, "items": []},
            "wall.get": {"count": 0, "items": []},
            "stories.getPhotoUploadServer": {"upload_url": "s"},
            "stories.save": 1,
        })
        self.engine.account.sleep = no_sleep
        self.assertEqual(await self.engine.repost(5, 1), 0)
        self.assertEqual(await self.engine.clear(5), 0)
        self.assertEqual((await self.engine.analytics(5)).members.total, 0)
        self.assertEqual(await self.engine.stories(5, 1), 0)
        with self.assertRaises(UserError):
            await self.engine.repost(6, 1)

    async def test_analytics_of_any_group_and_compare(self):
        self.store.add_community(5, "Моя")
        self.vk.handlers.update({
            "groups.getById": lambda group_id, **_p: group(vk_id=int(str(group_id).replace("club", "")),
                                                           name=f"Группа {group_id}", is_admin=0, members_count=10),
            "groups.getMembers": {"items": [{"sex": 1}] * 10},
        })
        other = await self.engine.analytics_of("https://vk.com/club7")
        self.assertEqual((other.community.vk_id, other.members.total), (7, 10))
        job = self.store.last_job()
        self.assertEqual((job["type"], job["community_id"], job["status"]), ("analytics_other", None, "ok"))
        self.assertIs(self.engine.cached_report(7), other)

        mine = await self.engine.analytics_of("https://vk.com/club5")
        self.assertEqual((mine.community.title, self.store.last_job()["type"]), ("Моя", "analytics"))

        members_calls = len(self.vk.params("groups.getMembers"))
        comparison = await self.engine.compare(5, 7)
        self.assertEqual((comparison.mine.community.vk_id, comparison.other.community.vk_id), (5, 7))
        self.assertIs(comparison.other, other)
        self.assertEqual(len(self.vk.params("groups.getMembers")), members_calls)
        self.assertTrue(comparison.charts)

        fresh = await self.engine.compare(5, 8)
        self.assertEqual(fresh.other.community.vk_id, 8)
        with self.assertRaises(UserError):
            await self.engine.compare(5, 5)
        self.engine._reports[8] = (time.monotonic() - engine_module.REPORT_CACHE_SEC - 1, fresh.other)
        self.assertIsNone(self.engine.cached_report(8))

    def test_apply_and_reset_schedule(self):
        self.store.add_community(5, "Моя")
        community = self.engine.apply_schedule(5, [20, 9, 14], [0, 1, 2, 3, 4])
        self.assertEqual((community.hours, community.days, community.posts_per_day), ([9, 14, 20], [0, 1, 2, 3, 4], 3))
        community = self.engine.reset_schedule(5)
        self.assertEqual((community.hours, community.days), ([], list(range(7))))
        with self.assertRaises(UserError):
            self.engine.apply_schedule(6, [9], [0])

    async def test_stories_use_stock_first(self):
        self.store.add_community(5, "Моя")
        folder = self.engine.folder(5)
        folder.mkdir(parents=True)
        (folder / "a.jpg").write_bytes(make_jpeg(1))
        self.vk.handlers.update({"stories.getPhotoUploadServer": {"upload_url": "s"}, "stories.save": 1})
        self.assertEqual(await self.engine.stories(5, 1), 1)
        self.assertEqual(self.engine.stock(5), 0)
        self.assertNotIn("wall.get", self.vk.methods())

    async def test_set_token_validates_and_persists(self):
        self.vk.handlers.update({
            "users.get": [{"first_name": "Иван", "last_name": "П"}],
            "account.getAppPermissions": 4 | 8192 | 262144,
        })
        self.assertEqual(await self.engine.set_token("new"), "Иван П")
        self.assertEqual(self.store.get_setting("vk_token"), "new")

        self.vk.handlers["account.getAppPermissions"] = 2
        with self.assertRaises(UserError) as ctx:
            await self.engine.set_token("narrow")
        self.assertIn("wall", str(ctx.exception))
        self.assertEqual(self.vk.token, "new")

        self.vk.handlers["users.get"] = VkAuthError(5, "bad")
        with self.assertRaises(UserError):
            await self.engine.set_token("broken")
        self.assertEqual(self.vk.token, "new")
        self.assertEqual(self.store.get_setting("vk_token"), "new")

    async def test_whoami_and_close(self):
        self.vk.handlers["users.get"] = [{"first_name": "Аня"}]
        self.assertEqual(await self.engine.whoami(), "Аня")
        await self.engine.close()
        self.assertTrue(self.vk.closed)

    def test_jsonable(self):
        self.assertEqual(_jsonable(None), None)
        self.assertEqual(_jsonable(3), {"result": 3})
        self.assertIn("published", _jsonable(FillResult()))

    def test_analytics_report_is_saved_with_job(self):
        import json

        members = MemberStats(total=2, ages=Counter({"18–24": 1, "не указан": 1}))
        report = Report(community=Community(vk_id=5), members=members, posts=PostStats(series=[(1, 2, 3)]))
        data = _jsonable(report)
        self.assertEqual(data["members"]["ages"], {"18–24": 1, "не указан": 1})
        self.assertNotIn("series", data["posts"])
        self.assertNotIn("community", data)
        json.dumps(data)
        both = _jsonable(Comparison(report, report))
        self.assertEqual(set(both), {"mine", "other"})
        json.dumps(both)

        self.store.add_community(5)
        job = self.store.start_job("analytics", 5)
        self.store.finish_job(job, "ok", stats=data)
        self.assertEqual(self.store.last_job(5)["status"], "ok")


class FakeEngine:
    def __init__(self, store, config, queues, fills):
        self.store = store
        self.config = config
        self.queues = queues
        self.fills = fills
        self.busy = False
        self.filled = []
        self.stocks = {}
        self.account = "Аня"
        self.access_checks = 0
        self.queues_read = 0

    def stock(self, vk_id):
        return self.stocks.get(vk_id, 0)

    async def whoami(self):
        self.access_checks += 1
        if isinstance(self.account, BaseException):
            raise self.account
        return self.account

    async def queue(self, vk_id):
        self.queues_read += 1
        value = self.queues[vk_id]
        if isinstance(value, BaseException):
            raise value
        return value

    async def fill(self, vk_id):
        self.filled.append(vk_id)
        value = self.fills[vk_id]
        if isinstance(value, BaseException):
            raise value
        return value


class AutoFillTests(AsyncTempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.messages = []
        self.now = [1000.0]

    async def notify(self, text, silent=False):
        self.messages.append((text, silent))

    def add(self, vk_id, name):
        self.store.add_community(vk_id, name)
        return self.store.update_community(vk_id, autofill=True)

    def filler(self, queues, fills):
        engine = FakeEngine(self.store, self.config, queues, fills)
        return engine, AutoFiller(engine, self.notify, 60, clock=lambda: self.now[0])

    def test_needs_refill(self):
        self.assertTrue(needs_refill(6, 9, 3))
        self.assertFalse(needs_refill(7, 9, 3))
        self.assertTrue(needs_refill(0, 1, 0))

    async def test_fills_only_short_queues_and_reports(self):
        from services.publisher import PublishStats, QueueInfo

        self.add(1, "Полная")
        self.add(2, "Пустая")
        self.add(3, "Выключенная")
        self.store.update_community(1, queue_days=3)
        self.store.update_community(3, autofill=False)
        done = FillResult(needed=9, published=PublishStats(posts_created=9, last_date=NOW))
        far = QueueInfo(count=200, last_date=int(self.now[0]) + 60 * 86400)
        engine, filler = self.filler({1: QueueInfo(count=9), 2: QueueInfo(count=0), 3: far}, {2: done})
        self.assertFalse(await filler.tick())
        self.assertEqual(engine.filled, [2])
        self.assertEqual(len(self.messages), 1)
        self.assertIn("добавил в отложку 9 постов", self.messages[0][0])
        self.assertTrue(self.messages[0][1])

    async def test_low_queue_without_autofill_is_reported_daily_with_buttons(self):
        from services.publisher import QueueInfo

        self.store.add_community(1, "Ручная")
        self.store.add_community(2, "Пустая")
        self.store.add_community(3, "С запасом")
        markups = []

        async def notify(text, silent=False, **extra):
            self.messages.append((text, silent))
            markups.append(extra.get("reply_markup"))

        now = self.now[0]
        queues = {1: QueueInfo(count=6, last_date=int(now) + 2 * 86400), 2: QueueInfo(count=0),
                  3: QueueInfo(count=30, last_date=int(now) + 10 * 86400)}
        engine = FakeEngine(self.store, self.config, queues, {})
        filler = AutoFiller(engine, notify, 60, clock=lambda: self.now[0], low_queue_buttons=lambda cid: f"kb{cid}")
        self.assertFalse(await filler.tick())
        self.assertEqual(engine.filled, [])
        self.assertEqual(len(self.messages), 2)
        self.assertIn("«Ручная»: в отложке осталось 6 постов, их хватит до", self.messages[0][0])
        self.assertIn("Автопополнение выключено", self.messages[0][0])
        self.assertIn("«Пустая»: отложка пустая", self.messages[1][0])
        self.assertEqual(markups, ["kb1", "kb2"])

        self.now[0] += 13 * 3600
        await filler.tick()
        self.assertEqual(len(self.messages), 2)
        self.assertEqual(engine.queues_read, 3)
        self.now[0] += 12 * 3600
        await filler.tick()
        self.assertEqual(len(self.messages), 4)
        self.assertEqual(engine.queues_read, 6)

        plain = AutoFiller(engine, self.notify, 60, clock=lambda: self.now[0])
        engine.queues = {1: QueueInfo(count=1), 2: QueueInfo(count=30, last_date=int(self.now[0]) + 30 * 86400),
                         3: VkAuthError(5, "expired")}
        self.messages.clear()
        await plain.tick()
        self.assertEqual(len(self.messages), 2)
        self.assertIn("«Ручная»: в отложке осталось 1 пост.", self.messages[0][0])
        self.assertIn("ВК больше не пускает бота", self.messages[1][0])

    async def test_vk_access_is_checked_twice_a_day(self):
        from services.publisher import QueueInfo

        engine, filler = self.filler({}, {})
        await filler.tick()
        self.assertEqual(engine.access_checks, 0)
        self.now[0] += 12 * 3600
        self.assertFalse(await filler.tick())
        self.assertEqual((engine.access_checks, self.messages), (1, []))
        engine.account = RuntimeError("network")
        self.now[0] += 12 * 3600
        await filler.tick()
        self.assertEqual(self.messages, [])
        engine.account = VkAuthError(5, "expired")
        self.now[0] += 12 * 3600
        self.add(1, "А")
        engine.queues[1] = QueueInfo(count=0)
        self.assertFalse(await filler.tick())
        self.assertIn("ВК больше не пускает бота", self.messages[0][0])
        self.assertEqual((engine.access_checks, engine.queues_read), (3, 0))

    async def test_big_queue_is_filled_in_parts_with_one_message(self):
        from services.publisher import PublishStats, QueueInfo

        self.add(1, "Группа")
        part = FillResult(needed=50, remaining=220, published=PublishStats(posts_created=50, last_date=NOW))
        engine, filler = self.filler({1: QueueInfo(count=0)}, {1: part})
        self.assertTrue(await filler.tick())
        self.assertTrue(await filler.tick())
        self.assertEqual(self.messages, [])
        engine.fills[1] = FillResult(needed=50, remaining=170, published=PublishStats(posts_created=49, upload_failed=3))
        self.assertTrue(await filler.tick())
        engine.fills[1] = FillResult(needed=20, published=PublishStats(posts_created=19, last_date=NOW))
        engine.stocks[1] = 6
        self.assertFalse(await filler.tick())
        self.assertEqual(len(self.messages), 1)
        self.assertIn("добавил в отложку 168 постов", self.messages[0][0])

    async def test_vk_denial_is_reported_once(self):
        from services.publisher import PublishStats, QueueInfo

        self.add(1, "Группа")
        denied = FillResult(needed=50, remaining=100, published=PublishStats(posts_created=10, denied=True))
        engine, filler = self.filler({1: QueueInfo(count=0)}, {1: denied})
        self.assertFalse(await filler.tick())
        await filler.tick()
        self.assertEqual(len(self.messages), 3)
        self.assertIn("добавил в отложку 10 постов", self.messages[0][0])
        self.assertIn("суточный лимит", self.messages[1][0])
        self.assertNotIn("мало новых картинок", "".join(m for m, _s in self.messages))

    async def test_slowed_uploads_wait_for_next_interval(self):
        from services.publisher import PublishStats, QueueInfo

        self.add(1, "Группа")
        slowed = FillResult(needed=50, remaining=100, published=PublishStats(posts_created=3, throttled=True))
        engine, filler = self.filler({1: QueueInfo(count=0)}, {1: slowed})
        self.assertFalse(await filler.tick())
        self.assertEqual(len(self.messages), 1)
        self.assertIn("добавил в отложку 3 поста", self.messages[0][0])
        self.assertNotIn("мало новых картинок", self.messages[0][0])

    async def test_short_result_and_repeated_warnings_are_throttled(self):
        from services.publisher import PublishStats, QueueInfo

        self.add(1, "Группа")
        short = FillResult(needed=5, published=PublishStats(posts_created=0))
        engine, filler = self.filler({1: QueueInfo(count=0)}, {1: short})
        await filler.tick()
        await filler.tick()
        self.assertEqual(len(self.messages), 1)
        self.assertIn("мало новых картинок", self.messages[0][0])
        self.now[0] += 13 * 3600
        await filler.tick()
        self.assertEqual(len(self.messages), 2)

    async def test_errors_become_messages(self):
        from services.publisher import QueueInfo

        self.add(1, "А")
        self.add(2, "Б")
        cases = [
            ({1: VkFloodError(9, "f", "photos.saveWallPhoto", retry_after=600), 2: QueueInfo()},
             {2: UserError("нет источников")}),
            ({1: RuntimeError("boom"), 2: QueueInfo()}, {2: BusyError("занят")}),
        ]
        engine, filler = self.filler(*cases[0])
        await filler.tick()
        self.assertIn("Не смог пополнить «А». ВК временно ограничил загрузку картинок в посты", self.messages[0][0])
        self.assertIn("Попробую снова сам примерно через 10 мин", self.messages[0][0])
        self.assertIn("нет источников", self.messages[1][0])
        self.messages.clear()
        engine, filler = self.filler(*cases[1])
        await filler.tick()
        self.assertEqual(len(self.messages), 1)
        self.assertIn("ошибки", self.messages[0][0])
        self.messages.clear()
        engine, filler = self.filler({1: VkError(100, "bad", "wall.post"), 2: VkError(-1, "net")}, {})
        await filler.tick()
        self.assertIn("ВК не выполнил публикацию постов и ответил ошибкой: bad", self.messages[0][0])
        self.assertIn("«Б»: ВК не отвечает", self.messages[1][0])

    async def test_auth_error_stops_the_round(self):
        from services.publisher import QueueInfo

        self.add(1, "А")
        self.add(2, "Б")
        engine, filler = self.filler({1: VkAuthError(5, "x"), 2: QueueInfo()}, {})
        await filler.tick()
        self.assertEqual(engine.filled, [])
        self.assertIn("Обновить доступ", self.messages[0][0])

    async def test_busy_engine_is_left_alone(self):
        self.add(1, "А")
        engine, filler = self.filler({}, {})
        engine.busy = True
        await filler.tick()
        self.assertEqual(self.messages, [])

    async def test_run_survives_crashing_tick(self):
        engine, filler = self.filler({}, {})
        ticks = []

        async def tick():
            ticks.append(1)
            if len(ticks) == 1:
                raise RuntimeError("boom")
            raise asyncio.CancelledError

        filler.tick = tick
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        from unittest.mock import patch
        with patch("services.autofill.asyncio.sleep", fake_sleep):
            with self.assertRaises(asyncio.CancelledError):
                await filler.run()
        self.assertEqual(len(ticks), 2)
        self.assertEqual(sleeps[0], 60)


if __name__ == "__main__":
    unittest.main()
