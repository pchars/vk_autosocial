import asyncio
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from api.vk import VkAuthError, VkClient, VkError, VkFloodError
from utils.store import Community

MEMBER_PAGE = 1000
MAX_MEMBER_PAGES = 20
POST_PAGES = 3
SUMMARY_DAYS = 30
# Views keep growing for about a day; younger posts would drag the numbers down.
FRESH_POST_SEC = 24 * 60 * 60
# Members seen in the last day would put every hour next to the time of the analysis.
ONLINE_MIN_AGE_SEC = 24 * 60 * 60
ONLINE_MAX_AGE_SEC = 14 * 24 * 60 * 60
MIN_POSTS_PER_SLOT = 3
PRIOR_POSTS = 5
NEIGHBOURS = 5
WEAK_DAY_SHARE = 0.8
MIN_DAYS = 5
NIGHT_HOURS = {2, 3, 4, 5}
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
AGE_BUCKETS: List[Tuple[str, int, int]] = [
    ("до 18", 0, 17), ("18–24", 18, 24), ("25–34", 25, 34), ("35–44", 35, 44), ("45+", 45, 200),
]
UNKNOWN_AGE = "не указан"


def percent(part: float, whole: float) -> int:
    return round(100 * part / whole) if whole else 0


@dataclass
class MemberStats:
    total: int = 0
    sampled: int = 0
    hidden: bool = False
    dead: int = 0
    female: int = 0
    male: int = 0
    active_week: int = 0
    active_month: int = 0
    inactive: int = 0
    ages: Counter = field(default_factory=Counter)
    online_hours: List[int] = field(default_factory=lambda: [0] * 24)

    @property
    def alive(self) -> int:
        return self.sampled - self.dead

    @property
    def online_share(self) -> List[float]:
        seen = sum(self.online_hours)
        return [count / seen for count in self.online_hours] if seen else []


@dataclass
class PostStats:
    count: int = 0
    hidden: bool = False
    per_day: float = 0.0
    median_views: int = 0
    median_likes: int = 0
    median_reposts: int = 0
    median_comments: int = 0
    engagement: float = 0.0
    series: List[Tuple[int, int, int]] = field(default_factory=list)
    hour_scores: Dict[int, float] = field(default_factory=dict)
    day_scores: Dict[int, float] = field(default_factory=dict)


@dataclass
class Recommendation:
    hours: List[int]
    days: List[int]
    by_posts: bool

    @property
    def every_day(self) -> bool:
        return len(self.days) == 7


@dataclass
class Report:
    community: Community
    members: MemberStats
    posts: PostStats
    recommendation: Optional[Recommendation] = None
    charts: List[Path] = field(default_factory=list)

    @property
    def reach(self) -> float:
        """Typical post views as a share of members, in percent."""
        return round(100 * self.posts.median_views / self.members.total, 1) if self.members.total else 0.0


@dataclass
class Comparison:
    mine: Report
    other: Report
    charts: List[Path] = field(default_factory=list)


def _age(bdate: str, year: int) -> Optional[int]:
    parts = str(bdate or "").split(".")
    return year - int(parts[2]) if len(parts) == 3 and parts[2].isdigit() else None


def member_stats(members: Iterable[Dict], total: int, now: float, tz: str = "UTC") -> MemberStats:
    stats = MemberStats(total=total)
    zone = ZoneInfo(tz)
    year = datetime.fromtimestamp(now, tz=timezone.utc).year
    for user in members:
        stats.sampled += 1
        if user.get("deactivated"):
            stats.dead += 1
            continue
        if user.get("sex") == 1:
            stats.female += 1
        elif user.get("sex") == 2:
            stats.male += 1
        seen = int((user.get("last_seen") or {}).get("time", 0))
        if seen >= now - 7 * 86400:
            stats.active_week += 1
        elif seen >= now - 30 * 86400:
            stats.active_month += 1
        else:
            stats.inactive += 1
        if now - ONLINE_MAX_AGE_SEC <= seen <= now - ONLINE_MIN_AGE_SEC:
            stats.online_hours[datetime.fromtimestamp(seen, zone).hour] += 1
        age = _age(user.get("bdate"), year)
        label = next((name for name, low, high in AGE_BUCKETS if age is not None and low <= age <= high), None)
        stats.ages[label or UNKNOWN_AGE] += 1
    return stats


def _metric(post: Dict, name: str) -> int:
    return int((post.get(name) or {}).get("count", 0))


