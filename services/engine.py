import asyncio
import time
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, TypeVar

from api.vk import VkClient, VkError
from utils.config import Config
from utils.logger import get_logger
from utils.store import Community, Store
from utils.vk_links import community_refs, find_community_ref

from .account import AccountService
from .analytics import Analytics, Comparison, Report
from .collector import CollectStats, Collector
from .images import list_images
from .publisher import MAX_POSTPONED, PublishStats, Publisher, QueueInfo, posts_needed
from .stories import StoryPublisher

logger = get_logger(__name__)
T = TypeVar("T")

# Bits from account.getAppPermissions the bot cannot work without.
REQUIRED_PERMISSIONS = {"photos": 4, "wall": 8192, "groups": 262144}
# A long run blocks every other button, so big queues are filled in several runs.
MAX_POSTS_PER_RUN = 50
EDITOR_LEVEL = 2
# A comparison right after an analytics report reuses it instead of asking VK again.
REPORT_CACHE_SEC = 15 * 60


class UserError(Exception):
    """A problem the person in the chat can fix; the text is shown as is."""


class BusyError(UserError):
    pass


@dataclass
class GroupInfo:
    vk_id: int
    name: str
    url: str
    can_post: bool
    is_closed: bool


@dataclass
class FillResult:
    queued_before: int = 0
    needed: int = 0
    remaining: int = 0
    collected: CollectStats = field(default_factory=CollectStats)
    published: PublishStats = field(default_factory=PublishStats)


