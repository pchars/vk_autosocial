import shutil
from typing import Any, Awaitable, Optional, Tuple

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ErrorEvent, FSInputFile, InlineKeyboardMarkup, InputMediaPhoto, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from api.vk import VkError
from api.vk_token import extract_token, oauth_url
from services.engine import Engine, UserError
from utils.dates import plural
from utils.logger import get_logger
from utils.vk_links import mentions_vk

from . import keyboards as kb
from . import texts
from .access import Access, AdminFilter, Notifier
from .keyboards import Account, Group

logger = get_logger(__name__)


class Input(StatesGroup):
    group_link = State()
    sources = State()
    text_value = State()
    token = State()
    compare_link = State()


async def attempt(message: Message, action: Awaitable[Any]) -> Tuple[bool, Any]:
    """Run an action and turn any failure into a plain-language reply."""
    try:
        return True, await action
    except Exception as exc:
        if not isinstance(exc, (UserError, VkError)):
            logger.exception("Bot action failed")
        await message.answer(texts.explain_error(exc))
        return False, None


async def show(callback: CallbackQuery, text: str, markup: Optional[InlineKeyboardMarkup] = None) -> None:
    """Replace the message with the button that was pressed; send a new one if Telegram refuses."""
    await callback.answer()
    try:
        await callback.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=markup, disable_web_page_preview=True)


