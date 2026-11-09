import asyncio
import random
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

from api.vk import EXECUTE_LIMIT, VkAuthError, VkClient, VkError, VkFloodError
from utils.logger import get_logger
from utils.store import Community, Store

from .images import image_hash, list_images, wall_jpeg

logger = get_logger(__name__)

# VK rejects publish_date that is not safely in the future.
MIN_PUBLISH_LEAD_SEC = 15 * 60
# VK keeps up to 1500 postponed posts per community; stay a little below.
MAX_POSTPONED = 1400
WALL_PAGE = 100
# wall.post code 214: daily posting limit reached (or no right to post at all).
POST_DENIED_CODE = 214
UPLOAD_ATTEMPTS = 4
UPLOAD_RETRY_DELAY_SEC = 2.0
# Upload servers have no documented rate limit, but photos.saveWallPhoto gets a one-hour
# flood ban (code 9) when an account saves photos too fast. Keep it near one photo a second.
UPLOAD_CONCURRENCY = 4
MIN_WAVE_SEC = 25.0
# Upload servers start refusing files before VK bans saveWallPhoto: stop when a third of a wave is refused.
THROTTLE_SHARE = 3
THROTTLE_MIN_FILES = 3
# About one upload in five fails for good; spare files keep a wave from coming up short.
SPARE_FILES_SHARE = 5
MAX_PUBLISH_FAILURES = 3
REJECTED_DIR = "rejected"


@dataclass
class QueueInfo:
    count: int = 0
    last_date: Optional[int] = None


@dataclass
class PublishStats:
    posts_created: int = 0
    upload_failed: int = 0
    publish_failed: int = 0
    last_date: Optional[int] = None
    seconds: float = 0.0
    denied: bool = False
    throttled: bool = False


def slot_step(posts_per_day: int) -> int:
    return 86400 // max(1, int(posts_per_day))


def schedule_base(last_postponed: Optional[int], now: Optional[int] = None) -> int:
    now_ts = int(time.time() if now is None else now)
    return max(int(last_postponed or 0), now_ts)


def next_publish_date(cursor: int, step: int, now: Optional[int] = None) -> int:
    """Next slot is one step after cursor, and never earlier than now + lead."""
    now_ts = int(time.time() if now is None else now)
    return max(int(cursor) + int(step), now_ts + MIN_PUBLISH_LEAD_SEC)


def next_slot(community: Community, cursor: int, tz: str, now: Optional[int] = None) -> int:
    """Next publish time after cursor: the chosen local hours and weekdays, or evenly spaced posts."""
    now_ts = int(time.time() if now is None else now)
    days = set(community.days)
    hours = community.hours
    if not hours:
        step = slot_step(community.posts_per_day)
        slot = next_publish_date(cursor, step, now_ts)
        zone = ZoneInfo(tz)
        for _ in range(7 * 24):
            if datetime.fromtimestamp(slot, zone).weekday() in days:
                break
            slot += step
        return slot
    earliest = max(int(cursor) + 1, now_ts + MIN_PUBLISH_LEAD_SEC)
    zone = ZoneInfo(tz)
    start = datetime.fromtimestamp(earliest, zone).date()
    for offset in range(8):
        day = start + timedelta(days=offset)
        if day.weekday() not in days:
            continue
        for hour in hours:
            slot = int(datetime(day.year, day.month, day.day, hour, tzinfo=zone).timestamp())
            if slot >= earliest:
                return slot
    raise ValueError("no publish slot within a week")


def posts_needed(community: Community, queued: int) -> int:
    return max(0, min(community.queue_target, MAX_POSTPONED) - int(queued))


def load_quotes(path: Path) -> List[str]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    # One quote per line; a literal \n inside a line is a line break in the post.
    quotes = {line.replace('"', "").replace("\\n", "\n").strip() for line in lines}
    return sorted(q for q in quotes if q)


def chat_message(community: Community) -> str:
    text = community.chat_text.strip() or "Присоединяйтесь к нашему чату!"
    if community.chat_link not in text:
        text = f"{text}\n\n{community.chat_link}"
    return "\n\n".join(part for part in (text, community.hashtags.strip()) if part)


def build_message(community: Community, quote: str, post_number: int) -> str:
    if community.chat_every and community.chat_link and post_number % community.chat_every == 0:
        return chat_message(community)
    return "\n\n".join(part for part in (quote.strip(), community.hashtags.strip()) if part)


def _read_jpeg(path: Path) -> Optional[bytes]:
    try:
        return wall_jpeg(path)
    except OSError:
        logger.warning("Cannot read image %s", path.name)
        return None


