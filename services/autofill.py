import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from api.vk import VkAuthError, VkError, VkFloodError
from utils.dates import human_date, plural
from utils.logger import get_logger

from .engine import BusyError, Engine, UserError
from .vk_actions import flood_text, vk_action

logger = get_logger(__name__)

Notify = Callable[..., Awaitable[None]]
REPEAT_NOTICE_SEC = 12 * 60 * 60
LOW_QUEUE_DAYS = 7
LOW_QUEUE_CHECK_SEC = 24 * 60 * 60
ACCESS_CHECK_SEC = 12 * 60 * 60
AUTH_LOST = (
    "ВК больше не пускает бота: доступ к аккаунту устарел или отозван. "
    "Отложка не пополняется. Владелец бота может обновить доступ: "
    "«Моя страница ВК» → «Обновить доступ к ВК»."
)
FIRST_RUN_DELAY_SEC = 60
CONTINUE_DELAY_SEC = 60


def needs_refill(queued: int, target: int, posts_per_day: int) -> bool:
    """Refill once at least a day of posts is missing, not after every published post."""
    return queued <= target - max(1, posts_per_day)


class AutoFiller:
    """Keeps every community's postponed queue topped up without anyone asking.

    Communities with autofill off are only watched once a day: the owners hear when their queue is about
    to run out. VK access is checked twice a day even when nothing is filled.
    """

    def __init__(self, engine: Engine, notify: Notify, interval_sec: int, clock: Callable[[], float] = time.time,
                 low_queue_buttons: Optional[Callable[[int], Any]] = None):
        self.engine = engine
        self.notify = notify
        self.interval_sec = interval_sec
        self.clock = clock
        self.low_queue_buttons = low_queue_buttons
        self._notices: Dict[Tuple[int, str], float] = {}
        self._added: Dict[int, int] = {}
        self._watched: Dict[int, float] = {}
        # The bot checks access on start, so the first scheduled check is half a day later.
        self._access_checked = clock()

    async def run(self) -> None:
        await asyncio.sleep(FIRST_RUN_DELAY_SEC)
        while True:
            more = False
            try:
                more = await self.tick()
            except Exception:
                logger.exception("Autofill tick crashed")
            await asyncio.sleep(CONTINUE_DELAY_SEC if more else self.interval_sec)

    async def tick(self) -> bool:
        """Top up every community once. Returns True if some queue still needs another run."""
        tz = self.engine.config.timezone
        more = False
        if not await self._check_access():
            return False
        for community in self.engine.store.list_communities():
            if self.engine.busy:
                return True
            if not community.autofill and not self._watch_due(community.vk_id):
                continue
            try:
                queue = await self.engine.queue(community.vk_id)
                if not community.autofill:
                    self._watched[community.vk_id] = self.clock()
                    await self._check_low_queue(community, queue)
                    continue
                if not needs_refill(queue.count, community.queue_target, community.posts_per_day):
                    continue
                result = await self.engine.fill(community.vk_id)
            except BusyError:
                return True
            except VkAuthError:
                await self._warn(0, "auth", AUTH_LOST)
                return False
            except VkFloodError as exc:
                await self._warn(community.vk_id, "flood", (
                    f"Не смог пополнить «{community.title}». {flood_text(exc, by_bot=True)}"
                ))
                continue
            except UserError as exc:
                await self._warn(community.vk_id, "user", f"Не могу пополнить «{community.title}»: {exc}")
                continue
            except VkError as exc:
                logger.warning("Autofill failed for %s: %s", community.vk_id, exc)
                problem = ("ВК не отвечает" if exc.code == -1
                           else f"ВК не выполнил {vk_action(exc.method)} и ответил ошибкой: {exc.message}")
                await self._warn(community.vk_id, "vk", (
                    f"Не получилось пополнить «{community.title}»: {problem}. Попробую снова позже сам."
                ))
                continue
            except Exception:
                logger.exception("Autofill failed for %s", community.vk_id)
                await self._warn(community.vk_id, "crash", (
                    f"Не получилось пополнить «{community.title}» из-за ошибки. Подробности в логе, попробую позже."
                ))
                continue

            created = result.published.posts_created
            added = self._added.pop(community.vk_id, 0) + created
            stopped = result.published.denied or result.published.throttled
            if result.remaining and created and not stopped:
                self._added[community.vk_id] = added
                more = True
                continue
            if added:
                until = human_date(result.published.last_date, tz) if result.published.last_date else ""
                await self.notify(
                    f"«{community.title}»: добавил в отложку {plural(added, 'пост', 'поста', 'постов')}"
                    + (f", теперь посты запланированы до {until}." if until else "."),
                    silent=True,
                )
            if result.published.denied:
                await self._warn(community.vk_id, "denied", (
                    f"ВК не даёт ставить новые посты в «{community.title}»: обычно это суточный лимит. "
                    "Продолжу позже сам."
                ))
            elif result.published.throttled:
                logger.info("Autofill for %s paused: VK slows down uploads", community.vk_id)
            elif created < result.needed and self.engine.stock(community.vk_id) < community.photos_per_post:
                await self._warn(community.vk_id, "short", (
                    f"«{community.title}»: нужно было {result.needed}, получилось {created}. "
                    "В источниках мало новых картинок — добавьте ещё групп-источников."
                ))
        return more

    def _watch_due(self, community_id: int) -> bool:
        last = self._watched.get(community_id)
        return last is None or self.clock() - last >= LOW_QUEUE_CHECK_SEC

    async def _check_access(self) -> bool:
        if self.clock() - self._access_checked < ACCESS_CHECK_SEC:
            return True
        self._access_checked = self.clock()
        try:
            await self.engine.whoami()
        except VkAuthError:
            await self._warn(0, "auth", AUTH_LOST)
            return False
        except Exception:
            logger.exception("VK access check failed")
        return True

    async def _check_low_queue(self, community, queue) -> None:
        now = self.clock()
        if queue.count and queue.last_date and queue.last_date - now >= LOW_QUEUE_DAYS * 86400:
            return
        if queue.count:
            until = human_date(queue.last_date, self.engine.config.timezone) if queue.last_date else ""
            text = (f"«{community.title}»: в отложке осталось {plural(queue.count, 'пост', 'поста', 'постов')}"
                    + (f", их хватит до {until}." if until else "."))
        else:
            text = f"«{community.title}»: отложка пустая, новые посты не выходят."
        text += " Автопополнение выключено: пополните отложку сами или включите его в карточке группы."
        buttons = self.low_queue_buttons(community.vk_id) if self.low_queue_buttons else None
        await self.notify(text, **({"reply_markup": buttons} if buttons is not None else {}))

    async def _warn(self, community_id: int, kind: str, text: str) -> None:
        key = (community_id, kind)
        now = self.clock()
        last: Optional[float] = self._notices.get(key)
        if last is not None and now - last < REPEAT_NOTICE_SEC:
            return
        self._notices[key] = now
        await self.notify(text)
