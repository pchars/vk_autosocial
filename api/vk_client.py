from typing import List, Dict, Any

import vk_api
from utils import get_logger

logger = get_logger(__name__)


class VKClient:
    """Client to communicate with VK API"""

    def __init__(self, vk_session):
        self.vk = vk_session.get_api()
        self.session = vk_session

    async def get_group_posts(self, group_id: str, post_filter: str = 'all', count: int = 100, offset: int = 0) -> Dict[
        str, Any]:
        """Get posts from group with filtering"""
        try:
            posts = await self._run_in_executor(
                self.vk.wall.get,
                owner_id=group_id,
                filter=post_filter,
                count=count,
                offset=offset
            )
            return posts
        except Exception as e:
            logger.error(f"Error getting posts for group {group_id}: {e}")
            return {'items': [], 'count': 0}

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

    async def get_friends(self, fields: List[str] = None) -> List[Dict]:
        """Get user's friends list with optional fields"""
        if fields is None:
            fields = ['deactivated', 'last_seen']

        logger.debug(f"Requesting friends list with fields: {fields}")

        try:
            friends = await self._run_in_executor(
                self.vk.friends.get,
                fields=','.join(fields)
            )

            friend_count = len(friends.get('items', []))
            logger.info(f"Retrieved {friend_count} friends from VK")

            return friends.get('items', [])

        except vk_api.exceptions.ApiError as api_error:
            if 'Permission denied' in str(api_error):
                logger.warning("Friends list is private or inaccessible")
            elif 'User authorization failed' in str(api_error):
                logger.error(f'VKAuth error occurred: {str(api_error)}')
            else:
                logger.error(f"VK API error getting friends: {api_error}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error getting friends: {e}")
            return []

    async def get_requests_friends(self, out: int = 1, offset: int = 0, count: int = 1000) -> Dict[str, Any]:
        """Get user's friends requests list"""
        logger.debug("Requesting requests friends list")

        try:
            friends_requests = await self._run_in_executor(
                self.vk.friends.getRequests,
                out=out,
                # 1 - requests which you sent to people, 0 - requests which people sent to you (your subscribers)
                offset=offset,
                count=count
            )

            return friends_requests

        except vk_api.exceptions.ApiError as api_error:
            if 'User authorization failed' in str(api_error):
                logger.error(f'VKAuth error occurred: {str(api_error)}')
            else:
                logger.error(f"VK API error getting friends requests: {api_error}")
            return {}
        except Exception as e:
            logger.error(f"Unexpected error getting friends: {e}")
            return {}

    async def put_post(self, group_id: str, message: str, attachments: List[str] = None) -> bool:
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

    async def delete_friend(self, user_id: str) -> bool:
        """Delete a friend by user ID"""
        logger.debug(f"Attempting to remove friend: {user_id}")

        try:
            result = await self._run_in_executor(
                self.vk.friends.delete,
                user_id=user_id
            )

            if result.get("success") == 1:
                logger.info(f"Successfully removed friend: {user_id}")
                return True
            else:
                logger.warning(f"Failed to remove friend: {user_id}, API returned: {result}")
                return False

        except vk_api.exceptions.ApiError as api_error:
            if "No friend or friend request found" in str(api_error):
                logger.warning(f"User {user_id} is not a friend")
            elif "flood" in str(api_error).lower():
                logger.warning("Rate limit exceeded for friend removal")
            else:
                logger.error(f"VK API error removing friend {user_id}: {api_error}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error removing friend {user_id}: {e}")
            return False

    async def delete_post(self, group_id: str, post_id: str) -> bool:
        """Delete a post by group_id and post_id"""
        logger.debug(f"Attempting to remove post {post_id} in {group_id}")

        try:
            result = await self._run_in_executor(
                self.vk.wall.delete,
                owner_id=group_id,
                post_id=post_id
            )

            if result == 1:
                logger.info(f"Successfully removed post {post_id} from {group_id}")
                return True
            else:
                logger.warning(f"Failed to remove post {post_id} from {group_id}, API returned: {result}")
                return False

        except vk_api.exceptions.ApiError as api_error:
            if "flood" in str(api_error).lower():
                logger.warning("Rate limit exceeded for friend removal")
            else:
                logger.error(f"VK API error removing post {post_id} from {group_id}: {api_error}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error removing post {post_id} from {group_id}: {e}")
            return False

    async def _run_in_executor(self, func, *args, **kwargs):
        """Run sync VK API methods in executor"""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))