class Publisher:
    def __init__(
            self,
            vk: VkClient,
            store: Store,
            quotes_file: Path,
            sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
            timezone: str = "Europe/Moscow",
    ):
        self.vk = vk
        self.store = store
        self.quotes_file = Path(quotes_file)
        self.timezone = timezone
        self.sleep = sleep
        self._upload_url = ""
        self._url_lock = asyncio.Lock()
        self._refused: Set[str] = set()

    async def queue(self, community_id: int) -> QueueInfo:
        params = {"owner_id": -int(community_id), "filter": "postponed", "count": WALL_PAGE}
        wall = await self.vk.call("wall.get", **params) or {}
        items = list(wall.get("items") or [])
        count = int(wall.get("count", len(items)))
        offsets = list(range(WALL_PAGE, min(count, MAX_POSTPONED + WALL_PAGE), WALL_PAGE))
        for start in range(0, len(offsets), EXECUTE_LIMIT):
            pages = await self.vk.execute([
                ("wall.get", {**params, "offset": offset}) for offset in offsets[start:start + EXECUTE_LIMIT]
            ])
            for page in pages:
                if isinstance(page, VkError):
                    raise page
                items += (page or {}).get("items") or []
        dates = [int(item["date"]) for item in items if item.get("date")]
        return QueueInfo(count=count, last_date=max(dates) if dates else None)

    async def publish(
            self,
            community: Community,
            folder: Path,
            max_posts: int,
            queue: Optional[QueueInfo] = None,
    ) -> PublishStats:
        """Schedule posts in waves: parallel uploads, then one execute for photos and one for posts."""
        stats = PublishStats()
        started = time.monotonic()
        files = list_images(folder)
        per_post = community.photos_per_post
        if max_posts <= 0 or len(files) < per_post:
            return stats

        queue = queue or await self.queue(community.vk_id)
        cursor = schedule_base(queue.last_date)
        quotes = load_quotes(self.quotes_file)
        posts_per_wave = max(1, EXECUTE_LIMIT // per_post)
        self._upload_url = ""
        pending: List[Tuple[Path, str]] = []
        next_file = 0

        wave_started: Optional[float] = None
        while (stats.posts_created < max_posts and stats.publish_failed < MAX_PUBLISH_FAILURES
               and not stats.denied and not stats.throttled):
            wanted = min(posts_per_wave, max_posts - stats.posts_created) * per_post - len(pending)
            wanted = max(wanted, min(wanted + max(1, wanted // SPARE_FILES_SHARE), EXECUTE_LIMIT))
            batch = files[next_file:next_file + wanted]
            next_file += len(batch)
            if not batch:
                break
            if wave_started is not None:
                await self.sleep(max(0.0, MIN_WAVE_SEC - (time.monotonic() - wave_started)))
            wave_started = time.monotonic()
            self._refused.clear()
            uploaded = await self._upload_many(community.vk_id, batch)
            failed = [path for path, attachment in uploaded if not attachment]
            stats.upload_failed += len(failed)
            if failed and len(failed) == len(uploaded):
                # Every file of the wave failed: VK is down, not the files. Keep them for the next run.
                logger.error("VK rejects uploads for %s, stopping this run", community.vk_id)
                break
            refused = len(self._refused)
            if refused >= THROTTLE_MIN_FILES and refused * THROTTLE_SHARE >= len(batch):
                # Refused files are fine, VK is slowing the account down: keep them and stop before a flood ban.
                logger.warning(
                    "VK refused %s of %s uploads for %s, stopping this run to avoid a flood ban",
                    refused, len(batch), community.vk_id,
                )
                stats.throttled = True
            else:
                for path in failed:
                    _reject(path)
            pending.extend((path, attachment) for path, attachment in uploaded if attachment)

            posts: List[Tuple[List[Tuple[Path, str]], int]] = []
            while len(pending) >= per_post and stats.posts_created + len(posts) < max_posts:
                chunk, pending = pending[:per_post], pending[per_post:]
                cursor = next_slot(community, cursor, self.timezone)
                posts.append((chunk, cursor))
            if posts:
                await self._post_many(community, posts, quotes, stats)
                logger.info("Publishing for %s: %s/%s posts scheduled", community.vk_id, stats.posts_created, max_posts)

        stats.seconds = round(time.monotonic() - started, 1)
        logger.info(
            "Published for %s: %s posts in %ss, %s uploads failed, %s posts failed",
            community.vk_id, stats.posts_created, stats.seconds, stats.upload_failed, stats.publish_failed,
        )
        return stats

    async def _post_many(
            self,
            community: Community,
            posts: List[Tuple[List[Tuple[Path, str]], int]],
            quotes: List[str],
            stats: PublishStats,
    ) -> None:
        calls = []
        for index, (chunk, publish_date) in enumerate(posts):
            number = community.posts_made + stats.posts_created + index + 1
            calls.append(("wall.post", {
                "owner_id": -community.vk_id,
                "from_group": 1,
                "attachments": [attachment for _path, attachment in chunk],
                "publish_date": publish_date,
                "message": build_message(
                    community, random.choice(quotes) if quotes and community.use_quotes else "", number),
                "primary_attachments_mode": "grid",
            }))
        results = await self.vk.execute(calls)

        fatal: Optional[VkError] = None
        for (chunk, publish_date), result in zip(posts, results):
            if isinstance(result, (VkAuthError, VkFloodError)):
                fatal = fatal or result
                continue
            if isinstance(result, VkError) and result.code == POST_DENIED_CODE:
                logger.warning("VK denied wall.post for %s (daily limit or rights): %s", community.vk_id, result)
                stats.denied = True
                continue
            if isinstance(result, VkError):
                logger.error("wall.post failed for %s at %s: %s", community.vk_id, publish_date, result)
                stats.publish_failed += 1
                continue
            hashes = [await asyncio.to_thread(image_hash, path) for path, _a in chunk]
            self.store.add_posted_hashes(community.vk_id, [h for h in hashes if h is not None])
            for path, _a in chunk:
                path.unlink(missing_ok=True)
            self.store.increment_posts_made(community.vk_id)
            stats.posts_created += 1
            stats.last_date = max(stats.last_date or 0, publish_date)
        if fatal:
            raise fatal

    async def _upload_many(self, group_id: int, paths: List[Path]) -> List[Tuple[Path, Optional[str]]]:
        """Upload pictures in parallel and save them with batched photos.saveWallPhoto calls."""
        payloads = await asyncio.gather(*(asyncio.to_thread(_read_jpeg, path) for path in paths))
        gate = asyncio.Semaphore(UPLOAD_CONCURRENCY)

        async def upload(path: Path, data: Optional[bytes]) -> Optional[Dict[str, Any]]:
            if data is None:
                return None
            async with gate:
                return await self._upload_file(group_id, path, data)

        answers = await asyncio.gather(*(upload(p, d) for p, d in zip(paths, payloads)))
        attachments: List[Optional[str]] = [None] * len(paths)
        ready = [(i, answer) for i, answer in enumerate(answers) if answer]
        for start in range(0, len(ready), EXECUTE_LIMIT):
            part = ready[start:start + EXECUTE_LIMIT]
            results = await self.vk.execute([
                ("photos.saveWallPhoto", {
                    "group_id": group_id, "server": a["server"], "photo": a["photo"], "hash": a["hash"],
                })
                for _i, a in part
            ])
            for (index, _answer), result in zip(part, results):
                if isinstance(result, (VkAuthError, VkFloodError)):
                    raise result
                if isinstance(result, VkError) or not result:
                    logger.warning("saveWallPhoto failed for %s: %s", paths[index].name, result)
                    continue
                attachments[index] = f"photo{result[0]['owner_id']}_{result[0]['id']}"
        return list(zip(paths, attachments))

    async def _upload_file(self, group_id: int, path: Path, data: bytes) -> Optional[Dict[str, Any]]:
        """VK upload servers intermittently answer 500/405 or an empty photo; retry on a fresh URL."""
        for attempt in range(1, UPLOAD_ATTEMPTS + 1):
            url = await self._wall_upload_url(group_id)
            answer = await self.vk.upload(url, "photo", f"{path.stem}.jpg", data)
            if answer.get("photo") not in (None, "", "[]"):
                return answer
            logger.warning("Upload of %s rejected (attempt %s/%s)", path.name, attempt, UPLOAD_ATTEMPTS)
            self._refused.add(path.name)
            if self._upload_url == url:
                self._upload_url = ""
            if attempt < UPLOAD_ATTEMPTS:
                await self.sleep(UPLOAD_RETRY_DELAY_SEC * attempt)
        return None

    async def _wall_upload_url(self, group_id: int) -> str:
        async with self._url_lock:
            if not self._upload_url:
                server = await self.vk.call("photos.getWallUploadServer", group_id=group_id)
                self._upload_url = server["upload_url"]
            return self._upload_url

    async def clear(self, community_id: int, published: bool = False) -> int:
        """Delete postponed (or published) posts, 25 per request. Returns how many were removed."""
        deleted = 0
        post_filter = "owner" if published else "postponed"
        while True:
            wall = await self.vk.call("wall.get", owner_id=-int(community_id), filter=post_filter, count=100)
            items = (wall or {}).get("items") or []
            removed_now = 0
            for start in range(0, len(items), EXECUTE_LIMIT):
                part = items[start:start + EXECUTE_LIMIT]
                results = await self.vk.execute([
                    ("wall.delete", {"owner_id": -int(community_id), "post_id": item["id"]}) for item in part
                ])
                for item, result in zip(part, results):
                    if isinstance(result, (VkAuthError, VkFloodError)):
                        raise result
                    if isinstance(result, VkError):
                        logger.warning("Cannot delete post %s: %s", item.get("id"), result)
                        continue
                    removed_now += 1
            deleted += removed_now
            if not items or removed_now == 0:
                return deleted


def _reject(path: Path) -> None:
    """Move a picture VK keeps refusing out of the queue without losing it."""
    target = path.parent / REJECTED_DIR
    target.mkdir(exist_ok=True)
    shutil.move(str(path), str(target / path.name))