def build_router(engine: Engine, access: Access, notifier: Notifier) -> Router:
    root = Router()
    admin = Router()
    admin.message.filter(AdminFilter(access))
    admin.callback_query.filter(AdminFilter(access))
    tz = engine.config.timezone
    store = engine.store

    async def card(cid: int) -> Tuple[str, InlineKeyboardMarkup]:
        community = engine.community(cid)
        try:
            queue = await engine.queue(cid)
        except VkError:
            queue = None
        text = texts.community_card(community, store.list_sources(cid), engine.stock(cid), queue, tz)
        return text, kb.community_card(community)

    async def open_card(callback: CallbackQuery, cid: int) -> None:
        ok, payload = await attempt(callback.message, card(cid))
        if ok:
            await show(callback, *payload)
        else:
            await callback.answer()

    # Main menu: always available and resets any unfinished input.

    @admin.message(Command("start", "menu", "cancel"), StateFilter("*"))
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(texts.WELCOME, reply_markup=kb.main_menu())

    @admin.message(F.text == texts.MENU_HELP, StateFilter("*"))
    async def help_menu(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(texts.HELP, reply_markup=kb.main_menu())

    @admin.message(F.text == texts.MENU_GROUPS, StateFilter("*"))
    async def groups_menu(message: Message, state: FSMContext) -> None:
        await state.clear()
        items = store.list_communities()
        if not items:
            await message.answer(f"Пока нет ни одной группы. Нажмите «{texts.MENU_ADD}».")
        elif len(items) == 1:
            ok, payload = await attempt(message, card(items[0].vk_id))
            if ok:
                text, markup = payload
                await message.answer(text, reply_markup=markup, disable_web_page_preview=True)
        else:
            await message.answer("Ваши группы:", reply_markup=kb.communities(items))

    @admin.message(F.text == texts.MENU_ADD, StateFilter("*"))
    async def add_menu(message: Message, state: FSMContext) -> None:
        await state.set_state(Input.group_link)
        await message.answer(texts.ASK_GROUP_LINK, reply_markup=kb.cancel(), disable_web_page_preview=True)

    @admin.message(F.text == texts.MENU_ACCOUNT, StateFilter("*"))
    async def account_menu(message: Message, state: FSMContext) -> None:
        await state.clear()
        try:
            status = f"Аккаунт ВК: {await engine.whoami()}. Доступ работает."
        except VkError as exc:
            status = texts.explain_error(exc)
        await message.answer(status, reply_markup=kb.account(access.is_owner(message.from_user.id)))

    # Text input steps

    @admin.message(Input.group_link, F.text)
    async def got_group_link(message: Message, state: FSMContext) -> None:
        ok, community = await attempt(message, engine.add_community(message.text))
        if not ok:
            await message.answer("Пришлите другую ссылку или нажмите «Отмена».", reply_markup=kb.cancel())
            return
        await state.set_state(Input.sources)
        await state.update_data(cid=community.vk_id, setup=True)
        await message.answer(f"Группа «{community.title}» подключена!\n\n{texts.ASK_SOURCES}", reply_markup=kb.cancel())

    @admin.message(Input.sources, F.text)
    async def got_sources(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        cid = int(data["cid"])
        ok, payload = await attempt(message, engine.add_sources(cid, message.text))
        if not ok:
            return
        added, problems = payload
        lines = []
        if added:
            lines.append("Добавил: " + ", ".join(f"«{name}»" for name in added) + ".")
        if problems:
            lines.append("Не добавил:\n" + "\n".join(f"— {p}" for p in problems))
        if not added:
            lines.append("Пришлите другие ссылки или нажмите «Отмена».")
            await message.answer("\n\n".join(lines), reply_markup=kb.cancel(), disable_web_page_preview=True)
            return
        await state.clear()
        community = engine.community(cid)
        if data.get("setup"):
            lines.append(
                "Готово! Проверьте источники и пополните отложку кнопкой. "
                f"Когда всё в порядке, включите автопополнение — тогда я сам буду держать в отложке "
                f"{plural(community.queue_target, 'пост', 'поста', 'постов')} "
                f"({community.posts_per_day} в день). Частоту можно поменять в настройках группы."
            )
            await message.answer("\n\n".join(lines), reply_markup=kb.after_setup(cid))
        else:
            await message.answer("\n\n".join(lines), reply_markup=kb.sources(community, store.list_sources(cid)))

    @admin.message(Input.text_value, F.text)
    async def got_text_value(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        await state.clear()
        value = "" if message.text.strip() == "-" else message.text.strip()
        community = store.update_community(int(data["cid"]), **{data["field"]: value})
        await message.answer("Сохранил.\n\n" + texts.settings_text(community), reply_markup=kb.settings(community))

    @admin.message(Input.token, F.text)
    async def got_token(message: Message, state: FSMContext, bot: Bot) -> None:
        try:
            await bot.delete_message(message.chat.id, message.message_id)
        except TelegramBadRequest:
            await message.answer("Не смог удалить сообщение со ссылкой — удалите его сами: в нём доступ к вашему ВК.")
        if not access.is_owner(message.from_user.id):
            await state.clear()
            await message.answer("Обновлять доступ к ВК может только владелец.", reply_markup=kb.main_menu())
            return
        token = extract_token(message.text)
        if not token:
            await message.answer(
                "В сообщении нет доступа ВК. Скопируйте адрес страницы oauth.vk.ru целиком — "
                "в нём должно быть «access_token=».",
                reply_markup=kb.cancel(),
            )
            return
        ok, name = await attempt(message, engine.set_token(token))
        if ok:
            await state.clear()
            await message.answer(f"Доступ к ВК обновлён. Аккаунт: {name}.", reply_markup=kb.main_menu())

    @admin.message(F.text, StateFilter(None))
    async def free_text(message: Message, state: FSMContext) -> None:
        if not mentions_vk(message.text):
            await message.answer(texts.UNKNOWN_TEXT, reply_markup=kb.main_menu())
            return
        await state.update_data(pending_link=message.text)
        builder = InlineKeyboardBuilder()
        builder.button(text="Посмотреть аналитику", callback_data=Account(action="link_stats"))
        builder.button(text="Это моя группа — подключить", callback_data=Account(action="link_group"))
        for community in store.list_communities():
            builder.button(text=f"Источник для «{community.title}»",
                           callback_data=Group(action="link_src", cid=community.vk_id))
        builder.adjust(1)
        await message.answer("Что сделать с этой ссылкой?", reply_markup=builder.as_markup())

    # Group buttons

    @admin.callback_query(Group.filter(F.action == "list"))
    async def list_groups(callback: CallbackQuery) -> None:
        await show(callback, "Ваши группы:", kb.communities(store.list_communities()))

    @admin.callback_query(Group.filter(F.action == "open"))
    async def open_group(callback: CallbackQuery, callback_data: Group, state: FSMContext) -> None:
        await state.clear()
        await open_card(callback, callback_data.cid)

    @admin.callback_query(Group.filter(F.action == "fill"))
    async def fill(callback: CallbackQuery, callback_data: Group) -> None:
        await callback.answer()
        community = engine.community(callback_data.cid)
        await callback.message.answer(
            f"Пополняю «{community.title}». Это займёт несколько минут — напишу, когда закончу."
        )
        ok, result = await attempt(callback.message, engine.fill(community.vk_id))
        if ok:
            await callback.message.answer(texts.fill_result(community, result, tz),
                                          reply_markup=kb.after_setup(community.vk_id))

    @admin.callback_query(Group.filter(F.action == "sources"))
    async def sources(callback: CallbackQuery, callback_data: Group) -> None:
        community = engine.community(callback_data.cid)
        items = store.list_sources(community.vk_id)
        await show(callback, texts.sources_text(community, items), kb.sources(community, items))

    @admin.callback_query(Group.filter(F.action == "src_add"))
    async def source_add(callback: CallbackQuery, callback_data: Group, state: FSMContext) -> None:
        await state.set_state(Input.sources)
        await state.update_data(cid=callback_data.cid, setup=False)
        await callback.answer()
        await callback.message.answer(texts.ASK_SOURCES, reply_markup=kb.cancel())

    @admin.callback_query(Group.filter(F.action == "src_del"))
    async def source_delete(callback: CallbackQuery, callback_data: Group) -> None:
        store.remove_source(callback_data.cid, int(callback_data.arg))
        community = engine.community(callback_data.cid)
        items = store.list_sources(community.vk_id)
        await show(callback, texts.sources_text(community, items), kb.sources(community, items))

    @admin.callback_query(Group.filter(F.action == "link_src"))
    async def link_as_source(callback: CallbackQuery, callback_data: Group, state: FSMContext) -> None:
        link = (await state.get_data()).get("pending_link", "")
        await state.clear()
        await callback.answer()
        ok, payload = await attempt(callback.message, engine.add_sources(callback_data.cid, link))
        if ok:
            added, problems = payload
            text = ("Добавил: " + ", ".join(f"«{n}»" for n in added) + ".") if added else ""
            if problems:
                text += "\nНе добавил:\n" + "\n".join(f"— {p}" for p in problems)
            await callback.message.answer(text.strip(), disable_web_page_preview=True)

    @admin.callback_query(Group.filter(F.action == "settings"))
    async def settings(callback: CallbackQuery, callback_data: Group, state: FSMContext) -> None:
        await state.clear()
        community = engine.community(callback_data.cid)
        await show(callback, texts.settings_text(community), kb.settings(community))

    @admin.callback_query(Group.filter(F.action == "pick"))
    async def pick(callback: CallbackQuery, callback_data: Group) -> None:
        community = engine.community(callback_data.cid)
        title = texts.SETTING_TITLES[callback_data.arg]
        await show(callback, f"«{community.title}»\n{title}:", kb.options(community, callback_data.arg))

    @admin.callback_query(Group.filter(F.action == "set"))
    async def set_value(callback: CallbackQuery, callback_data: Group) -> None:
        field, value = callback_data.arg.split("=", 1)
        community = store.update_community(callback_data.cid, **{field: int(value)})
        if field == "chat_every" and community.chat_every and not community.chat_link:
            await callback.message.answer("Чтобы приглашения работали, укажите ещё «Ссылку на чат».")
        await show(callback, texts.settings_text(community), kb.settings(community))

    @admin.callback_query(Group.filter(F.action == "autofill"))
    async def toggle_autofill(callback: CallbackQuery, callback_data: Group) -> None:
        community = engine.community(callback_data.cid)
        community = store.update_community(community.vk_id, autofill=not community.autofill)
        await show(callback, texts.settings_text(community), kb.settings(community))

    @admin.callback_query(Group.filter(F.action == "quotes"))
    async def toggle_quotes(callback: CallbackQuery, callback_data: Group) -> None:
        community = engine.community(callback_data.cid)
        community = store.update_community(community.vk_id, use_quotes=not community.use_quotes)
        await show(callback, texts.settings_text(community), kb.settings(community))

    @admin.callback_query(Group.filter(F.action == "text"))
    async def ask_text(callback: CallbackQuery, callback_data: Group, state: FSMContext) -> None:
        await state.set_state(Input.text_value)
        await state.update_data(cid=callback_data.cid, field=callback_data.arg)
        await callback.answer()
        await callback.message.answer(texts.TEXT_FIELDS[callback_data.arg][1], reply_markup=kb.cancel())

    @admin.callback_query(Group.filter(F.action == "stories"))
    async def stories(callback: CallbackQuery, callback_data: Group) -> None:
        await show(callback, "Сколько сторис выложить прямо сейчас?",
                   kb.choices(callback_data.cid, "stories_go", (1, 3, 5)))

    @admin.callback_query(Group.filter(F.action == "stories_go"))
    async def stories_go(callback: CallbackQuery, callback_data: Group) -> None:
        await show(callback, "Выкладываю сторис…")
        ok, count = await attempt(callback.message, engine.stories(callback_data.cid, int(callback_data.arg)))
        if ok:
            await callback.message.answer(f"Опубликовал {plural(count, 'сторис', 'сторис', 'сторис')}.")

    async def send_charts(message: Message, charts) -> None:
        if charts:
            await message.answer_media_group([InputMediaPhoto(media=FSInputFile(p)) for p in charts[:10]])

    async def send_report(message: Message, report) -> None:
        await send_charts(message, report.charts)
        own = store.get_community(report.community.vk_id)
        if own:
            rec = report.recommendation
            can_apply = bool(rec) and (rec.hours != own.hours or rec.days != own.days)
            await message.answer(texts.analytics_text(report), reply_markup=kb.own_analytics(own, can_apply))
        else:
            mine = store.list_communities()
            await message.answer(texts.analytics_text(report, own=False),
                                 reply_markup=kb.compare_with(report.community.vk_id, mine) if mine else None)

    async def run_compare(message: Message, cid: int, other_id: int) -> None:
        await message.answer("Сравниваю группы, это может занять пару минут…")
        ok, comparison = await attempt(message, engine.compare(cid, other_id))
        if ok:
            await send_charts(message, comparison.charts)
            await message.answer(texts.comparison_text(comparison),
                                 reply_markup=kb.own_analytics(comparison.mine.community, False))

    @admin.callback_query(Group.filter(F.action == "analytics"))
    async def analytics(callback: CallbackQuery, callback_data: Group) -> None:
        await callback.answer()
        await callback.message.answer("Считаю аналитику, это может занять пару минут…")
        ok, report = await attempt(callback.message, engine.analytics(callback_data.cid))
        if ok:
            await send_report(callback.message, report)

    @admin.callback_query(Account.filter(F.action == "link_stats"))
    async def link_stats(callback: CallbackQuery, state: FSMContext) -> None:
        link = (await state.get_data()).get("pending_link", "")
        await state.clear()
        await callback.answer()
        await callback.message.answer("Считаю аналитику, это может занять пару минут…")
        ok, report = await attempt(callback.message, engine.analytics_of(link))
        if ok:
            await send_report(callback.message, report)

    @admin.callback_query(Group.filter(F.action == "compare"))
    async def compare(callback: CallbackQuery, callback_data: Group) -> None:
        await callback.answer()
        await run_compare(callback.message, callback_data.cid, int(callback_data.arg))

    @admin.callback_query(Group.filter(F.action == "compare_ask"))
    async def compare_ask(callback: CallbackQuery, callback_data: Group, state: FSMContext) -> None:
        community = engine.community(callback_data.cid)
        await state.set_state(Input.compare_link)
        await state.update_data(cid=community.vk_id)
        await callback.answer()
        await callback.message.answer(f"Пришлите ссылку на группу ВК, с которой сравнить «{community.title}».",
                                      reply_markup=kb.cancel())

    @admin.message(Input.compare_link, F.text)
    async def got_compare_link(message: Message, state: FSMContext) -> None:
        ok, info = await attempt(message, engine.resolve(message.text))
        if not ok:
            await message.answer("Пришлите другую ссылку или нажмите «Отмена».", reply_markup=kb.cancel())
            return
        cid = int((await state.get_data())["cid"])
        await state.clear()
        await run_compare(message, cid, info.vk_id)

    def fresh_recommendation(cid: int):
        report = engine.cached_report(cid)
        return report.recommendation if report else None

    @admin.callback_query(Group.filter(F.action == "sched_ask"))
    async def schedule_ask(callback: CallbackQuery, callback_data: Group) -> None:
        community = engine.community(callback_data.cid)
        rec = fresh_recommendation(community.vk_id)
        if not rec:
            await callback.answer("Рекомендации устарели — откройте аналитику ещё раз.", show_alert=True)
            return
        await show(callback, texts.schedule_question(community, rec), kb.apply_schedule(community.vk_id))

    @admin.callback_query(Group.filter(F.action == "sched_ok"))
    async def schedule_ok(callback: CallbackQuery, callback_data: Group) -> None:
        rec = fresh_recommendation(callback_data.cid)
        if not rec:
            await callback.answer("Рекомендации устарели — откройте аналитику ещё раз.", show_alert=True)
            return
        community = engine.apply_schedule(callback_data.cid, rec.hours, rec.days)
        await show(callback, "Готово! Новые посты будут выходить: " + texts.schedule_text(community) + ".\n\n"
                   + texts.settings_text(community), kb.settings(community))

    @admin.callback_query(Group.filter(F.action == "sched_reset"))
    async def schedule_reset(callback: CallbackQuery, callback_data: Group) -> None:
        community = engine.reset_schedule(callback_data.cid)
        await show(callback, texts.settings_text(community), kb.settings(community))

    @admin.callback_query(Group.filter(F.action == "clear"))
    async def clear(callback: CallbackQuery, callback_data: Group) -> None:
        community = engine.community(callback_data.cid)
        await show(callback, f"Что удалить в «{community.title}»? Это нельзя отменить.", kb.clear(community.vk_id))

    @admin.callback_query(Group.filter(F.action == "clear_ok"))
    async def clear_ok(callback: CallbackQuery, callback_data: Group) -> None:
        community = engine.community(callback_data.cid)
        if callback_data.arg == "published":
            await show(callback, f"Точно удалить ВСЕ опубликованные посты в «{community.title}»? "
                                 "Вернуть их будет нельзя.",
                       kb.confirm(community.vk_id, "clear_ok", "Да, удалить всё", arg="published!"))
            return
        published = callback_data.arg == "published!"
        await show(callback, "Удаляю посты…")
        ok, count = await attempt(callback.message, engine.clear(community.vk_id, published=published))
        if ok:
            await callback.message.answer(f"Удалил {plural(count, 'пост', 'поста', 'постов')} в «{community.title}».")

    @admin.callback_query(Group.filter(F.action == "delete"))
    async def delete(callback: CallbackQuery, callback_data: Group) -> None:
        community = engine.community(callback_data.cid)
        await show(callback, f"Отключить «{community.title}» от бота? Посты, которые уже стоят в отложке, останутся.",
                   kb.confirm(community.vk_id, "delete_ok", "Да, отключить"))

    @admin.callback_query(Group.filter(F.action == "delete_ok"))
    async def delete_ok(callback: CallbackQuery, callback_data: Group) -> None:
        store.delete_community(callback_data.cid)
        shutil.rmtree(engine.folder(callback_data.cid), ignore_errors=True)
        await show(callback, "Группа отключена от бота.")

    @admin.callback_query(Group.filter(F.action == "repost_group"))
    async def repost_group(callback: CallbackQuery, callback_data: Group) -> None:
        await show(callback, "Сколько последних постов группы репостнуть на вашу стену?",
                   kb.choices(callback_data.cid, "repost", (1, 3, 5)))

    @admin.callback_query(Group.filter(F.action == "repost"))
    async def repost(callback: CallbackQuery, callback_data: Group) -> None:
        await show(callback, "Делаю репосты…")
        ok, count = await attempt(callback.message, engine.repost(callback_data.cid, int(callback_data.arg)))
        if ok:
            await callback.message.answer(f"Репостнул {plural(count, 'пост', 'поста', 'постов')} на вашу стену.")

    # Account buttons

    @admin.callback_query(Account.filter(F.action == "cancel"))
    async def cancel(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await show(callback, texts.CANCELLED)

    @admin.callback_query(Account.filter(F.action == "link_group"))
    async def link_as_group(callback: CallbackQuery, state: FSMContext) -> None:
        link = (await state.get_data()).get("pending_link", "")
        await callback.answer()
        ok, community = await attempt(callback.message, engine.add_community(link))
        if not ok:
            await state.clear()
            return
        await state.set_state(Input.sources)
        await state.update_data(cid=community.vk_id, setup=True)
        await callback.message.answer(f"Группа «{community.title}» подключена!\n\n{texts.ASK_SOURCES}",
                                      reply_markup=kb.cancel())

    @admin.callback_query(Account.filter(F.action == "repost"))
    async def pick_group(callback: CallbackQuery) -> None:
        items = store.list_communities()
        if not items:
            await show(callback, f"Сначала подключите группу кнопкой «{texts.MENU_ADD}».")
            return
        if len(items) == 1:
            await show(callback, "Сколько последних постов группы репостнуть на вашу стену?",
                       kb.choices(items[0].vk_id, "repost", (1, 3, 5)))
            return
        await show(callback, "Из какой группы делать репост?", kb.communities(items, action="repost_group"))

    @admin.callback_query(Account.filter(F.action == "token"))
    async def token(callback: CallbackQuery, state: FSMContext) -> None:
        if not access.is_owner(callback.from_user.id):
            await callback.answer("Только для владельца", show_alert=True)
            return
        await state.set_state(Input.token)
        await callback.answer()
        await callback.message.answer(texts.ASK_TOKEN, reply_markup=kb.token(oauth_url(engine.config.vk_api_version)))

    @admin.callback_query(Account.filter(F.action == "admins"))
    async def admins(callback: CallbackQuery) -> None:
        if not access.is_owner(callback.from_user.id):
            await callback.answer("Только для владельца", show_alert=True)
            return
        items = store.list_admins()
        text = ("Чтобы дать доступ помощнику, попросите его написать боту любое сообщение — "
                "вам придёт кнопка «Дать доступ».")
        if items:
            text = "Помощники:\n" + "\n".join(f"— {a['name'] or a['tg_id']}" for a in items) + "\n\n" + text
        await show(callback, text, kb.admins(items))

    @admin.callback_query(Account.filter(F.action.in_({"approve", "deny", "revoke"})))
    async def manage_access(callback: CallbackQuery, callback_data: Account, bot: Bot) -> None:
        if not access.is_owner(callback.from_user.id):
            await callback.answer("Только для владельца", show_alert=True)
            return
        tg_id = int(callback_data.arg)
        if callback_data.action == "approve":
            store.add_admin(tg_id, access.pending_names.pop(tg_id, ""))
            await show(callback, f"Доступ выдан ({tg_id}).")
            await bot.send_message(tg_id, "Вам открыли доступ к боту. Нажмите /start")
        elif callback_data.action == "deny":
            access.pending_names.pop(tg_id, None)
            await show(callback, "Запрос отклонён.")
        else:
            store.remove_admin(tg_id)
            await show(callback, "Доступ отозван.", kb.admins(store.list_admins()))

    @root.error()
    async def on_error(event: ErrorEvent) -> bool:
        exc = event.exception
        if not isinstance(exc, (UserError, VkError)):
            logger.error("Unhandled bot error", exc_info=exc)
        text = texts.explain_error(exc)
        update = event.update
        if update.callback_query:
            await update.callback_query.answer()
            if update.callback_query.message:
                await update.callback_query.message.answer(text)
        elif update.message:
            await update.message.answer(text)
        return True

    root.include_router(admin)

    # Everyone else

    stranger = Router()

    @stranger.message()
    async def stranger_message(message: Message) -> None:
        user = message.from_user
        if user is None:
            return
        if access.should_answer_stranger(user.id):
            await message.answer(texts.NOT_ADMIN.format(tg_id=user.id))
        if access.should_forward_request(user.id):
            name = user.full_name + (f" (@{user.username})" if user.username else "")
            access.pending_names[user.id] = name
            await notifier.send(f"{name} просит доступ к боту.", owners_only=True,
                                reply_markup=kb.access_request(user.id))

    @stranger.callback_query()
    async def stranger_callback(callback: CallbackQuery) -> None:
        await callback.answer("Нет доступа", show_alert=True)

    root.include_router(stranger)
    return root
