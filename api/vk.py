import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

import aiohttp

from utils.logger import get_logger

logger = get_logger(__name__)

API_URL = "https://api.vk.ru/method/"
MIN_INTERVAL_SEC = 0.34  # user tokens: 3 requests per second
NETWORK_ATTEMPTS = 3
EXECUTE_LIMIT = 25
AUTH_CODES = {5, 1116}
RETRY_CODES = {6, 10}
COOLDOWN_SEC = {9: 60 * 60, 14: 60 * 60, 29: 6 * 60 * 60}
MEDIA_HOSTS = ("vkuserphoto.ru", "userapi.com", "vk-cdn.net", "vkuser.net", "vk.com", "vk.ru", "mycdn.me")
MAX_DOWNLOAD_BYTES = 30 * 1024 * 1024


def is_vk_media_url(url: str) -> bool:
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host == h or host.endswith("." + h) for h in MEDIA_HOSTS)


class VkError(Exception):
    def __init__(self, code: int, message: str, method: str = ""):
        super().__init__(f"[{code}] {message}" + (f" ({method})" if method else ""))
        self.code = code
        self.message = message
        self.method = method


class VkAuthError(VkError):
    """The token was revoked or expired."""


class VkFloodError(VkError):
    """VK temporarily blocks this kind of action."""

    def __init__(self, code: int, message: str, method: str = "", retry_after: int = 0):
        super().__init__(code, message, method)
        self.retry_after = int(retry_after)


class VkClient:
    def __init__(
            self,
            token: str,
            api_version: str = "5.199",
            cooldown_file: Optional[Path] = None,
            session: Optional[aiohttp.ClientSession] = None,
            min_interval: float = MIN_INTERVAL_SEC,
    ):
        self.token = token
        self.api_version = api_version
        self.min_interval = min_interval
        self._session = session
        self._owns_session = session is None
        self._lock = asyncio.Lock()
        self._last_call = 0.0
        self._cooldown_file = Path(cooldown_file) if cooldown_file else None
        self._cooldowns: Dict[str, float] = self._load_cooldowns()

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90))
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()

    def set_token(self, token: str) -> None:
        self.token = token
        self._cooldowns.clear()
        self._save_cooldowns()

    def cooldown_left(self, method: str) -> int:
        return max(0, int(self._cooldowns.get(method, 0) - time.time()))

    async def call(self, method: str, **params: Any) -> Any:
        return (await self._request(method, params)).get("response")

    async def execute(self, calls: Sequence[Tuple[str, Dict[str, Any]]]) -> List[Any]:
        """Run up to 25 API calls in one request. Failed calls come back as VkError items."""
        if len(calls) > EXECUTE_LIMIT:
            raise ValueError(f"execute accepts at most {EXECUTE_LIMIT} calls")
        if not calls:
            return []
        for method, _params in calls:
            left = self.cooldown_left(method)
            if left:
                raise VkFloodError(9, "cooldown is active", method, retry_after=left)
        code = "return [" + ",".join(
            f"API.{method}({json.dumps({k: _encode(v) for k, v in params.items() if v is not None}, ensure_ascii=False)})"
            for method, params in calls
        ) + "];"
        body = await self._request("execute", {"code": code})
        results = list(body.get("response") or [])
        results += [False] * (len(calls) - len(results))
        errors = list(body.get("execute_errors") or [])
        out: List[Any] = []
        for (method, _params), result in zip(calls, results):
            if result is False:
                error = errors.pop(0) if errors else {}
                out.append(self._error(
                    int(error.get("error_code", -1)), str(error.get("error_msg", "call failed")), method
                ))
            else:
                out.append(result)
        return out

    async def _request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        left = self.cooldown_left(method)
        if left:
            raise VkFloodError(9, "cooldown is active", method, retry_after=left)
        payload = {key: _encode(value) for key, value in params.items() if value is not None}
        payload.update(access_token=self.token, v=self.api_version)

        for attempt in range(1, NETWORK_ATTEMPTS + 1):
            try:
                body = await self._post(method, payload)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                if attempt == NETWORK_ATTEMPTS:
                    raise VkError(-1, f"network error: {type(exc).__name__}", method) from exc
                await asyncio.sleep(attempt)
                continue

            error = body.get("error")
            if not error:
                return body
            code = int(error.get("error_code", 0))
            if code in RETRY_CODES and attempt < NETWORK_ATTEMPTS:
                await asyncio.sleep(attempt)
                continue
            raise self._error(code, str(error.get("error_msg", "unknown error")), method)
        raise VkError(-1, "unreachable", method)  # pragma: no cover

    def _error(self, code: int, message: str, method: str) -> VkError:
        if code in AUTH_CODES:
            return VkAuthError(code, message, method)
        if code in COOLDOWN_SEC:
            self._cooldowns[method] = time.time() + COOLDOWN_SEC[code]
            self._save_cooldowns()
            logger.warning("VK limited %s with code %s for %ss", method, code, COOLDOWN_SEC[code])
            return VkFloodError(code, message, method, retry_after=COOLDOWN_SEC[code])
        return VkError(code, message, method)

    async def upload(self, url: str, field: str, filename: str, data: bytes) -> Dict[str, Any]:
        """POST a file to a VK upload server. Returns {} on HTTP errors or non-JSON answers."""
        form = aiohttp.FormData()
        form.add_field(field, data, filename=filename, content_type="image/jpeg")
        try:
            async with self.session.post(url, data=form) as response:
                text = await response.text()
                status = response.status
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning("Upload request failed: %s", type(exc).__name__)
            return {}
        try:
            parsed = json.loads(text)
        except ValueError:
            logger.warning("Upload server answered HTTP %s without JSON", status)
            return {}
        return parsed if isinstance(parsed, dict) else {}

    async def download(self, url: str) -> Optional[bytes]:
        """Only VK photo CDNs, no redirects, bounded size: URLs come from third-party posts."""
        if not is_vk_media_url(url):
            logger.warning("Skipped download from a non-VK host: %s", urlsplit(url).hostname)
            return None
        try:
            async with self.session.get(url, allow_redirects=False) as response:
                if response.status != 200 or (response.content_length or 0) > MAX_DOWNLOAD_BYTES:
                    return None
                data = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    data += chunk
                    if len(data) > MAX_DOWNLOAD_BYTES:
                        return None
                return bytes(data)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

    async def _post(self, method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        async with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with self.session.post(API_URL + method, data=payload) as response:
                    body = await response.json(content_type=None)
            finally:
                self._last_call = time.monotonic()
        if not isinstance(body, dict):
            raise ValueError("VK answered with a non-object body")
        return body

    def _load_cooldowns(self) -> Dict[str, float]:
        if not self._cooldown_file or not self._cooldown_file.exists():
            return {}
        try:
            data = json.loads(self._cooldown_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        now = time.time()
        return {str(k): float(v) for k, v in data.items() if isinstance(v, (int, float)) and v > now}

    def _save_cooldowns(self) -> None:
        if not self._cooldown_file:
            return
        try:
            self._cooldown_file.parent.mkdir(parents=True, exist_ok=True)
            self._cooldown_file.write_text(json.dumps(self._cooldowns), encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not save VK cooldowns: %s", exc)


def _encode(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(item) for item in value)
    return value
