import asyncio
from typing import Awaitable, Callable

from api.vk import VkAuthError, VkClient, VkError, VkFloodError
from utils.logger import get_logger

logger = get_logger(__name__)


class AccountService:
    """Actions on the admin's personal VK page."""

    def __init__(self, vk: VkClient, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.vk = vk
        self.sleep = sleep

    async def name(self) -> str:
        users = await self.vk.call("users.get")
        user = users[0]
        return f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()

    async def repost(self, community_id: int, count: int) -> int:
        """Share the latest community posts on the admin's own wall."""
        wall = await self.vk.call("wall.get", owner_id=-int(community_id), filter="owner", count=min(count + 5, 100))
        reposted = 0
        for post in (wall or {}).get("items") or []:
            if reposted >= count:
                break
            if post.get("marked_as_ads") or post.get("is_pinned"):
                continue
            try:
                await self.vk.call("wall.repost", object=f"wall-{int(community_id)}_{post['id']}")
            except (VkAuthError, VkFloodError):
                raise
            except VkError as exc:
                logger.warning("Repost of %s failed: %s", post.get("id"), exc)
                continue
            reposted += 1
            await self.sleep(1.0)
        return reposted