class Engine:
    def __init__(self, config: Config, store: Store, vk: VkClient):
        self.config = config
        self.store = store
        self.vk = vk
        self.collector = Collector(vk, store)
        self.publisher = Publisher(vk, store, config.texts_file, timezone=config.timezone)
        self.stories_publisher = StoryPublisher(vk, store)
        self.account = AccountService(vk)
        self.analytics_service = Analytics(vk, config.timezone)
        self._lock = asyncio.Lock()
        self.current_task = ""
        self._reports: Dict[int, Tuple[float, Report]] = {}

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def folder(self, community_id: int) -> Path:
        return self.config.images_dir / str(community_id)

    def stock(self, community_id: int) -> int:
        return len(list_images(self.folder(community_id)))

    def community(self, community_id: int) -> Community:
        community = self.store.get_community(community_id)
        if community is None:
            raise UserError("Эта группа уже удалена из бота.")
        return community

    async def close(self) -> None:
        await self.vk.close()

    # Groups

    async def resolve(self, text: str) -> GroupInfo:
        ref = find_community_ref(text)
        if not ref:
            raise UserError("Не похоже на ссылку на группу ВК. Пример: https://vk.com/club123456")
        return await self._group_info(ref)

    async def add_community(self, text: str) -> Community:
        info = await self.resolve(text)
        if not info.can_post:
            raise UserError(
                f"В группе «{info.name}» у вашего аккаунта ВК нет прав редактора или администратора. "
                "Без них бот не сможет ставить посты в отложку. Выдайте права в настройках группы "
                "(Управление → Участники → Руководители) и пришлите ссылку ещё раз."
            )
        return self.store.add_community(info.vk_id, info.name, info.url)

    async def add_sources(self, community_id: int, text: str) -> Tuple[List[str], List[str]]:
        """Returns (added names, problems) for every community found in the message."""
        refs = community_refs(text)
        if not refs:
            raise UserError("Не нашёл в сообщении ссылок на группы ВК. Пример: https://vk.com/club123456")
        added: List[str] = []
        problems: List[str] = []
        for ref in refs:
            try:
                info = await self._group_info(ref)
            except UserError as exc:
                problems.append(f"{ref}: {exc}")
                continue
            if info.vk_id == int(community_id):
                problems.append(f"«{info.name}» — это сама группа, из неё брать картинки нельзя")
            elif info.is_closed:
                problems.append(f"«{info.name}» — закрытая группа, бот не видит её записи")
            elif self.store.add_source(community_id, info.vk_id, info.name, info.url):
                added.append(info.name)
            else:
                problems.append(f"«{info.name}» уже есть в списке")
        return added, problems

    async def _group_info(self, ref: str) -> GroupInfo:
        try:
            answer = await self.vk.call("groups.getById", group_id=ref, fields="is_admin,admin_level,is_member")
        except VkError as exc:
            if exc.code == 100:
                raise UserError("такой группы ВК нет") from exc
            raise
        groups = answer.get("groups") if isinstance(answer, dict) else answer
        if not groups:
            raise UserError("такой группы ВК нет")
        group = groups[0]
        if group.get("deactivated"):
            raise UserError(f"группа «{group.get('name', ref)}» удалена или заблокирована")
        screen = group.get("screen_name") or f"club{group['id']}"
        return GroupInfo(
            vk_id=int(group["id"]),
            name=group.get("name") or screen,
            url=f"https://vk.com/{screen}",
            can_post=bool(group.get("is_admin")) and int(group.get("admin_level", 0)) >= EDITOR_LEVEL,
            is_closed=int(group.get("is_closed", 0)) > 0 and not group.get("is_member"),
        )

    # Content

    async def queue(self, community_id: int) -> QueueInfo:
        return await self.publisher.queue(community_id)

    async def fill(self, community_id: int, posts: Optional[int] = None) -> FillResult:
        community = self.community(community_id)
        sources = self.store.list_sources(community_id)
        if not sources:
            raise UserError(f"У группы «{community.title}» нет источников картинок. Добавьте их в карточке группы.")

        async def job() -> FillResult:
            queue = await self.publisher.queue(community_id)
            wanted = max(0, min(posts if posts else posts_needed(community, queue.count), MAX_POSTPONED - queue.count))
            needed = min(wanted, MAX_POSTS_PER_RUN)
            result = FillResult(queued_before=queue.count, needed=needed, remaining=wanted - needed)
            if result.needed == 0:
                return result
            folder = self.folder(community_id)
            missing = result.needed * community.photos_per_post - len(list_images(folder))
            result.collected = await self.collector.collect(community, sources, folder, missing)
            result.published = await self.publisher.publish(community, folder, result.needed, queue)
            return result

        return await self._job("fill", f"пополняет «{community.title}»", community_id, job)

    async def stories(self, community_id: int, count: int) -> int:
        community = self.community(community_id)

        async def job() -> int:
            folder = self.folder(community_id)
            missing = count - len(list_images(folder))
            if missing > 0:
                await self.collector.collect(community, self.store.list_sources(community_id), folder, missing)
            return await self.stories_publisher.publish(community_id, folder, count)

        return await self._job("stories", f"публикует сторис в «{community.title}»", community_id, job)

    async def clear(self, community_id: int, published: bool = False) -> int:
        community = self.community(community_id)
        return await self._job(
            "clear", f"чистит стену «{community.title}»", community_id,
            lambda: self.publisher.clear(community_id, published=published),
        )

    async def analytics(self, community_id: int) -> Report:
        community = self.community(community_id)
        return await self._job(
            "analytics", f"считает аналитику «{community.title}»", community_id,
            lambda: self._report(community, fresh=True),
        )

    async def analytics_of(self, text: str) -> Report:
        """Any VK community by link, from public data only."""
        info = await self.resolve(text)
        if self.store.get_community(info.vk_id):
            return await self.analytics(info.vk_id)
        other = Community(vk_id=info.vk_id, name=info.name, url=info.url)
        return await self._job(
            "analytics_other", f"считает аналитику «{other.title}»", None, lambda: self._report(other, fresh=True)
        )

    async def compare(self, community_id: int, other_id: int) -> Comparison:
        mine = self.community(community_id)
        if int(other_id) == mine.vk_id:
            raise UserError("Это та же самая группа — пришлите ссылку на другую.")
        cached = self.cached_report(other_id)
        if cached:
            other = cached.community
        else:
            info = await self._group_info(str(int(other_id)))
            other = Community(vk_id=info.vk_id, name=info.name, url=info.url)

        async def job() -> Comparison:
            mine_report = await self._report(mine)
            other_report = await self._report(other)
            return await self.analytics_service.compare(mine_report, other_report, self.config.charts_dir)

        return await self._job("compare", f"сравнивает «{mine.title}» и «{other.title}»", community_id, job)

    def cached_report(self, vk_id: int) -> Optional[Report]:
        cached = self._reports.get(int(vk_id))
        return cached[1] if cached and time.monotonic() - cached[0] < REPORT_CACHE_SEC else None

    async def _report(self, community: Community, fresh: bool = False) -> Report:
        cached = None if fresh else self.cached_report(community.vk_id)
        if cached:
            return cached
        report = await self.analytics_service.report(community, self.config.charts_dir)
        self._reports[community.vk_id] = (time.monotonic(), report)
        return report

    def apply_schedule(self, community_id: int, hours: List[int], days: List[int]) -> Community:
        self.community(community_id)
        return self.store.update_community(community_id, post_hours=hours, post_days=days)

    def reset_schedule(self, community_id: int) -> Community:
        self.community(community_id)
        return self.store.update_community(community_id, post_hours="", post_days="")

    # Personal page

    async def repost(self, community_id: int, count: int) -> int:
        community = self.community(community_id)
        return await self._job(
            "repost", f"делает репосты из «{community.title}»", community_id,
            lambda: self.account.repost(community_id, count),
        )

    # VK access

    async def whoami(self) -> str:
        return await self.account.name()

    async def set_token(self, token: str) -> str:
        previous = self.vk.token
        self.vk.set_token(token)
        try:
            name = await self.account.name()
            granted = int(await self.vk.call("account.getAppPermissions") or 0)
        except VkError as exc:
            self.vk.set_token(previous)
            raise UserError("ВК не принял этот доступ. Попробуйте получить ссылку ещё раз.") from exc
        missing = [title for title, bit in REQUIRED_PERMISSIONS.items() if not granted & bit]
        if missing:
            self.vk.set_token(previous)
            raise UserError("При входе не выданы нужные права: " + ", ".join(missing) + ". Разрешите всё и повторите.")
        self.store.set_setting("vk_token", token)
        return name

    async def _job(self, kind: str, title: str, community_id: Optional[int], factory: Callable[[], Awaitable[T]]) -> T:
        if self._lock.locked():
            raise BusyError(f"Бот сейчас занят: {self.current_task}. Попробуйте через пару минут.")
        async with self._lock:
            self.current_task = title
            job_id = self.store.start_job(kind, community_id)
            try:
                result = await factory()
            except Exception as exc:
                self.store.finish_job(job_id, "error", error=str(exc))
                raise
            finally:
                self.current_task = ""
            try:
                self.store.finish_job(job_id, "ok", stats=_jsonable(result))
            except Exception:
                # The work is done; a broken history record must not turn it into an error for the user.
                logger.exception("Cannot record %s job %s", kind, job_id)
            return result


def _jsonable(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, Comparison):
        return {"mine": _jsonable(value.mine), "other": _jsonable(value.other)}
    if is_dataclass(value):
        data = _plain(value)
        data.pop("community", None)
        data.pop("charts", None)
        if isinstance(data.get("posts"), dict):
            data["posts"].pop("series", None)
        return data
    return {"result": value} if value is not None else None


def _plain(value: Any) -> Any:
    """Like dataclasses.asdict, but Counter and other dicts keep their keys (as strings)."""
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(item) for item in value]
    return value
