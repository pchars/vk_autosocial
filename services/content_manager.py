import asyncio
import random
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

import aiohttp
import requests
from utils import get_logger, FileContentUtils
from utils.os_management import OSManagement

logger = get_logger(__name__)


class ContentManager:
    def __init__(self, vk_client):
        self.vk_client = vk_client
        self.os_manager = OSManagement()

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

    async def post_publisher(self, folder: Path, your_group: int, start_time: int, time_delay: int = 28800,
                             max_photos: int = 6, group_chat_reminder_text: str = "", hashtags: str = "",
                             group_chat_link: str = "",
                             group_chat_reminder: int = 6, text: str = "db_text.txt") -> None:
        logger.info('Starting publication of posts.')

        os_manager = OSManagement()

        image_files = os_manager.get_files_list(folder)
        image_files = [f for f in image_files if f and f.exists()]

        if not image_files:
            logger.warning("No valid image files found to process")
            return

        # Getting upload_url one time for further usage
        upload_server_response = await self.vk_client.get_photos_wall_upload_server(group_id=your_group)
        upload_url = upload_server_response['upload_url']
        logger.debug(f'getWallUploadServer method response: {upload_server_response}')
        logger.debug(f'URL for uploading was determined: {upload_url}')

        # HTTP Session establishing for the POST requests
        session = requests.Session()

        photos, image_counter = [], 1
        delay = time_delay

        # Determining time for the postponed posts
        postponed = await self.vk_client.get_group_posts(group_id=-your_group, post_filter='postponed')
        if postponed['count'] > 0:
            dates = sorted(item['date'] for item in postponed['items'])
            now = int(dates[-1]) + delay
            logger.info(f'Using last postponed post time: {now}')
        else:
            now = float(start_time) if start_time else time.time()
            logger.info(f'Using config or current time: {now}')

        # Uploading and publishing images
        for file in image_files:
            logger.debug(f"Processing file: {file}")
            if not file or not file.exists() or str(file).strip() == '':
                logger.warning(f"Skipping invalid file path: {file}")
                continue
            try:
                if not file.exists():
                    logger.warning(f"File does not exist: {file}")
                    continue

                with open(file, 'rb') as photo:
                    response = session.post(upload_url, files={'photo': photo}).json()
                    logger.debug(f"Upload response: {response}")

                if 'photo' not in response or response['photo'] == '':
                    logger.error(f"Upload failed for {file.name}: {response}")
                    continue

                vk_photo_response = await self.vk_client.put_photos_save_wall_photo(group_id=your_group,
                                                                                    response=response)
                logger.debug(f"Save wall photo response: {vk_photo_response}")

                if not vk_photo_response or len(vk_photo_response) == 0:
                    logger.error(f"Failed to save photo for {file.name}")
                    continue

                vk_photo = vk_photo_response[0]
                photo_id = f"photo{vk_photo['owner_id']}_{vk_photo['id']}"
                photos.append(photo_id)

                success = os_manager.delete_file(file)
                if not success:
                    logger.warning(f"Could not delete file: {file}")
            except Exception as e:
                logger.error(f'Failed to process {file}: {e}')
                continue

            if len(photos) == max_photos:
                await self.post_maker(delay, your_group, now, photos, group_chat_reminder_text, hashtags,
                                      group_chat_link, group_chat_reminder, text)
                delay += time_delay
                photos.clear()

        logger.info('Finished preparing postponed posts.')
        return

    async def post_maker(self, delay: int, group: int, start_time: int, photos: List[Any],
                         group_chat_reminder_text: str, hashtags: str, group_chat_link: str,
                         group_chat_reminder: int, text: str) -> None:
        if not photos or len(photos) == 0:
            logger.warning("No photos to create post, skipping")
            return

        # Calculate the publishing date as a Unix timestamp
        publish_date = int(start_time + delay)

        file_manipulation = FileContentUtils()

        quotes_for_post = file_manipulation.delete_duplicates_from_text_db(text)

        p = await self.vk_client.get_group_posts(group_id=-int(group), post_filter='postponed')
        if len(group_chat_reminder_text) == 0 and len(hashtags) == 0:
            await self.vk_client.put_post(group_id=group, publish_date=publish_date, attachments=photos)
            logger.info('Post was published. Going to schedule next post.')
        else:
            if (int(round((publish_date % time.time()) / 60 / 60 / 24, 0)) % group_chat_reminder == 0 and
                    (p['count'] > 0 and
                     (group_chat_link not in p['items'][-1]['text'] and group_chat_link not in p['items'][-2][
                         'text']))):
                await self.vk_client.put_post(group_id=group,
                                              publish_date=publish_date,
                                              attachments=photos,
                                              message=str(group_chat_reminder_text).format(group_chat_link, hashtags,
                                                                                           '\n\n'))
                logger.info('Post about CHAT was published. Going to schedule next post.')
            else:
                await self.vk_client.put_post(group_id=group,
                                              publish_date=publish_date,
                                              attachments=photos,
                                              message=random.choice(quotes_for_post) + '\n\n' + hashtags)
                logger.info('Post was published. Going to schedule next post.')

        photos.clear()

    @staticmethod
    async def download_image(session: aiohttp.ClientSession, url: str, path: str, retries: int = 10) -> bool:
        """Download image with retries on failure."""
        for attempt in range(retries):
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    if resp.status == 200:
                        with open(path, 'wb') as f:
                            f.write(await resp.read())
                        logger.info(f"Downloaded: {path}")
                        return True
                    logger.warning(f"HTTP {resp.status}: {url} (attempt {attempt + 1}/{retries})")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.error(f"Failed: {url} - {type(e).__name__} (attempt {attempt + 1}/{retries})")
            await asyncio.sleep(1)
        return False

    async def fetch_group_posts(
            self,
            group_id: str,
            max_posts: Optional[int] = None,
            batch_size: int = 100
    ) -> List[Dict]:
        """
        Fetch posts from VK group with pagination.
        """
        try:
            # Используем VKClient
            total_response = await self.vk_client.get_group_posts(
                group_id=group_id,
                count=1
            )
            total_count = total_response.get('count', 0)

        except Exception as e:
            logger.error(f"Failed to get wall length for group {group_id}: {e}")
            return []

        post_limit = min(max_posts, total_count) if max_posts else total_count
        if post_limit <= 0:
            return []

        all_posts = []
        offset = 0

        while offset < post_limit:
            try:
                current_batch = min(batch_size, post_limit - offset)

                posts = await self.vk_client.get_group_posts(
                    group_id=group_id,
                    count=current_batch,
                    offset=offset
                )

                if not posts.get('items'):
                    break

                all_posts.extend(posts['items'])
                offset += len(posts['items'])
                logger.info(f"Group {group_id}: loaded {len(all_posts)}/{post_limit} posts")

                await asyncio.sleep(0.5)  # Rate limiting

            except Exception as e:
                logger.error(f"Error loading posts for group {group_id} (offset={offset}): {e}")
                break

        return all_posts

    @staticmethod
    def extract_image_urls(posts: List[Dict]) -> List[str]:
        """
        Извлекает URL изображений из постов
        """
        urls = []
        for post in posts:
            if post.get('marked_as_ads', 0) == 1:
                continue
            for att in post.get('attachments', []):
                if att.get('type') == 'photo' and 'sizes' in att.get('photo', {}):
                    urls.append(max(att['photo']['sizes'], key=lambda x: x['width'])['url'])
        return urls

    async def download_images_from_groups(
            self,
            folder: Path,
            group_ids: List[int],
            max_posts_per_group: Optional[int] = None,
            max_concurrent: int = 10
    ) -> Dict[str, int]:
        """
        Основной метод для загрузки изображений из групп
        """
        # Создаем папку
        self.os_manager.ensure_folder_exists(folder)

        stats = {
            'total_downloaded': 0,
            'total_failed': 0,
            'groups_processed': 0
        }

        connector = aiohttp.TCPConnector(limit=max_concurrent)
        timeout = aiohttp.ClientTimeout(total=60)

        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            for group_id in group_ids:
                try:
                    vk_group_id = f"-{group_id}"

                    posts = await self.fetch_group_posts(vk_group_id, max_posts=max_posts_per_group)

                    if not posts:
                        logger.warning(f"No posts found in group {group_id}")
                        continue

                    # Извлекаем URL изображений
                    urls = self.extract_image_urls(posts)

                    if not urls:
                        logger.warning(f"No images found in group {group_id}")
                        continue

                    # Загружаем изображения
                    tasks = []
                    image_counter = stats['total_downloaded'] + 1

                    for url in urls:
                        path = folder / f"img_{image_counter}.jpg"
                        tasks.append(self.download_image(session, url, str(path)))
                        image_counter += 1

                    results = await asyncio.gather(*tasks)
                    successful = sum(results)
                    failed = len(results) - successful

                    stats['total_downloaded'] += successful
                    stats['total_failed'] += failed
                    stats['groups_processed'] += 1

                    logger.info(f"Group {group_id}: {successful}/{len(urls)} images downloaded")

                except Exception as e:
                    logger.error(f"Error processing group {group_id}: {e}")
                    continue

        logger.info(f"Download completed: {stats['total_downloaded']} downloaded, {stats['total_failed']} failed")
        return stats

    async def analyze_group(self, group_id: str):
        """Анализ группы"""
        pass
        #members = await self.vk_client.get_group_members(group_id)
