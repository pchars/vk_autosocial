import time
from typing import Dict, List, Optional, Set, Union

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from utils.logger import get_logger
from utils.store import Store

logger = get_logger(__name__)

REQUEST_COOLDOWN_SEC = 60 * 60
MAX_REQUESTS_PER_HOUR = 10
STRANGER_REPLY_COOLDOWN_SEC = 5 * 60


class Access:
    """Owners come from TELEGRAM_ADMIN_IDS; helpers are approved by an owner from the chat."""

    def __init__(self, owner_ids: List[int], store: Store):
        self.owner_ids: Set[int] = set(owner_ids)
        self.store = store
        self._requests: Dict[int, float] = {}
        self._replies: Dict[int, float] = {}
        self.pending_names: Dict[int, str] = {}

    def admin_ids(self) -> Set[int]:
        return self.owner_ids | {admin["tg_id"] for admin in self.store.list_admins()}

    def is_admin(self, tg_id: int) -> bool:
        return tg_id in self.admin_ids()

    def is_owner(self, tg_id: int) -> bool:
        return tg_id in self.owner_ids

    def should_answer_stranger(self, tg_id: int, now: float = None) -> bool:
        return _allow(self._replies, tg_id, STRANGER_REPLY_COOLDOWN_SEC, None, now)

    def should_forward_request(self, tg_id: int, now: float = None) -> bool:
        """One request per person per hour and a few per hour overall, so strangers cannot flood owners."""
        return _allow(self._requests, tg_id, REQUEST_COOLDOWN_SEC, MAX_REQUESTS_PER_HOUR, now)


def _allow(history: Dict[int, float], tg_id: int, cooldown: float, limit: Optional[int], now: float = None) -> bool:
    now = time.time() if now is None else now
    for key in [k for k, moment in history.items() if now - moment >= cooldown]:
        del history[key]
    if tg_id in history or (limit is not None and len(history) >= limit):
        return False
    history[tg_id] = now
    return True


class AdminFilter(BaseFilter):
    def __init__(self, access: Access):
        self.access = access

    async def __call__(self, event: Union[Message, CallbackQuery]) -> bool:
        return bool(event.from_user) and self.access.is_admin(event.from_user.id)


class Notifier:
    def __init__(self, bot: Bot, access: Access):
        self.bot = bot
        self.access = access

    async def send(self, text: str, silent: bool = False, owners_only: bool = False, **kwargs) -> None:
        targets = self.access.owner_ids if owners_only else self.access.admin_ids()
        for tg_id in targets:
            try:
                await self.bot.send_message(tg_id, text, disable_notification=silent, **kwargs)
            except TelegramAPIError as exc:
                logger.warning("Cannot notify %s: %s", tg_id, exc)
