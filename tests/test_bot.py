import itertools
import time
import unittest
from datetime import datetime
from typing import Any, AsyncGenerator, List, Optional

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import (
    AnswerCallbackQuery, DeleteMessage, EditMessageText, SendMediaGroup, SendMessage, TelegramMethod,
)
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, ReplyKeyboardMarkup, Update, User

from api.vk import VkAuthError, VkError, VkFloodError
from bot import app as app_module
from bot import texts
from bot.access import Access, Notifier
from bot.handlers import build_router
from bot.keyboards import Account, Group
from services.analytics import Comparison, MemberStats, PostStats, Recommendation, Report
from services.collector import CollectStats
from services.engine import Engine, FillResult, UserError
from services.publisher import PublishStats, QueueInfo
from utils.store import Community, Source

from tests.fakes import AsyncTempDirTestCase, FakeVk, make_jpeg

OWNER = 100
HELPER = 200
STRANGER = 300


class RecordingSession(BaseSession):
    """Answers every Bot API call locally and remembers what the bot tried to send."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: List[TelegramMethod[Any]] = []
        self.refuse_edits = False

    async def close(self) -> None:
        return None

    async def stream_content(self, *args: Any, **kwargs: Any) -> AsyncGenerator[bytes, None]:
        yield b""

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: Optional[int] = None) -> Any:
        self.requests.append(method)
        if isinstance(method, EditMessageText) and self.refuse_edits:
            raise TelegramBadRequest(method=method, message="Bad Request: message can't be edited")
        if isinstance(method, (SendMessage, EditMessageText)):
            return Message(
                message_id=len(self.requests),
                date=datetime.now(),
                chat=Chat(id=int(method.chat_id or 1), type="private"),
                text=method.text,
            )
        if isinstance(method, SendMediaGroup):
            return []
        return True


def group_answer(vk_id: int, name: str, is_admin: int = 1, level: int = 3, closed: int = 0) -> dict:
    return {"groups": [{
        "id": vk_id, "name": name, "screen_name": f"club{vk_id}",
        "is_admin": is_admin, "admin_level": level, "is_closed": closed, "is_member": 0,
    }]}


class BotTests(AsyncTempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.groups = {
            "flowers": group_answer(5, "Цветы"),
            "10": group_answer(10, "Источник", is_admin=0),
            "11": group_answer(11, "Закрытая", is_admin=0, closed=1),
            "alien": group_answer(7, "Чужая", is_admin=0),
        }
        self.vk = FakeVk({
            "groups.getById": self.group_by_id,
            "wall.get": {"count": 2, "items": [{"id": 1, "date": 1_900_000_000}, {"id": 2, "date": 1_900_043_200}]},
            "users.get": [{"first_name": "Аня", "last_name": "К"}],
        })
        self.engine = Engine(self.config, self.store, self.vk)
        self.session = RecordingSession()
        self.bot = Bot("42:TEST", session=self.session)
        self.access = Access([OWNER], self.store)
        self.dp = Dispatcher(storage=MemoryStorage())
        self.dp.include_router(build_router(self.engine, self.access, Notifier(self.bot, self.access)))
        self.ids = itertools.count(1)

    def group_by_id(self, group_id, fields=""):
        key = str(group_id)
        found = self.groups.get(key) or next(
            (answer for answer in self.groups.values() if str(answer["groups"][0]["id"]) == key), None)
        return found or VkError(100, "no group")

    def user(self, tg_id: int) -> User:
        return User(id=tg_id, is_bot=False, first_name=f"User{tg_id}", username=f"u{tg_id}")

    async def send(self, text: str, tg_id: int = OWNER) -> None:
        n = next(self.ids)
        message = Message(message_id=n, date=datetime.now(), chat=Chat(id=tg_id, type="private"),
                          from_user=self.user(tg_id), text=text)
        await self.dp.feed_update(self.bot, Update(update_id=n, message=message))

    async def press(self, data: CallbackData, tg_id: int = OWNER) -> None:
        n = next(self.ids)
        message = Message(message_id=n, date=datetime.now(), chat=Chat(id=tg_id, type="private"), text="old")
        callback = CallbackQuery(id=str(n), from_user=self.user(tg_id), chat_instance="chat",
                                 data=data.pack(), message=message)
        await self.dp.feed_update(self.bot, Update(update_id=n, callback_query=callback))

    def sent(self, chat_id: Optional[int] = None) -> List[TelegramMethod[Any]]:
        return [r for r in self.session.requests if isinstance(r, (SendMessage, EditMessageText))
                and (chat_id is None or int(r.chat_id or 0) == chat_id)]

    def last_text(self) -> str:
        return self.sent()[-1].text

    def buttons(self) -> List[str]:
        markup = self.sent()[-1].reply_markup
        if isinstance(markup, InlineKeyboardMarkup):
            return [b.text for row in markup.inline_keyboard for b in row]
        if isinstance(markup, ReplyKeyboardMarkup):
            return [b.text for row in markup.keyboard for b in row]
        return []

    def add_group(self) -> None:
        self.store.add_community(5, "Цветы", "https://vk.com/flowers")

    # Menu and access

    async def test_start_shows_menu(self):
        await self.send("/start")
        self.assertEqual(self.last_text(), texts.WELCOME)
        self.assertEqual(self.buttons(), [texts.MENU_GROUPS, texts.MENU_ADD, texts.MENU_ACCOUNT, texts.MENU_HELP])
        await self.send(texts.MENU_HELP)
        self.assertEqual(self.last_text(), texts.HELP)

    async def test_unknown_text_points_to_buttons(self):
        await self.send("привет")
        self.assertEqual(self.last_text(), texts.UNKNOWN_TEXT)

    async def test_stranger_asks_owner_and_gets_access(self):
        await self.send("привет", tg_id=STRANGER)
        self.assertIn(str(STRANGER), self.sent(STRANGER)[-1].text)
        request = self.sent(OWNER)[-1]
        self.assertIn("просит доступ", request.text)
        await self.send("ещё раз", tg_id=STRANGER)
        self.assertEqual(len(self.sent(OWNER)), 1)
        self.assertEqual(len(self.sent(STRANGER)), 1)

        await self.press(Account(action="approve", arg=str(STRANGER)), tg_id=HELPER)
        self.assertFalse(self.access.is_admin(STRANGER))
        await self.press(Account(action="approve", arg=str(STRANGER)))
        self.assertTrue(self.access.is_admin(STRANGER))
        self.assertIn("открыли доступ", self.sent(STRANGER)[-1].text)
        self.assertEqual(self.store.list_admins()[0]["name"], f"User{STRANGER} (@u{STRANGER})")

        await self.send("/start", tg_id=STRANGER)
        self.assertEqual(self.last_text(), texts.WELCOME)

        await self.press(Account(action="revoke", arg=str(STRANGER)))
        self.assertFalse(self.access.is_admin(STRANGER))

    async def test_stranger_buttons_and_deny(self):
        await self.press(Group(action="open", cid=5), tg_id=STRANGER)
        answer = self.session.requests[-1]
        self.assertIsInstance(answer, AnswerCallbackQuery)
        self.assertTrue(answer.show_alert)
        self.access.pending_names[STRANGER] = "x"
        await self.press(Account(action="deny", arg=str(STRANGER)))
        self.assertEqual(self.access.pending_names, {})
        self.assertEqual(self.last_text(), "Запрос отклонён.")

    async def test_helper_cannot_manage_helpers(self):
        self.store.add_admin(HELPER, "Помощник")
        await self.press(Account(action="admins"), tg_id=HELPER)
        self.assertTrue(self.session.requests[-1].show_alert)
        await self.send(texts.MENU_ACCOUNT, tg_id=HELPER)
        self.assertEqual(self.buttons(), ["Репост из группы на мою стену"])
        await self.press(Account(action="token"), tg_id=HELPER)
        self.assertTrue(self.session.requests[-1].show_alert)
        self.assertEqual(self.store.get_setting("vk_token"), "")
        await self.send(texts.MENU_ACCOUNT)
        self.assertIn("Аня К", self.last_text())
        self.assertIn("Помощники", self.buttons())
        await self.press(Account(action="admins"))
        self.assertIn("Помощник", self.last_text())

    # Adding a group

    async def test_add_group_wizard(self):
        await self.send(texts.MENU_ADD)
        self.assertEqual(self.last_text(), texts.ASK_GROUP_LINK)
        await self.send("https://vk.com/alien")
        self.assertIn("нет прав редактора", self.sent()[-2].text)
        await self.send("https://vk.com/flowers")
        self.assertIn("«Цветы» подключена", self.last_text())

        await self.send("vk.com/club11")
        self.assertIn("закрытая группа", self.last_text())
        await self.send("https://vk.com/club10 https://vk.com/club404")
        text = self.last_text()
        self.assertIn("Добавил: «Источник»", text)
        self.assertIn("такой группы ВК нет", text)
        self.assertIn("включите автопополнение", text)
        self.assertEqual(self.buttons(), ["Пополнить отложку сейчас", "Включить автопополнение", "Открыть группу"])
        self.assertEqual([s.vk_id for s in self.store.list_sources(5)], [10])
        self.assertFalse(self.store.get_community(5).autofill)

        await self.send("привет")
        self.assertEqual(self.last_text(), texts.UNKNOWN_TEXT)

    async def test_menu_button_interrupts_wizard_and_cancel_works(self):
        await self.send(texts.MENU_ADD)
        await self.send(texts.MENU_GROUPS)
        self.assertIn("нет ни одной группы", self.last_text())
        await self.send("https://vk.com/flowers")
        self.assertEqual(self.last_text(), "Что сделать с этой ссылкой?")
        await self.send(texts.MENU_ADD)
        await self.press(Account(action="cancel"))
        self.assertEqual(self.last_text(), texts.CANCELLED)
        await self.send("https://vk.com/flowers")
        self.assertIsNone(self.store.get_community(5))

    async def test_link_sent_as_plain_text(self):
        await self.send("смотри https://vk.com/flowers")
        self.assertEqual(self.buttons(), ["Посмотреть аналитику", "Это моя группа — подключить"])
        await self.press(Account(action="link_group"))
        self.assertIn("«Цветы» подключена", self.last_text())
        await self.send(texts.MENU_GROUPS)

        await self.send("https://vk.com/club10")
        self.assertIn("Источник для «Цветы»", self.buttons())
        await self.press(Group(action="link_src", cid=5))
        self.assertEqual(self.last_text(), "Добавил: «Источник».")

    # Group card and settings

    async def test_group_card(self):
        self.add_group()
        await self.send(texts.MENU_GROUPS)
        text = self.last_text()
        self.assertIn("В отложке: 2 поста", text)
        self.assertIn("Автопополнение выключено", text)
        self.store.update_community(5, autofill=True)
        await self.send(texts.MENU_GROUPS)
        self.assertIn("держу 270 постов (3 в день на 90 дней)", self.last_text())
        self.assertIn("Добавьте источники", text)

        self.vk.handlers["wall.get"] = VkError(-1, "network")
        await self.press(Group(action="open", cid=5))
        self.assertIn("не удалось узнать", self.last_text())

        self.store.add_community(6, "Вторая")
        await self.send(texts.MENU_GROUPS)
        self.assertCountEqual(self.buttons(), ["Вторая", "Цветы"])

    async def test_settings(self):
        self.add_group()
        await self.press(Group(action="settings", cid=5))
        self.assertIn("Запас: на 90 дней (это 270 постов)", self.last_text())
        await self.press(Group(action="pick", cid=5, arg="queue_days"))
        self.assertIn("[90]", self.buttons())
        await self.press(Group(action="set", cid=5, arg="queue_days=30"))
        self.assertEqual(self.store.get_community(5).queue_days, 30)
        self.assertIn("на 30 дней (это 90 постов)", self.last_text())

        await self.press(Group(action="set", cid=5, arg="chat_every=5"))
        self.assertIn("Ссылку на чат", self.sent()[-2].text)

        self.assertFalse(self.store.get_community(5).autofill)
        await self.press(Group(action="autofill", cid=5))
        self.assertTrue(self.store.get_community(5).autofill)
        self.assertIn("Выключить автопополнение", self.buttons())
        await self.press(Group(action="autofill", cid=5))
        self.assertFalse(self.store.get_community(5).autofill)
        self.assertIn("Включить автопополнение", self.buttons())

        self.assertIn("Подписи к постам: без подписи, только хэштеги", self.last_text())
        await self.press(Group(action="quotes", cid=5))
        self.assertTrue(self.store.get_community(5).use_quotes)
        self.assertIn("Подписи к постам: фразы из базы", self.last_text())
        self.assertIn("Убрать подписи к постам", self.buttons())
        await self.press(Group(action="quotes", cid=5))
        self.assertFalse(self.store.get_community(5).use_quotes)

    async def test_text_settings(self):
        self.add_group()
        await self.press(Group(action="text", cid=5, arg="hashtags"))
        self.assertEqual(self.last_text(), texts.TEXT_FIELDS["hashtags"][1])
        await self.send("  #цветы #весна ")
        self.assertEqual(self.store.get_community(5).hashtags, "#цветы #весна")
        self.assertIn("Сохранил", self.last_text())
        await self.press(Group(action="text", cid=5, arg="hashtags"))
        await self.send("-")
        self.assertEqual(self.store.get_community(5).hashtags, "")

    async def test_sources_screen(self):
        self.add_group()
        self.store.add_source(5, 10, "Источник", "https://vk.com/club10")
        await self.press(Group(action="sources", cid=5))
        self.assertIn("1. Источник", self.last_text())
        await self.press(Group(action="src_del", cid=5, arg="10"))
        self.assertEqual(self.store.list_sources(5), [])
        self.assertIn("пока нет источников", self.last_text())
        await self.press(Group(action="src_add", cid=5))
        await self.send("club10")
        self.assertIn("Добавил", self.last_text())
        self.assertIn("Убрать: Источник", self.buttons())

    async def test_edit_falls_back_to_new_message(self):
        self.add_group()
        self.session.refuse_edits = True
        await self.press(Group(action="settings", cid=5))
        self.assertIsInstance(self.sent()[-1], SendMessage)
        self.assertIn("Настройки «Цветы»", self.last_text())

    # Actions

    async def test_fill_without_sources_explains(self):
        self.add_group()
        await self.press(Group(action="fill", cid=5))
        self.assertIn("Пополняю «Цветы»", self.sent()[-2].text)
        self.assertIn("нет источников", self.last_text())

    async def test_fill_reports_result(self):
        self.add_group()
        self.store.add_source(5, 10)
        self.store.update_community(5, queue_days=1)
        self.vk.handlers["wall.get"] = {"count": 3, "items": []}
        await self.press(Group(action="fill", cid=5))
        self.assertIn("отложка уже полная", self.last_text())

    async def test_clear_needs_double_confirmation_for_published(self):
        self.add_group()
        self.vk.handlers["wall.get"] = lambda **p: {"items": []}
        await self.press(Group(action="clear", cid=5))
        self.assertIn("Удалить ВСЕ опубликованные посты", self.buttons())
        await self.press(Group(action="clear_ok", cid=5, arg="published"))
        self.assertIn("Точно удалить ВСЕ", self.last_text())
        self.assertNotIn("wall.get", self.vk.methods())
        await self.press(Group(action="clear_ok", cid=5, arg="published!"))
        self.assertEqual(self.vk.params("wall.get")[-1]["filter"], "owner")
        self.assertIn("Удалил 0 постов", self.last_text())

    async def test_delete_group(self):
        self.add_group()
        folder = self.engine.folder(5)
        folder.mkdir(parents=True)
        await self.press(Group(action="delete", cid=5))
        self.assertIn("Отключить «Цветы»", self.last_text())
        await self.press(Group(action="delete_ok", cid=5))
        self.assertIsNone(self.store.get_community(5))
        self.assertFalse(folder.exists())

    async def test_vanished_group_is_explained_by_error_handler(self):
        await self.press(Group(action="settings", cid=404))
        self.assertEqual(self.last_text(), "Эта группа уже удалена из бота.")

    async def test_account_menu_has_only_working_actions(self):
        await self.send(texts.MENU_ACCOUNT)
        self.assertEqual(self.buttons(), ["Репост из группы на мою стену", "Обновить доступ к ВК", "Помощники"])

    async def test_repost(self):
        self.add_group()
        self.vk.handlers["wall.get"] = {"count": 1, "items": [{"id": 3}]}
        self.vk.handlers["wall.repost"] = {"success": 1}
        self.engine.account.sleep = _no_sleep
        await self.press(Account(action="repost"))
        self.assertEqual(self.buttons(), ["1", "3", "5", "« Назад"])
        await self.press(Group(action="repost", cid=5, arg="1"))
        self.assertIn("Репостнул 1 пост", self.last_text())

        self.store.add_community(6, "Вторая")
        await self.press(Account(action="repost"))
        self.assertEqual(self.last_text(), "Из какой группы делать репост?")
        await self.press(Group(action="repost_group", cid=6))
        self.assertIn("Сколько последних постов", self.last_text())

    async def test_repost_without_groups(self):
        await self.press(Account(action="repost"))
        self.assertIn("Сначала подключите группу", self.last_text())

    async def test_token_update(self):
        self.vk.handlers["account.getAppPermissions"] = 4 | 8192 | 262144
        await self.press(Account(action="token"))
        self.assertEqual(self.last_text(), texts.ASK_TOKEN)
        self.assertEqual(self.buttons(), ["Открыть ВК", "Отмена"])
        await self.send("не то")
        self.assertIsInstance(self.session.requests[-2], DeleteMessage)
        self.assertIn("access_token=", self.last_text())
        await self.send("https://phishing.example/blank.html#access_token=vk1.a.other")
        self.assertIn("access_token=", self.last_text())
        self.assertEqual(self.store.get_setting("vk_token"), "")
        await self.send("https://oauth.vk.com/blank.html#access_token=vk1.a.secret&expires_in=0&user_id=1")
        self.assertIn("Доступ к ВК обновлён. Аккаунт: Аня К.", self.last_text())
        self.assertEqual(self.store.get_setting("vk_token"), "vk1.a.secret")
        self.assertNotIn("vk1.a.secret", " ".join(m.text for m in self.sent()))


    async def test_stories_and_analytics(self):
        self.add_group()
        folder = self.engine.folder(5)
        folder.mkdir(parents=True)
        (folder / "a.jpg").write_bytes(make_jpeg(1))
        self.vk.handlers.update({"stories.getPhotoUploadServer": {"upload_url": "s"}, "stories.save": 1})
        await self.press(Group(action="stories", cid=5))
        self.assertEqual(self.buttons(), ["1", "3", "5", "« Назад"])
        await self.press(Group(action="stories_go", cid=5, arg="1"))
        self.assertEqual(self.last_text(), "Опубликовал 1 сторис.")

        member = {"id": 1, "sex": 1, "bdate": "1.1.2000", "last_seen": {"time": int(time.time())}}
        self.groups["flowers"]["groups"][0]["members_count"] = 2
        self.vk.handlers.update({
            "groups.getMembers": lambda offset=0, **_p: {"count": 2, "items": [member, {"id": 2}] if offset == 0 else []},
            "wall.get": {"count": 1, "items": [{"date": int(time.time()), "views": {"count": 10},
                                                "likes": {"count": 2}, "reposts": {"count": 1}}]},
        })
        await self.press(Group(action="analytics", cid=5))
        self.assertIn("Аналитика «Цветы»", self.last_text())
        self.assertIn("Участников: 2", self.last_text())
        self.assertEqual(self.buttons(), ["Сравнить с другой группой", "« К группе"])
        self.assertTrue(any(isinstance(r, SendMediaGroup) for r in self.session.requests))
        self.assertEqual(self.store.last_job(5)["status"], "ok")

        self.vk.handlers["groups.getMembers"] = VkAuthError(5, "expired")
        await self.press(Group(action="analytics", cid=5))
        self.assertIn("Обновить доступ к ВК", self.last_text())

    async def test_foreign_group_analytics_compare_and_schedule(self):
        self.add_group()
        seen = int(time.time()) - 2 * 86400
        self.vk.handlers["groups.getMembers"] = {"items": [{"id": 1, "sex": 1, "last_seen": {"time": seen}}]}

        await self.send("https://vk.com/alien")
        await self.press(Account(action="link_stats"))
        self.assertIn("Аналитика «Чужая»", self.last_text())
        self.assertIn("Лучшее время для постов у этой группы", self.last_text())
        self.assertEqual(self.buttons(), ["Сравнить с «Цветы»"])
        self.assertIsNone(self.store.get_community(7))

        await self.press(Group(action="compare", cid=5, arg="7"))
        self.assertIn("Сравнение: «Цветы» и «Чужая»", self.last_text())
        self.assertEqual(self.buttons(), ["Сравнить с другой группой", "« К группе"])

        await self.press(Group(action="analytics", cid=5))
        self.assertEqual(self.buttons()[0], "Применить рекомендации")
        rec = self.engine.cached_report(5).recommendation
        await self.press(Group(action="sched_ask", cid=5))
        self.assertIn("Публиковать посты в «Цветы»", self.last_text())
        self.assertEqual(self.buttons(), ["Да, применить", "« Нет, оставить как есть"])
        await self.press(Group(action="sched_ok", cid=5))
        self.assertIn("Готово! Новые посты будут выходить", self.last_text())
        self.assertEqual(self.store.get_community(5).hours, rec.hours)
        self.assertIn("Публиковать равномерно, каждый день", self.buttons())

        await self.press(Group(action="analytics", cid=5))
        self.assertEqual(self.buttons(), ["Сравнить с другой группой", "« К группе"])

        await self.press(Group(action="sched_reset", cid=5))
        self.assertEqual(self.store.get_community(5).hours, [])
        self.assertNotIn("Публиковать равномерно, каждый день", self.buttons())

        self.engine._reports.clear()
        await self.press(Group(action="sched_ok", cid=5))
        await self.press(Group(action="sched_ask", cid=5))
        alerts = [r.text for r in self.session.requests if isinstance(r, AnswerCallbackQuery) and r.text]
        self.assertEqual(alerts[-2:], ["Рекомендации устарели — откройте аналитику ещё раз."] * 2)
        self.assertEqual(self.store.get_community(5).hours, [])

    async def test_compare_with_link(self):
        self.add_group()
        self.vk.handlers["groups.getMembers"] = {"items": []}
        await self.press(Group(action="compare_ask", cid=5))
        self.assertIn("с которой сравнить «Цветы»", self.last_text())
        await self.send("https://vk.com/nope")
        self.assertIn("Пришлите другую ссылку", self.last_text())
        await self.send("https://vk.com/flowers")
        self.assertIn("та же самая группа", self.sent()[-1].text)
        await self.press(Group(action="compare_ask", cid=5))
        await self.send("https://vk.com/alien")
        self.assertIn("Сравнение: «Цветы» и «Чужая»", self.last_text())

    async def test_vk_access_check_on_start(self):
        await app_module._check_vk_access(self.engine, Notifier(self.bot, self.access))
        self.assertEqual(self.sent(), [])
        self.vk.handlers["users.get"] = VkAuthError(5, "expired")
        await app_module._check_vk_access(self.engine, Notifier(self.bot, self.access))
        self.assertIn("Обновить доступ", self.sent(OWNER)[-1].text)
        self.vk.token = ""
        await app_module._check_vk_access(self.engine, Notifier(self.bot, self.access))
        self.assertIn("доступ к ВК не настроен", self.sent(OWNER)[-1].text)


class AccessLimitsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.access = Access([OWNER], store=None)

    def test_strangers_cannot_flood_owners(self):
        allowed = [self.access.should_forward_request(1000 + i, now=0) for i in range(15)]
        self.assertEqual(allowed.count(True), 10)
        self.assertFalse(self.access.should_forward_request(1000, now=60))
        self.assertTrue(self.access.should_forward_request(5000, now=3600))
        self.assertEqual(len(self.access._requests), 1)

    def test_stranger_replies_are_throttled(self):
        self.assertTrue(self.access.should_answer_stranger(1, now=0))
        self.assertFalse(self.access.should_answer_stranger(1, now=299))
        self.assertTrue(self.access.should_answer_stranger(1, now=300))


class TextsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.community = Community(vk_id=5, name="Цветы", autofill=True)

    def test_fill_result_variants(self):
        full = FillResult(queued_before=270, needed=0)
        self.assertIn("отложка уже полная: 270 постов", texts.fill_result(self.community, full, "UTC"))

        part = FillResult(needed=50, remaining=220, published=PublishStats(posts_created=50, last_date=60))
        text = texts.fill_result(self.community, part, "UTC")
        self.assertIn("добавил в отложку 50 постов", text)
        self.assertIn("Посты запланированы до 1 января в 00:01", text)
        self.assertIn("ещё 220 постов — сделаю это сам", text)
        manual = Community(vk_id=5, name="Цветы", autofill=False)
        self.assertIn("нажмите «Пополнить» ещё раз", texts.fill_result(manual, part, "UTC"))

        short = FillResult(needed=5, collected=CollectStats(failed_sources=["Закрытая"]),
                           published=PublishStats(posts_created=2, upload_failed=3))
        text = texts.fill_result(self.community, short, "UTC")
        self.assertIn("Хотел добавить 5", text)
        self.assertIn("Не открылись источники: Закрытая", text)
        self.assertIn("ВК не принял 3 картинки", text)

        denied = FillResult(needed=5, published=PublishStats(posts_created=1, denied=True))
        text = texts.fill_result(self.community, denied, "UTC")
        self.assertIn("суточный лимит", text)
        self.assertNotIn("не хватило картинок", text)

        throttled = FillResult(needed=50, remaining=10, published=PublishStats(
            posts_created=3, upload_failed=10, throttled=True))
        text = texts.fill_result(manual, throttled, "UTC")
        self.assertIn("притормаживать загрузку", text)
        self.assertIn("минут через 15–20", text)
        self.assertNotIn("ВК не принял", text)
        self.assertIn("Продолжу позже сам", texts.fill_result(self.community, throttled, "UTC"))

    def test_explain_error(self):
        cases = [
            (UserError("своё"), "своё"),
            (VkFloodError(14, "captcha"), "капчу"),
            (VkFloodError(9, "flood", retry_after=120), "через 2 мин"),
            (VkFloodError(9, "flood", "photos.saveWallPhoto", retry_after=3600),
             "ВК временно ограничил загрузку картинок в посты: слишком много однотипных действий подряд"),
            (VkFloodError(9, "flood", "photos.saveWallPhoto", retry_after=3600), "Пополнить отложку пока не"),
            (VkFloodError(9, "flood", "photos.saveWallPhoto", retry_after=3600), "примерно через 1 ч."),
            (VkFloodError(29, "limit", "wall.get", retry_after=5400), "через 1 ч 30 мин"),
            (VkFloodError(29, "limit", "wall.get"), "суточный лимит ВК"),
            (VkError(214, "denied"), "суточный лимит"),
            (VkError(3, "Unknown method passed", "wall.repost"), "ВК отключил репосты"),
            (VkError(15, "denied", "groups.getMembers"), "ВК не разрешил чтение участников групп"),
            (VkError(-1, "network"), "Не удалось связаться"),
            (VkError(100, "странно", "wall.post"), "ВК не выполнил публикацию постов и ответил ошибкой: странно"),
            (VkError(100, "странно"), "ВК не выполнил запросы бота к ВК"),
            (RuntimeError("x"), "Что-то пошло не так"),
        ]
        for exc, expected in cases:
            with self.subTest(exc=exc):
                self.assertIn(expected, texts.explain_error(exc))

    def test_card_and_settings_texts(self):
        source = Source(community_id=5, vk_id=10, name="Источник", url="https://vk.com/club10")
        card = texts.community_card(Community(vk_id=5, name="Цветы", autofill=False), [source], 7,
                                    QueueInfo(count=0), "UTC")
        self.assertIn("В отложке: пусто", card)
        self.assertIn("Автопополнение выключено", card)
        self.assertIn("Картинок в запасе: 7", card)
        settings = texts.settings_text(Community(vk_id=5, chat_link="https://t.me/x", chat_every=6))
        self.assertIn("каждый 6-й пост", settings)

    def test_low_queue_buttons_do_not_toggle_autofill(self):
        from bot import keyboards

        markup = keyboards.low_queue(5)
        self.assertEqual([b.text for row in markup.inline_keyboard for b in row],
                         ["Пополнить отложку сейчас", "Открыть группу"])

    def test_analytics_text_with_empty_group(self):
        report = Report(community=self.community, members=MemberStats(), posts=PostStats(), charts=[])
        text = texts.analytics_text(report)
        self.assertIn("Участников: 0", text)
        self.assertIn("постов не было", text)
        self.assertNotIn("Женщин", text)

    def test_analytics_text_full_and_hidden(self):
        members = MemberStats(total=5000, sampled=1000, dead=100, female=600, male=300, active_week=450, inactive=300)
        posts = PostStats(count=30, per_day=1.0, median_views=1500, median_likes=21, median_reposts=3,
                          median_comments=1, engagement=1.7)
        rec = Recommendation(hours=[9, 14, 20], days=[1, 2, 3, 4, 5, 6], by_posts=True)
        text = texts.analytics_text(Report(self.community, members, posts, rec))
        for part in ["по выборке из 1 000", "Удалённых и заблокированных страниц: 10%", "Женщин: 67%",
                     "Заходили за неделю: 50%", "1500 просмотров, 21 лайк, 3 репоста, 1 комментарий",
                     "Охват поста: 30.0%", "Вовлечённость: 1.7%", "Лучшее время для постов: 09:00, 14:00, 20:00, "
                     "вт, ср, чт, пт, сб, вс", "как набирали просмотры посты", "В пн посты набирают"]:
            self.assertIn(part, text)
        hidden = Report(self.community, MemberStats(total=10, hidden=True), PostStats(hidden=True))
        self.assertIn("Список участников скрыт", texts.analytics_text(hidden))
        self.assertIn("Стена закрыта", texts.analytics_text(hidden))
        other = texts.analytics_text(Report(self.community, members, posts, Recommendation([9], list(range(7)), False)),
                                     own=False)
        self.assertIn("у этой группы: 09:00, каждый день.\nПосчитал по тому, когда заходит аудитория.", other)

    def test_schedule_and_comparison_texts(self):
        community = Community(vk_id=5, name="Цветы", post_hours="9,20", post_days="0,1")
        self.assertEqual(texts.schedule_text(community), "09:00, 20:00, пн, вт")
        self.assertEqual(texts.schedule_text(Community(vk_id=5)), "равномерно в течение суток, каждый день")
        self.assertIn("Время публикаций: 09:00, 20:00, пн, вт", texts.settings_text(community))
        question = texts.schedule_question(community, Recommendation([9, 14, 20], list(range(7)), True))
        self.assertIn("в 09:00, 14:00, 20:00, каждый день?", question)
        self.assertIn("Это 3 поста в день", question)

        members = MemberStats(total=100, sampled=100, female=50, active_week=40)
        posts = PostStats(count=10, median_views=10)
        mine = Report(community, members, posts, Recommendation([20], list(range(7)), True))
        other = Report(Community(vk_id=7, name="Чужая"), MemberStats(total=10, hidden=True),
                       PostStats(count=3, median_views=4), Recommendation([9], list(range(7)), True))
        text = texts.comparison_text(Comparison(mine, other))
        for part in ["Сравнение: «Цветы» и «Чужая»", "Участников: 100 и 10", "Обычный пост, просмотров: 10 и 4",
                     "У «Чужая» лучшее время: 09:00.", "У «Цветы»: 20:00.", "скрывает часть данных"]:
            self.assertIn(part, text)


async def _no_sleep(_seconds: float) -> None:
    return None
