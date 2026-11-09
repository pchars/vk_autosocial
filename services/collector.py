import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from api.vk import VkAuthError, VkClient, VkError, VkFloodError
from utils.logger import get_logger
from utils.store import Community, Source, Store

from .images import image_hash, is_duplicate, list_images

logger = get_logger(__name__)

PAGE_SIZE = 100
MAX_PAGES = 3
# Look through this many candidates per needed picture so the best ones can be chosen.
SELECTION_FACTOR = 3
DOWNLOAD_BATCH = 16
PROGRESS_EVERY_BATCHES = 4

Candidate = Tuple[str, str]


@dataclass
class CollectStats:
    downloaded: int = 0
    duplicates: int = 0
    broken: int = 0
    failed_sources: List[str] = field(default_factory=list)


def best_photo_url(photo: Dict) -> str:
    orig = (photo.get("orig_photo") or {}).get("url")
    if orig:
        return orig
    sizes = photo.get("sizes") or []
    if not sizes:
        return ""
    return max(sizes, key=lambda s: int(s.get("width", 0)) * int(s.get("height", 0))).get("url", "")


AD_MARKS = re.compile(r"\berid\b|(?<![а-яё])реклам|#ad\b|\bинн\b", re.I)
LINKS = re.compile(
    r"https?://|\b(vk\.cc|vk\.me|t\.me|clck\.ru)/|\b[\w-]+\.(ru|рф|com|shop|store|online|site)\b", re.I)
MENTIONS = re.compile(r"\[(club|public)\d+\|", re.I)
CREDIT = re.compile(r"^\s*(автор|арт|art|by|фото|photo)\b", re.I)
PROMO = re.compile(
    r"\b(розыгрыш|конкурс|промокод|скидк|взаимопиар|подпис(ыв)?ай(ся|тесь)|магазин|в наличии|закажи|заказать"
    r"|для покупки|купить|цена\b|цены\b|стоимость)|₽",
    re.I)
HANDMADE = re.compile(
    r"\b(х[еэ]н[дт]?м[еэ]йд|handmade|hand made|ручн\w* работ|сделан\w* вручную|мастерск"
    r"|(сделан|создан|изготовлен)\w* своими руками|(создаю|делаю|авторск\w*|уникальн\w*)\s+(\w+\s+)?украшени"
    r"|бижутер|брош|серьг|серёж|сереж|колечк|бусик|бусин|подвеск|кулон|браслет)",
    re.I)


def is_ad(post: Dict) -> bool:
    """Marked ads and the usual hidden ones: reposts, links, other communities, sales, giveaways and handmade."""
    if post.get("marked_as_ads") or post.get("copy_history"):
        return True
    if any(a.get("type") == "link" for a in post.get("attachments") or []):
        return True
    text = post.get("text") or ""
    if MENTIONS.search(text) and not CREDIT.match(text):
        return True
    return any(rule.search(text) for rule in (AD_MARKS, LINKS, PROMO, HANDMADE))


def extract_photos(posts: Iterable[Dict]) -> List[Candidate]:
    """(photo key, url) pairs from wall posts, skipping ads and pinned posts."""
    out: List[Candidate] = []
    for post in posts:
        if post.get("is_pinned") or is_ad(post):
            continue
        for attachment in post.get("attachments") or []:
            photo = attachment.get("photo") if attachment.get("type") == "photo" else None
            if not photo:
                continue
            url = best_photo_url(photo)
            if url:
                out.append((f"{photo['owner_id']}_{photo['id']}", url))
    return out


def popularity(post: Dict) -> float:
    """Likes per view: unlike raw likes, it does not favour old posts or big sources."""
    views = int((post.get("views") or {}).get("count", 0))
    likes = int((post.get("likes") or {}).get("count", 0))
    return likes / views if views else 0.0


def interleave(groups: Sequence[List[Candidate]]) -> List[Candidate]:
    """Take one picture from each source in turn so posts mix sources."""
    out: List[Candidate] = []
    for index in range(max((len(g) for g in groups), default=0)):
        out.extend(group[index] for group in groups if index < len(group))
    return out


class Collector:
    def __init__(self, vk: VkClient, store: Store):
        self.vk = vk
        self.store = store

    async def collect(self, community: Community, sources: List[Source], folder: Path, need: int) -> CollectStats:
        stats = CollectStats()
        if need <= 0 or not sources:
            return stats
        folder.mkdir(parents=True, exist_ok=True)
        seen = self.store.seen_keys(community.vk_id)
        candidates = await self._candidates(sources, seen, need, stats)

        known = self.store.posted_hashes(community.vk_id)
        for path in list_images(folder):
            existing = await asyncio.to_thread(image_hash, path)
            if existing is not None:
                known.append(existing)

        logger.info("Collecting %s pictures for %s from %s candidates", need, community.vk_id, len(candidates))
        for start in range(0, len(candidates), DOWNLOAD_BATCH):
            batch = candidates[start:start + DOWNLOAD_BATCH]
            payloads = await asyncio.gather(*(self.vk.download(url) for _key, url in batch))
            new_seen: List[str] = []
            for (key, _url), data in zip(batch, payloads):
                if stats.downloaded >= need:
                    break
                if data is None:
                    stats.broken += 1
                    continue
                new_seen.append(key)
                value = await asyncio.to_thread(image_hash, data)
                if value is None:
                    stats.broken += 1
                elif is_duplicate(value, known):
                    stats.duplicates += 1
                else:
                    known.append(value)
                    (folder / f"{key}.jpg").write_bytes(data)
                    stats.downloaded += 1
            self.store.mark_seen(community.vk_id, new_seen)
            if (start // DOWNLOAD_BATCH) % PROGRESS_EVERY_BATCHES == PROGRESS_EVERY_BATCHES - 1:
                logger.info("Collecting for %s: %s/%s downloaded", community.vk_id, stats.downloaded, need)
            if stats.downloaded >= need:
                break

        logger.info(
            "Collected for %s: %s new, %s duplicates, %s broken, failed sources %s",
            community.vk_id, stats.downloaded, stats.duplicates, stats.broken, stats.failed_sources,
        )
        return stats

    async def _candidates(self, sources: List[Source], seen: set, need: int, stats: CollectStats) -> List[Candidate]:
        posts: Dict[int, List[Dict]] = {s.vk_id: [] for s in sources}
        active = list(sources)
        picked = set(seen)
        found = 0
        wanted = need * SELECTION_FACTOR
        for page in range(MAX_PAGES):
            for source in list(active):
                try:
                    wall = await self.vk.call(
                        "wall.get", owner_id=-source.vk_id, count=PAGE_SIZE, offset=page * PAGE_SIZE
                    )
                except (VkAuthError, VkFloodError):
                    raise
                except VkError as exc:
                    logger.warning("Source %s is unavailable: %s", source.vk_id, exc)
                    stats.failed_sources.append(source.title)
                    active.remove(source)
                    continue
                items = (wall or {}).get("items") or []
                posts[source.vk_id].extend(items)
                for key, _url in extract_photos(items):
                    if key not in picked:
                        picked.add(key)
                        found += 1
                if len(items) < PAGE_SIZE:
                    active.remove(source)
            if not active or found >= wanted:
                break
        taken = set(seen)
        ranked: List[List[Candidate]] = []
        for source in sources:
            best_first: List[Candidate] = []
            for key, url in extract_photos(sorted(posts[source.vk_id], key=popularity, reverse=True)):
                if key not in taken:
                    taken.add(key)
                    best_first.append((key, url))
            ranked.append(best_first)
        return interleave(ranked)