def usable_posts(posts: Iterable[Dict], now: float) -> List[Dict]:
    """The group's own regular posts that already collected their views."""
    return sorted(
        (p for p in posts
         if p.get("date") and int(p["date"]) <= now - FRESH_POST_SEC
         and not p.get("is_pinned") and not p.get("marked_as_ads")),
        key=lambda p: int(p["date"]),
    )


def post_stats(posts: Iterable[Dict], now: float, tz: str = "UTC") -> PostStats:
    ready = usable_posts(posts, now)
    recent = [p for p in ready if int(p["date"]) >= now - SUMMARY_DAYS * 86400]
    stats = PostStats(count=len(recent))
    if recent:
        stats.per_day = round(len(recent) / SUMMARY_DAYS, 1)
        stats.median_views = int(statistics.median(_metric(p, "views") for p in recent))
        stats.median_likes = int(statistics.median(_metric(p, "likes") for p in recent))
        stats.median_reposts = int(statistics.median(_metric(p, "reposts") for p in recent))
        stats.median_comments = int(statistics.median(_metric(p, "comments") for p in recent))
        rates = [
            (_metric(p, "likes") + _metric(p, "reposts") + _metric(p, "comments")) / _metric(p, "views")
            for p in recent if _metric(p, "views")
        ]
        stats.engagement = round(100 * statistics.median(rates), 1) if rates else 0.0
        stats.series = [(int(p["date"]), _metric(p, "views"), _metric(p, "likes")) for p in recent]
    stats.hour_scores, stats.day_scores = slot_scores(ready, tz)
    return stats


def slot_scores(posts: List[Dict], tz: str) -> Tuple[Dict[int, float], Dict[int, float]]:
    """How each hour and weekday does compared to posts around the same date (removes growth trends)."""
    zone = ZoneInfo(tz)
    views = [_metric(p, "views") for p in posts]
    by_hour: Dict[int, List[float]] = defaultdict(list)
    by_day: Dict[int, List[float]] = defaultdict(list)
    for index, post in enumerate(posts):
        around = views[max(0, index - NEIGHBOURS):index + NEIGHBOURS + 1]
        typical = statistics.median(around)
        if not typical:
            continue
        moment = datetime.fromtimestamp(int(post["date"]), zone)
        score = views[index] / typical
        by_hour[moment.hour].append(score)
        by_day[moment.weekday()].append(score)

    def average(groups: Dict[int, List[float]]) -> Dict[int, float]:
        # Few posts per slot are pulled towards 1.0 so one viral post can't decide the schedule.
        return {k: round((sum(v) + PRIOR_POSTS) / (len(v) + PRIOR_POSTS), 2)
                for k, v in groups.items() if len(v) >= MIN_POSTS_PER_SLOT}

    return average(by_hour), average(by_day)


