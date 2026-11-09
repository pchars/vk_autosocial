import re
from typing import List, Optional
from urllib.parse import parse_qs, urlparse

VK_HOSTS = {"vk.com", "vk.ru", "m.vk.com", "m.vk.ru", "www.vk.com", "www.vk.ru"}
WALL_RE = re.compile(r"^wall-(\d+)_\d+$", re.IGNORECASE)
PREFIX_RE = re.compile(r"^(?:club|public|event)(\d+)$", re.IGNORECASE)
SCREEN_NAME_RE = re.compile(r"^[a-z0-9_.]{2,64}$", re.IGNORECASE)
RESERVED_PATHS = {"feed", "im", "friends", "groups", "video", "music", "away.php", "login", "id"}


def community_ref(text: str) -> Optional[str]:
    """Turn a VK link, @alias, club123 or a bare id into a value groups.getById accepts."""
    token = (text or "").strip().strip("<>").rstrip(".,;)")
    if not token:
        return None
    if token.startswith("@"):
        token = token[1:]
    elif "/" in token or token.lower().startswith(("http", "vk.", "m.vk.", "www.vk.")):
        token = _path_from_url(token)
        if token is None:
            return None
    if token.isdigit():
        return token
    for pattern in (WALL_RE, PREFIX_RE):
        match = pattern.match(token)
        if match:
            return match.group(1)
    if token.lower().startswith("id") and token[2:].isdigit():
        return None
    if token.lower() in RESERVED_PATHS or not SCREEN_NAME_RE.match(token):
        return None
    return token


def community_refs(text: str) -> List[str]:
    """All distinct communities mentioned in a message, in order."""
    refs: List[str] = []
    for chunk in re.split(r"[\s,]+", text or ""):
        ref = community_ref(chunk)
        if ref and ref not in refs:
            refs.append(ref)
    return refs


def find_community_ref(text: str) -> Optional[str]:
    """The message itself as a reference, or else the first VK link written inside it."""
    ref = community_ref(text)
    if ref:
        return ref
    for chunk in re.split(r"[\s,]+", text or ""):
        if mentions_vk(chunk):
            ref = community_ref(chunk)
            if ref:
                return ref
    return None


def mentions_vk(text: str) -> bool:
    """True for messages that are clearly about a VK community, not free text."""
    for chunk in re.split(r"[\s,]+", text or ""):
        lower = chunk.lower()
        if any(host + "/" in lower for host in VK_HOSTS) or PREFIX_RE.match(lower) or lower.startswith("@"):
            return True
    return False


def _path_from_url(token: str) -> Optional[str]:
    parsed = urlparse(token if "://" in token else "https://" + token)
    if (parsed.hostname or "").lower() not in VK_HOSTS:
        return None
    wall = parse_qs(parsed.query).get("w", [""])[0]
    if WALL_RE.match(wall):
        return wall
    path = parsed.path.strip("/")
    return path.split("/")[0] if path else None
