import asyncio
import contextlib

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, LinkPreviewOptions

from api.vk import VkError
from services.autofill import AutoFiller
from services.engine import Engine
from utils.logger import get_logger

from . import keyboards as kb
from . import texts
from .access import Access, Notifier
from .handlers import build_router

logger = get_logger(__name__)


async def run_bot(engine: Engine) -> None:
    config = engine.config
    bot = Bot(
        token=config.telegram_token,
        default=DefaultBotProperties(link_preview=LinkPreviewOptions(is_disabled=True)),
    )
    access = Access(config.admin_ids, engine.store)
    notifier = Notifier(bot, access)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(build_router(engine, access, notifier))
    await bot.set_my_commands([BotCommand(command="start", description="Главное меню")])

    await _check_vk_access(engine, notifier)
    autofill = AutoFiller(engine, notifier.send, config.autofill_interval_min * 60, low_queue_buttons=kb.low_queue)
    autofill_task = asyncio.create_task(autofill.run())
    logger.info("Telegram bot polling started")
    try:
        await dispatcher.start_polling(bot)
    finally:
        autofill_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await autofill_task
        await engine.close()
        await bot.session.close()


async def _check_vk_access(engine: Engine, notifier: Notifier) -> None:
    if not engine.vk.token:
        await notifier.send(
            "Бот запущен, но доступ к ВК не настроен. "
            f"Откройте «{texts.MENU_ACCOUNT}» → «Обновить доступ к ВК».",
            owners_only=True,
        )
        return
    try:
        logger.info("VK account: %s", await engine.whoami())
    except VkError as exc:
        logger.error("VK access check failed: %s", exc)
        await notifier.send(texts.explain_error(exc), owners_only=True)
