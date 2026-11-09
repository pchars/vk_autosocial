import asyncio
from pathlib import Path

from api.vk import VkAuthError, VkClient, VkError, VkFloodError
from utils.logger import get_logger
from utils.store import Store

from .images import image_hash, list_images, story_jpeg

logger = get_logger(__name__)

MAX_FAILURES = 3


class StoryPublisher:
    def __init__(self, vk: VkClient, store: Store):
        self.vk = vk
        self.store = store

    async def publish(self, community_id: int, folder: Path, count: int) -> int:
        published = failures = 0
        for path in list_images(folder):
            if published >= count or failures >= MAX_FAILURES:
                break
            try:
                data = await asyncio.to_thread(story_jpeg, path)
            except OSError:
                logger.warning("Cannot read image %s, removing it", path.name)
                path.unlink(missing_ok=True)
                continue
            try:
                server = await self.vk.call("stories.getPhotoUploadServer", group_id=community_id, add_to_news=1)
                answer = await self.vk.upload(server["upload_url"], "file", f"{path.stem}.jpg", data)
                result = (answer.get("response") or {}).get("upload_result")
                if not result:
                    raise VkError(-1, "empty story upload result", "upload")
                await self.vk.call("stories.save", upload_results=result)
            except (VkAuthError, VkFloodError):
                raise
            except VkError as exc:
                failures += 1
                logger.warning("Story upload failed for %s: %s", path.name, exc)
                continue
            value = await asyncio.to_thread(image_hash, path)
            if value is not None:
                self.store.add_posted_hashes(community_id, [value])
            path.unlink(missing_ok=True)
            published += 1
        logger.info("Published %s stories for %s", published, community_id)
        return published
