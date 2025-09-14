from typing import List, Dict
from utils import get_logger

logger = get_logger(__name__)


class VKClient:
    """Client to communicate with VK API"""

    def __init__(self, vk_session):
        self.vk = vk_session.get_api()
        self.session = vk_session

    async def get_group_posts(self, group_id: str, count: int = 100) -> List[Dict]:
        """Get posts from group"""
        try:
            posts = await self._run_in_executor(
                self.vk.wall.get,
                owner_id=-int(group_id),
                count=count
            )
            return posts.get('items', [])
        except Exception as e:
            logger.error(f"Error getting posts for group {group_id}: {e}")
            return []

    async def get_group_members(self, group_id: str, fields: List[str] = None) -> List[Dict]:
        """Get members of some group or public"""
        if fields is None:
            fields = ['sex', 'last_seen']

        logger.info(f"Sending request to VK to obtain list of the users with preferred parameters: {str(fields)}")
        try:
            members = await self._run_in_executor(
                self.vk.groups.getMembers,
                group_id=group_id,
                fields=','.join(fields)
            )
            return members.get('items', [])
        except Exception as e:
            logger.error(f"Error getting members for group {group_id}: {e}")
            return []

    async def publish_post(self, group_id: str, message: str, attachments: List[str] = None) -> bool:
        """Publish post to the wall"""
        try:
            result = await self._run_in_executor(
                self.vk.wall.post,
                owner_id=-int(group_id),
                message=message,
                attachments=attachments or []
            )
            return True
        except Exception as e:
            logger.error(f"Error publishing post: {e}")
            return False

    async def add_friend(self, user_id: str) -> bool:
        """Invite a friend"""
        logger.info(f"Sending request to VK to invite a random friend")
        try:
            result = await self._run_in_executor(
                self.vk.friends.add,
                user_id=user_id
            )
            return True
        except Exception as e:
            logger.error(f"Error while adding a friend: {e}")
            return False

    async def _run_in_executor(self, func, *args, **kwargs):
        """Run sync VK API methods in executor"""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))