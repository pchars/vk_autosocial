from typing import Iterable, List

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from utils.store import Community, Source

from . import texts


class Group(CallbackData, prefix="g"):
    action: str
    cid: int
    arg: str = ""


class Account(CallbackData, prefix="a"):
    action: str
    arg: str = ""


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=texts.MENU_GROUPS), KeyboardButton(text=texts.MENU_ADD)],
            [KeyboardButton(text=texts.MENU_ACCOUNT), KeyboardButton(text=texts.MENU_HELP)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def cancel() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Отмена", callback_data=Account(action="cancel"))
    return builder.as_markup()


def communities(items: Iterable[Community], action: str = "open") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for community in items:
        builder.button(text=community.title, callback_data=Group(action=action, cid=community.vk_id))
    builder.adjust(1)
    return builder.as_markup()


def community_card(community: Community) -> InlineKeyboardMarkup:
    cid = community.vk_id
    builder = InlineKeyboardBuilder()
    builder.button(text="Пополнить отложку сейчас", callback_data=Group(action="fill", cid=cid))
    builder.button(text="Источники картинок", callback_data=Group(action="sources", cid=cid))
    builder.button(text="Настройки", callback_data=Group(action="settings", cid=cid))
    builder.button(text="Сторис", callback_data=Group(action="stories", cid=cid))
    builder.button(text="Аналитика", callback_data=Group(action="analytics", cid=cid))
    builder.button(text="Очистить стену", callback_data=Group(action="clear", cid=cid))
    builder.button(text="Отключить группу от бота", callback_data=Group(action="delete", cid=cid))
    builder.button(text="« Все группы", callback_data=Group(action="list", cid=cid))
    builder.adjust(1, 2, 2, 1, 1, 1)
    return builder.as_markup()


def sources(community: Community, items: List[Source]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for source in items:
        builder.button(
            text=f"Убрать: {source.title}",
            callback_data=Group(action="src_del", cid=community.vk_id, arg=str(source.vk_id)),
        )
    builder.button(text="Добавить источники", callback_data=Group(action="src_add", cid=community.vk_id))
    builder.button(text="« К группе", callback_data=Group(action="open", cid=community.vk_id))
    builder.adjust(1)
    return builder.as_markup()


def settings(community: Community) -> InlineKeyboardMarkup:
    cid = community.vk_id
    builder = InlineKeyboardBuilder()
    for field in texts.SETTING_TITLES:
        builder.button(text=texts.SETTING_TITLES[field], callback_data=Group(action="pick", cid=cid, arg=field))
    builder.button(
        text="Выключить автопополнение" if community.autofill else "Включить автопополнение",
        callback_data=Group(action="autofill", cid=cid),
    )
    builder.button(
        text="Убрать подписи к постам" if community.use_quotes else "Подписывать посты фразами из базы",
        callback_data=Group(action="quotes", cid=cid),
    )
    for field, (title, _prompt) in texts.TEXT_FIELDS.items():
        builder.button(text=title, callback_data=Group(action="text", cid=cid, arg=field))
    if community.hours or len(community.days) < 7:
        builder.button(text="Публиковать равномерно, каждый день", callback_data=Group(action="sched_reset", cid=cid))
    builder.button(text="« К группе", callback_data=Group(action="open", cid=cid))
    builder.adjust(1)
    return builder.as_markup()


def own_analytics(community: Community, can_apply: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if can_apply:
        builder.button(text="Применить рекомендации", callback_data=Group(action="sched_ask", cid=community.vk_id))
    builder.button(text="Сравнить с другой группой", callback_data=Group(action="compare_ask", cid=community.vk_id))
    builder.button(text="« К группе", callback_data=Group(action="open", cid=community.vk_id))
    builder.adjust(1)
    return builder.as_markup()


def compare_with(other_id: int, mine: Iterable[Community]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for community in mine:
        builder.button(text=f"Сравнить с «{community.title}»",
                       callback_data=Group(action="compare", cid=community.vk_id, arg=str(other_id)))
    builder.adjust(1)
    return builder.as_markup()


def apply_schedule(cid: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Да, применить", callback_data=Group(action="sched_ok", cid=cid))
    builder.button(text="« Нет, оставить как есть", callback_data=Group(action="open", cid=cid))
    builder.adjust(1)
    return builder.as_markup()


def options(community: Community, field: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    current = getattr(community, field)
    for value in texts.SETTING_OPTIONS[field]:
        label = "выкл" if field == "chat_every" and value == 0 else str(value)
        if value == current:
            label = f"[{label}]"
        builder.button(text=label, callback_data=Group(action="set", cid=community.vk_id, arg=f"{field}={value}"))
    builder.button(text="« Назад", callback_data=Group(action="settings", cid=community.vk_id))
    builder.adjust(4, 4, 1)
    return builder.as_markup()


def choices(cid: int, action: str, values: Iterable[int], back: str = "open") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for value in values:
        builder.button(text=str(value), callback_data=Group(action=action, cid=cid, arg=str(value)))
    builder.button(text="« Назад", callback_data=Group(action=back, cid=cid))
    builder.adjust(3, 1)
    return builder.as_markup()


def clear(cid: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Удалить все отложенные посты", callback_data=Group(action="clear_ok", cid=cid, arg="postponed"))
    builder.button(text="Удалить ВСЕ опубликованные посты", callback_data=Group(action="clear_ok", cid=cid, arg="published"))
    builder.button(text="« Не удалять", callback_data=Group(action="open", cid=cid))
    builder.adjust(1)
    return builder.as_markup()


def confirm(cid: int, action: str, yes: str, arg: str = "") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=yes, callback_data=Group(action=action, cid=cid, arg=arg))
    builder.button(text="« Нет, назад", callback_data=Group(action="open", cid=cid))
    builder.adjust(1)
    return builder.as_markup()


def low_queue(cid: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Пополнить отложку сейчас", callback_data=Group(action="fill", cid=cid))
    builder.button(text="Открыть группу", callback_data=Group(action="open", cid=cid))
    builder.adjust(1)
    return builder.as_markup()


def after_setup(cid: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Пополнить отложку сейчас", callback_data=Group(action="fill", cid=cid))
    builder.button(text="Включить автопополнение", callback_data=Group(action="autofill", cid=cid))
    builder.button(text="Открыть группу", callback_data=Group(action="open", cid=cid))
    builder.adjust(1)
    return builder.as_markup()


def account(is_owner: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Репост из группы на мою стену", callback_data=Account(action="repost"))
    if is_owner:
        builder.button(text="Обновить доступ к ВК", callback_data=Account(action="token"))
        builder.button(text="Помощники", callback_data=Account(action="admins"))
    builder.adjust(1)
    return builder.as_markup()


def token(url: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Открыть ВК", url=url)
    builder.button(text="Отмена", callback_data=Account(action="cancel"))
    builder.adjust(1)
    return builder.as_markup()


def access_request(tg_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Дать доступ", callback_data=Account(action="approve", arg=str(tg_id)))
    builder.button(text="Отклонить", callback_data=Account(action="deny", arg=str(tg_id)))
    builder.adjust(2)
    return builder.as_markup()


def admins(items: Iterable[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for admin in items:
        builder.button(
            text=f"Убрать: {admin['name'] or admin['tg_id']}",
            callback_data=Account(action="revoke", arg=str(admin["tg_id"])),
        )
    builder.adjust(1)
    return builder.as_markup()
