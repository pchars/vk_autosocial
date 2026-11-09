import asyncio
import sys

from api.vk import VkClient
from bot import run_bot
from services.engine import Engine
from utils.config import Config
from utils.lock import AlreadyRunningError, SingleInstanceLock
from utils.logger import get_logger, setup_logging
from utils.store import Store

logger = get_logger("main")


def main() -> int:
    config = Config.load()
    setup_logging(config.log_level, config.logs_dir)
    if not config.telegram_token:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Put it into .env and restart.")
        return 1
    if not config.admin_ids:
        logger.error("TELEGRAM_ADMIN_IDS is empty: nobody could use the bot. Put your Telegram id into .env.")
        return 1

    lock = SingleInstanceLock(config.data_dir / "bot.lock")
    try:
        lock.acquire()
    except AlreadyRunningError:
        logger.error("The bot is already running on this machine (lock %s).", lock.path)
        return 1

    try:
        store = Store(config.db_path)
        store.init()
        token = store.get_setting("vk_token") or config.vk_token
        vk = VkClient(token, config.vk_api_version, cooldown_file=config.data_dir / "vk_cooldowns.json")
        logger.info("Starting VK AutoSocial")
        asyncio.run(run_bot(Engine(config, store, vk)))
    except KeyboardInterrupt:
        pass
    finally:
        lock.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
