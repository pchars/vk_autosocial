import io
import random
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from PIL import Image

from api.vk import VkError
from utils.config import Config
from utils.store import Store


def make_jpeg(seed: int, size: Tuple[int, int] = (90, 80)) -> bytes:
    """A picture with a random block pattern, so different seeds never look like duplicates."""
    rng = random.Random(seed)
    image = Image.new("L", (9, 8))
    image.putdata([rng.randrange(256) for _ in range(72)])
    image = image.resize(size, Image.Resampling.NEAREST).convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def photo(owner_id: int, photo_id: int, url: Optional[str] = None) -> Dict[str, Any]:
    return {
        "type": "photo",
        "photo": {
            "owner_id": owner_id,
            "id": photo_id,
            "sizes": [
                {"width": 10, "height": 10, "url": "small"},
                {"width": 100, "height": 80, "url": url or f"https://img/{owner_id}_{photo_id}"},
            ],
        },
    }


class FakeVk:
    """Stand-in for VkClient: methods answer from a table, everything is recorded."""

    def __init__(self, handlers: Optional[Dict[str, Any]] = None):
        self.token = "token"
        self.handlers: Dict[str, Any] = dict(handlers or {})
        self.calls: List[Tuple[str, Dict[str, Any]]] = []
        self.uploads: List[Tuple[str, str, str]] = []
        self.upload_answers: List[Dict[str, Any]] = []
        self.files: Dict[str, bytes] = {}
        self.upload_failures: Dict[str, int] = {}
        self.executes = 0
        self.closed = False

    async def call(self, method: str, **params: Any) -> Any:
        self.calls.append((method, params))
        if method not in self.handlers:
            raise AssertionError(f"Unexpected VK call {method} {params}")
        handler = self.handlers[method]
        if isinstance(handler, BaseException):
            raise handler
        result = handler(**params) if callable(handler) else handler
        if isinstance(result, BaseException):
            raise result
        return result

    async def execute(self, calls: List[Tuple[str, Dict[str, Any]]]) -> List[Any]:
        assert len(calls) <= 25, "VK execute takes at most 25 calls"
        self.executes += 1
        results: List[Any] = []
        for method, params in calls:
            try:
                results.append(await self.call(method, **params))
            except VkError as exc:
                results.append(exc)
        return results

    async def upload(self, url: str, field: str, filename: str, data: bytes) -> Dict[str, Any]:
        self.uploads.append((url, field, filename))
        if self.upload_failures.get(filename, 0) > 0:
            self.upload_failures[filename] -= 1
            return {}
        if self.upload_answers:
            return self.upload_answers.pop(0)
        if field == "file":
            return {"response": {"upload_result": f"story-{filename}"}}
        return {"server": 1, "photo": f"[{filename}]", "hash": "h"}

    async def download(self, url: str) -> Optional[bytes]:
        return self.files.get(url)

    def set_token(self, token: str) -> None:
        self.token = token

    async def close(self) -> None:
        self.closed = True

    def methods(self) -> List[str]:
        return [method for method, _params in self.calls]

    def params(self, method: str) -> List[Dict[str, Any]]:
        return [params for name, params in self.calls if name == method]


def counter(start: int = 1) -> Callable[..., Any]:
    """saveWallPhoto handler that returns a new photo id on every call."""
    state = {"next": start}

    def handler(**_params: Any) -> List[Dict[str, int]]:
        state["next"] += 1
        return [{"owner_id": -1, "id": state["next"]}]

    return handler


class TempDirTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.config = Config(data_dir=self.tmp / "data", texts_file=self.tmp / "texts.txt")
        self.store = Store(self.config.db_path)
        self.store.init()


class AsyncTempDirTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        TempDirTestCase.setUp(self)  # type: ignore[arg-type]