def recommend(members: MemberStats, posts: PostStats, count: int) -> Optional[Recommendation]:
    """Best `count` hours (audience online, corrected by how posts did) and weekdays without weak ones."""
    online = members.online_share
    by_posts = len(posts.hour_scores) >= count
    if not online and not by_posts:
        return None
    mean_online = (sum(online) / 24) if online else 0
    scores = {}
    for hour in (range(24) if online else posts.hour_scores):
        if hour in NIGHT_HOURS and count <= 12:
            continue
        audience = online[hour] / mean_online if mean_online else 1.0
        scores[hour] = audience * posts.hour_scores.get(hour, 1.0)
    gap = max(1, 12 // max(1, count))
    hours: List[int] = []
    for hour in sorted(scores, key=lambda h: (-scores[h], h)):
        if all(min(abs(hour - h), 24 - abs(hour - h)) >= gap for h in hours):
            hours.append(hour)
        if len(hours) == count:
            break
    if len(hours) < count:
        return None
    days = list(range(7))
    if len(posts.day_scores) == 7:
        typical = statistics.median(posts.day_scores.values())
        ranked = sorted(posts.day_scores, key=lambda d: posts.day_scores[d])
        weak = [d for d in ranked if posts.day_scores[d] < WEAK_DAY_SHARE * typical][:7 - MIN_DAYS]
        days = [d for d in days if d not in weak]
    return Recommendation(hours=sorted(hours), days=days, by_posts=by_posts)


class Analytics:
    def __init__(self, vk: VkClient, timezone_name: str = "Europe/Moscow"):
        self.vk = vk
        self.tz = timezone_name

    async def report(self, community: Community, charts_dir: Path, now: Optional[float] = None,
                     posts_per_day: Optional[int] = None) -> Report:
        now = now or time.time()
        total = await self._members_count(community.vk_id)
        members, hidden_members = await self._members(community.vk_id, total)
        posts, hidden_posts = await self._posts(community.vk_id)
        member_part = member_stats(members, total, now, self.tz)
        member_part.hidden = hidden_members
        post_part = post_stats(posts, now, self.tz)
        post_part.hidden = hidden_posts
        report = Report(community, member_part, post_part)
        report.recommendation = recommend(member_part, post_part, posts_per_day or community.posts_per_day)
        folder = Path(charts_dir) / str(community.vk_id)
        report.charts = await asyncio.to_thread(render_charts, report, folder)
        return report

    async def compare(self, mine: Report, other: Report, charts_dir: Path) -> Comparison:
        folder = Path(charts_dir) / f"compare_{mine.community.vk_id}_{other.community.vk_id}"
        comparison = Comparison(mine, other)
        comparison.charts = await asyncio.to_thread(render_comparison, comparison, folder)
        return comparison

    async def _members_count(self, community_id: int) -> int:
        answer = await self.vk.call("groups.getById", group_id=community_id, fields="members_count")
        groups = answer.get("groups") if isinstance(answer, dict) else answer
        return int((groups or [{}])[0].get("members_count", 0))

    async def _members(self, community_id: int, total: int) -> Tuple[List[Dict], bool]:
        """Pages spread over the whole list: the list is sorted by id, the first pages are the oldest members."""
        pages = max(1, -(-total // MEMBER_PAGE))
        if pages <= MAX_MEMBER_PAGES:
            offsets = [page * MEMBER_PAGE for page in range(pages)]
        else:
            offsets = [round(i * (total - MEMBER_PAGE) / (MAX_MEMBER_PAGES - 1)) for i in range(MAX_MEMBER_PAGES)]
        items: List[Dict] = []
        for offset in offsets:
            try:
                chunk = await self.vk.call(
                    "groups.getMembers", group_id=community_id, offset=offset,
                    count=MEMBER_PAGE, fields="sex,bdate,last_seen",
                )
            except (VkAuthError, VkFloodError):
                raise
            except VkError:
                return items, not items
            items.extend((chunk or {}).get("items") or [])
        return items, False

    async def _posts(self, community_id: int) -> Tuple[List[Dict], bool]:
        posts: List[Dict] = []
        for page in range(POST_PAGES):
            try:
                wall = await self.vk.call(
                    "wall.get", owner_id=-int(community_id), filter="owner", count=100, offset=page * 100
                )
            except (VkAuthError, VkFloodError):
                raise
            except VkError:
                return posts, not posts
            batch = (wall or {}).get("items") or []
            posts.extend(batch)
            if len(batch) < 100:
                break
        return posts, False


def _pyplot():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def render_charts(report: Report, folder: Path) -> List[Path]:
    plt = _pyplot()
    folder.mkdir(parents=True, exist_ok=True)
    charts: List[Path] = []
    members = report.members

    if members.alive:
        fig, (left, right) = plt.subplots(1, 2, figsize=(10, 5))
        _pie(left, "Пол", {"Женщины": members.female, "Мужчины": members.male})
        _pie(right, "Активность", {
            "Заходили за неделю": members.active_week,
            "За месяц": members.active_month,
            "Давно не заходили": members.inactive,
            "Удалённые и заблокированные": members.dead,
        })
        charts.append(_save(fig, plt, folder / "audience.png"))

    known_ages = members.alive - members.ages.get(UNKNOWN_AGE, 0)
    if known_ages:
        fig, axis = plt.subplots(figsize=(8, 5))
        labels = [name for name, _l, _h in AGE_BUCKETS]
        axis.bar(labels, [percent(members.ages.get(label, 0), known_ages) for label in labels], color="#4a76a8")
        axis.set_ylabel("% участников")
        axis.set_title(f"Возраст (указали {percent(known_ages, members.alive)}% участников)")
        charts.append(_save(fig, plt, folder / "ages.png"))

    if report.posts.series:
        fig, views_axis = plt.subplots(figsize=(10, 5))
        dates = [datetime.fromtimestamp(ts, tz=timezone.utc) for ts, _v, _l in report.posts.series]
        views_axis.plot(dates, [v for _t, v, _l in report.posts.series], marker="o", color="#4a76a8")
        views_axis.set_ylabel("Просмотры", color="#4a76a8")
        likes_axis = views_axis.twinx()
        likes_axis.plot(dates, [lk for _t, _v, lk in report.posts.series], marker=".", color="#e64646")
        likes_axis.set_ylabel("Лайки", color="#e64646")
        views_axis.set_title(f"Посты за {SUMMARY_DAYS} дней")
        fig.autofmt_xdate()
        charts.append(_save(fig, plt, folder / "posts.png"))

    if members.online_share or report.posts.hour_scores:
        charts.append(_hours_chart(plt, report, folder / "hours.png"))
    return charts


def _hours_chart(plt, report: Report, path: Path) -> Path:
    fig, axis = plt.subplots(figsize=(10, 5))
    hours = list(range(24))
    chosen = set(report.recommendation.hours) if report.recommendation else set()
    online = report.members.online_share
    if online:
        colors = ["#2e9e5b" if h in chosen else "#4a76a8" for h in hours]
        axis.bar(hours, [round(100 * v, 1) for v in online], color=colors)
        axis.set_ylabel("% аудитории, когда заходят")
    if report.posts.hour_scores:
        scores = report.posts.hour_scores
        twin = axis.twinx() if online else axis
        known = sorted(scores)
        twin.plot(known, [scores[h] for h in known], "o", color="#e64646")
        twin.axhline(1.0, color="#e64646", linewidth=0.5, linestyle="--")
        twin.set_ylabel("Просмотры поста к обычным (1 = как всегда)", color="#e64646")
    axis.set_xticks(hours)
    axis.set_xlabel("Час")
    axis.set_title("Когда публиковать" + (" (зелёные — рекомендованные часы)" if chosen and online else ""))
    return _save(fig, plt, path)


def comparison_rows(comparison: Comparison) -> List[Tuple[str, float, float]]:
    """Share-based numbers that make sense for groups of different size."""
    mine, other = comparison.mine, comparison.other
    rows = []
    if mine.members.alive and other.members.alive:
        rows += [
            ("Заходили за неделю, %", percent(mine.members.active_week, mine.members.alive),
             percent(other.members.active_week, other.members.alive)),
            ("Удалённые и заблокированные, %", percent(mine.members.dead, mine.members.sampled),
             percent(other.members.dead, other.members.sampled)),
            ("Женщин, %", percent(mine.members.female, mine.members.alive),
             percent(other.members.female, other.members.alive)),
        ]
    if mine.posts.count and other.posts.count:
        rows += [
            ("Охват поста, % участников", mine.reach, other.reach),
            ("Вовлечённость, %", mine.posts.engagement, other.posts.engagement),
            ("Постов в день", mine.posts.per_day, other.posts.per_day),
        ]
    return rows


def render_comparison(comparison: Comparison, folder: Path) -> List[Path]:
    plt = _pyplot()
    folder.mkdir(parents=True, exist_ok=True)
    charts: List[Path] = []
    names = (comparison.mine.community.title, comparison.other.community.title)
    rows = comparison_rows(comparison)
    if rows:
        fig, axis = plt.subplots(figsize=(10, 5))
        positions = list(range(len(rows)))
        axis.barh([p + 0.2 for p in positions], [r[1] for r in rows], height=0.4, label=names[0], color="#4a76a8")
        axis.barh([p - 0.2 for p in positions], [r[2] for r in rows], height=0.4, label=names[1], color="#f0a030")
        axis.set_yticks(positions)
        axis.set_yticklabels([r[0] for r in rows])
        axis.invert_yaxis()
        axis.legend()
        axis.set_title("Сравнение")
        charts.append(_save(fig, plt, folder / "compare.png"))

    lines = [(name, report.members.online_share) for name, report in zip(names, (comparison.mine, comparison.other))]
    if any(share for _name, share in lines):
        fig, axis = plt.subplots(figsize=(10, 5))
        for (name, share), color in zip(lines, ("#4a76a8", "#f0a030")):
            if share:
                axis.plot(range(24), [round(100 * v, 1) for v in share], "o-", label=name, color=color)
        axis.set_xticks(range(24))
        axis.set_xlabel("Час")
        axis.set_ylabel("% аудитории")
        axis.set_title("Когда заходит аудитория")
        axis.legend()
        charts.append(_save(fig, plt, folder / "compare_hours.png"))
    return charts


def _pie(axis, title: str, values: Dict[str, int]) -> None:
    values = {k: v for k, v in values.items() if v}
    axis.set_title(title)
    if values:
        axis.pie(list(values.values()), labels=list(values.keys()), autopct="%1.0f%%", startangle=90)
    axis.axis("equal")


def _save(fig, plt, path: Path) -> Path:
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)
    return path
