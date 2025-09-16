import asyncio
from typing import Dict, Any

from utils import get_logger

logger = get_logger(__name__)


class ContentManager:
    def __init__(self, vk_client):
        self.vk_client = vk_client

    async def wall_cleaner(self, group_id: int, delete_postponed: bool = True, delete_published: bool = True) -> Dict[
        str, Any]:
        """
        Removing posts from group with options

        :param group_id: Group identifier for the posts deletion taken from config file
        :param delete_postponed: Remove postponed posts (True by default)
        :param delete_published: Remove published posts (True by default)
        """
        logger.info("Starting group wall cleanup")
        stats = {
            'deleted_count': 0,
            'problem_ids': [],
            'errors': []
        }

        async def delete_posts(post_type: str):
            nonlocal stats
            offset = 0
            count = 100  # Max per request
            total_processed = 0

            while True:
                try:
                    # Getting posts batch
                    response = await self.vk_client.get_group_posts(
                        group_id=group_id,
                        post_filter=post_type if post_type else 'all',
                        count=count,
                        offset=offset
                    )

                    if not response or 'items' not in response or not response['items']:
                        logger.info(f"No more {post_type} posts found. Total processed: {total_processed}")
                        break

                    current_batch = response['items']
                    batch_size = len(current_batch)
                    total_processed += batch_size

                    logger.info(
                        f"Processing {batch_size} {post_type} posts, offset: {offset}, total processed: {total_processed}")

                    # Removing in reverse order (from new to old)
                    for post in reversed(current_batch):
                        try:
                            result = await self.vk_client.delete_post(
                                group_id=group_id,
                                post_id=post['id']
                            )
                            if result == 1:
                                logger.info(f"Deleted post {post['id']}")
                                stats['deleted_count'] += 1
                            else:
                                stats['problem_ids'].append(post['id'])
                                logger.warning(f"Failed to delete post {post['id']}")

                            # Pause between API requests to avoid flood control
                            await asyncio.sleep(0.35)

                        except Exception as post_error:
                            error_msg = f"Error deleting post {post['id']}: {post_error}"
                            stats['errors'].append(error_msg)
                            logger.error(error_msg)
                            continue

                    offset += batch_size

                    # If we got less amount of the posts than we requested then it is last page
                    if batch_size < count:
                        logger.info(f"Last batch received ({batch_size} < {count}), stopping")
                        break

                except Exception as ex:
                    error_msg = f"Error processing batch at offset {offset}: {ex}"
                    stats['errors'].append(error_msg)
                    logger.error(error_msg)
                    # Continue with the next offset
                    offset += count
                    continue

        try:
            # Removing postponed posts
            if delete_postponed:
                logger.info("Removing postponed posts...")
                await delete_posts(post_type='postponed')

            # Removing published posts
            if delete_published:
                logger.info("Removing published posts...")
                await delete_posts(post_type='owner')

        except Exception as e:
            logger.error(f"Critical error in wall_cleaner method: {e}")
            stats['errors'].append(str(e))

        logger.info(f"Wall cleanup completed. Total deleted: {stats['deleted_count']}")
        return stats

    # WIP
    async def download_group_images(self, group_id: str, max_posts: int = 100):
        """Основная логика загрузки изображений"""
        logger.info(f"Downloading images from group {group_id}")

        # Используем VKClient для получения постов
        posts = await self.vk_client.get_group_posts(group_id=group_id, count=max_posts)

        # Извлекаем URL изображений
        image_urls = []
        for post in posts:
            if post.get('marked_as_ads', 0) == 1:
                continue
            for att in post.get('attachments', []):
                if att.get('type') == 'photo' and 'sizes' in att.get('photo', {}):
                    best_size = max(att['photo']['sizes'], key=lambda x: x['width'])
                    image_urls.append(best_size['url'])

        return image_urls

    async def analyze_group(self, group_id: str):
        """Анализ группы"""
        members = await self.vk_client.get_group_members(group_id)
        # ... аналитика ...
