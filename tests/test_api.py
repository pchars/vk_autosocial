import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import aiohttp

from api.vk import VkAuthError, VkClient, VkError, VkFloodError
from api.vk_token import OAUTH_APP_ID, extract_token, oauth_url


class FakeResponse:
    def __init__(self, body: Any = None, status: int = 200, raw: bytes = b"", error: Exception = None):
        self.body = body
        self.status = status
        self.raw = raw
        self.error = error

    async def __aenter__(self) -> "FakeResponse":
        if self.error:
            raise self.error
        return self

    async def __aexit__(self, *_exc) -> None:
        return None

    async def json(self, content_type=None) -> Any:
        if isinstance(self.body, str):
            return json.loads(self.body)
        return self.body

    async def text(self) -> str:
        return self.body if isinstance(self.body, str) else json.dumps(self.body)

    async def read(self) -> bytes:
        return self.raw

    async def iter_chunked(self, size: int):
        for start in range(0, len(self.raw), 2):
            yield self.raw[start:start + 2]

    @property
    def content(self) -> "FakeResponse":
        return self

    @property
    def content_length(self) -> int:
        return len(self.raw) if self.length_header else None

    length_header = False


class FakeSession:
    def __init__(self, responses: List[FakeResponse]):
        self.responses = list(responses)
        self.requests: List[Dict[str, Any]] = []
        self.closed = False

    def post(self, url: str, data: Any = None) -> FakeResponse:
        self.requests.append({"url": url, "data": data})
        return self.responses.pop(0)

    def get(self, url: str, allow_redirects: bool = True) -> FakeResponse:
        self.requests.append({"url": url, "allow_redirects": allow_redirects})
        return self.responses.pop(0)

    async def close(self) -> None:
        self.closed = True


def client(responses: List[FakeResponse], cooldown_file: Path = None) -> VkClient:
    return VkClient("tok", "5.199", cooldown_file=cooldown_file, session=FakeSession(responses), min_interval=0)


def error(code: int, message: str = "oops") -> FakeResponse:
    return FakeResponse({"error": {"error_code": code, "error_msg": message}})


async def no_sleep(_seconds: float) -> None:
    return None


class VkClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        patcher = patch("api.vk.asyncio.sleep", no_sleep)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_call_encodes_params_and_returns_response(self):
        vk = client([FakeResponse({"response": {"ok": 1}})])
        result = await vk.call("wall.post", owner_id=-1, attachments=["a", "b"], from_group=True, skip=None)
        self.assertEqual(result, {"ok": 1})
        request = vk.session.requests[0]
        self.assertEqual(request["url"], "https://api.vk.ru/method/wall.post")
        self.assertEqual(request["data"], {
            "owner_id": -1, "attachments": "a,b", "from_group": 1, "access_token": "tok", "v": "5.199",
        })

    async def test_too_many_requests_is_retried(self):
        vk = client([error(6), FakeResponse({"response": 5})])
        self.assertEqual(await vk.call("users.get"), 5)

    async def test_network_errors_are_retried_then_raised(self):
        vk = client([FakeResponse(error=aiohttp.ClientError()), FakeResponse({"response": 1})])
        self.assertEqual(await vk.call("users.get"), 1)
        vk = client([FakeResponse(error=asyncio.TimeoutError())] * 3)
        with self.assertRaises(VkError) as ctx:
            await vk.call("users.get")
        self.assertEqual(ctx.exception.code, -1)

    async def test_non_object_body_counts_as_network_error(self):
        vk = client([FakeResponse([1, 2])] * 3)
        with self.assertRaises(VkError):
            await vk.call("users.get")

    async def test_auth_error(self):
        vk = client([error(5, "User authorization failed")])
        with self.assertRaises(VkAuthError) as ctx:
            await vk.call("users.get")
        self.assertIn("users.get", str(ctx.exception))

    async def test_generic_error_keeps_code(self):
        vk = client([error(15, "Access denied")])
        with self.assertRaises(VkError) as ctx:
            await vk.call("wall.get")
        self.assertEqual((ctx.exception.code, ctx.exception.message), (15, "Access denied"))
        self.assertNotIsInstance(ctx.exception, (VkAuthError, VkFloodError))

    async def test_execute_batches_calls_and_maps_failed_items(self):
        vk = client([FakeResponse({
            "response": [{"post_id": 1}, False, False],
            "execute_errors": [
                {"method": "wall.post", "error_code": 100, "error_msg": "bad date"},
                {"method": "wall.post", "error_code": 9, "error_msg": "Flood control"},
            ],
        })])
        results = await vk.execute([
            ("wall.post", {"owner_id": -1, "attachments": ["photo1_1", "photo1_2"], "message": "Привет \"мир\""}),
            ("wall.post", {"owner_id": -1, "skip": None}),
            ("wall.post", {"owner_id": -1, "from_group": True}),
        ])
        self.assertEqual(results[0], {"post_id": 1})
        self.assertEqual((type(results[1]), results[1].code), (VkError, 100))
        self.assertIsInstance(results[2], VkFloodError)
        self.assertGreater(vk.cooldown_left("wall.post"), 0)

        request = vk.session.requests[0]
        self.assertTrue(request["url"].endswith("/execute"))
        code = request["data"]["code"]
        self.assertEqual(code.count("API.wall.post("), 3)
        first = code[len("return [API.wall.post("):code.index(")")]
        self.assertEqual(json.loads(first), {
            "owner_id": -1, "attachments": "photo1_1,photo1_2", "message": "Привет \"мир\"",
        })
        self.assertIn('{"owner_id": -1, "from_group": 1}', code)

        with self.assertRaises(VkFloodError):
            await vk.execute([("wall.post", {})])

    async def test_execute_limits(self):
        vk = client([FakeResponse({"response": [1]})])
        self.assertEqual(await vk.execute([]), [])
        with self.assertRaises(ValueError):
            await vk.execute([("users.get", {})] * 26)
        results = await vk.execute([("users.get", {}), ("users.get", {})])
        self.assertEqual(results[0], 1)
        self.assertIsInstance(results[1], VkError)

    async def test_execute_whole_request_error_raises(self):
        vk = client([error(5, "User authorization failed")])
        with self.assertRaises(VkAuthError):
            await vk.execute([("users.get", {})])

    async def test_flood_blocks_only_that_method_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cooldowns.json"
            vk = client([error(9, "Flood control"), FakeResponse({"response": []})], cooldown_file=path)
            with self.assertRaises(VkFloodError) as ctx:
                await vk.call("wall.post")
            self.assertEqual(ctx.exception.retry_after, 3600)
            self.assertEqual(await vk.call("wall.get"), [])
            with self.assertRaises(VkFloodError):
                await vk.call("wall.post")
            self.assertEqual(len(vk.session.requests), 2)

            restarted = client([], cooldown_file=path)
            self.assertGreater(restarted.cooldown_left("wall.post"), 3500)
            restarted.set_token("new")
            self.assertEqual(restarted.cooldown_left("wall.post"), 0)
            self.assertEqual(json.loads(path.read_text()), {})

    async def test_broken_or_expired_cooldown_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            path.write_text("not json")
            self.assertEqual(client([], cooldown_file=path).cooldown_left("x"), 0)
            path.write_text(json.dumps({"x": time.time() - 10, "y": "bad"}))
            self.assertEqual(client([], cooldown_file=path).cooldown_left("x"), 0)

    async def test_upload_parses_json_and_survives_http_errors(self):
        vk = client([
            FakeResponse('{"server": 1, "photo": "p", "hash": "h"}'),
            FakeResponse("<html>500</html>", status=500),
            FakeResponse(error=aiohttp.ClientError()),
            FakeResponse("[1]"),
        ])
        self.assertEqual(await vk.upload("u", "photo", "a.jpg", b"x"), {"server": 1, "photo": "p", "hash": "h"})
        self.assertEqual(await vk.upload("u", "photo", "a.jpg", b"x"), {})
        self.assertEqual(await vk.upload("u", "photo", "a.jpg", b"x"), {})
        self.assertEqual(await vk.upload("u", "photo", "a.jpg", b"x"), {})

    async def test_download(self):
        url = "https://sun9-37.vkuserphoto.ru/s/v1/ig2/a.jpg"
        vk = client([
            FakeResponse(raw=b"image"),
            FakeResponse(status=404),
            FakeResponse(error=aiohttp.ClientError()),
            FakeResponse(status=302),
        ])
        self.assertEqual(await vk.download(url), b"image")
        self.assertFalse(vk.session.requests[0]["allow_redirects"])
        self.assertIsNone(await vk.download(url))
        self.assertIsNone(await vk.download(url))
        self.assertIsNone(await vk.download(url))

    async def test_download_refuses_foreign_hosts_and_huge_files(self):
        vk = client([])
        for url in ("http://sun9-37.vkuserphoto.ru/a.jpg", "https://127.0.0.1/a.jpg",
                    "https://evilvkuserphoto.ru/a.jpg", "https://vkuserphoto.ru.evil.com/a.jpg", "u"):
            self.assertIsNone(await vk.download(url), url)
        self.assertEqual(vk.session.requests, [])

        url = "https://pp.userapi.com/a.jpg"
        with patch("api.vk.MAX_DOWNLOAD_BYTES", 3):
            declared = FakeResponse(raw=b"12345")
            declared.length_header = True
            vk = client([declared, FakeResponse(raw=b"12345"), FakeResponse(raw=b"123")])
            self.assertIsNone(await vk.download(url))
            self.assertIsNone(await vk.download(url))
            self.assertEqual(await vk.download(url), b"123")

    async def test_own_session_is_created_and_closed(self):
        vk = VkClient("tok")
        session = vk.session
        self.assertIs(vk.session, session)
        await vk.close()
        self.assertTrue(session.closed)
        borrowed = FakeSession([])
        vk = VkClient("tok", session=borrowed)
        await vk.close()
        self.assertFalse(borrowed.closed)

    async def test_calls_are_spaced_out(self):
        waits: List[float] = []

        async def record(seconds: float) -> None:
            waits.append(seconds)

        vk = VkClient("tok", session=FakeSession([FakeResponse({"response": 1})] * 2), min_interval=0.5)
        with patch("api.vk.asyncio.sleep", record):
            await vk.call("a")
            await vk.call("b")
        self.assertEqual(len(waits), 1)
        self.assertGreater(waits[0], 0.4)


class VkTokenTests(unittest.TestCase):
    def test_oauth_url(self):
        url = oauth_url("5.199")
        self.assertTrue(url.startswith("https://oauth.vk.ru/authorize?"))
        self.assertIn(f"client_id={OAUTH_APP_ID}", url)
        self.assertIn("response_type=token", url)
        self.assertNotIn("messages", url)

    def test_extract_token(self):
        token = "vk1.a." + "x" * 40
        self.assertEqual(extract_token(f"https://oauth.vk.ru/blank.html#access_token={token}&expires_in=0"), token)
        self.assertEqual(extract_token(f" https://oauth.vk.com/blank.html#access_token={token} "), token)
        self.assertEqual(extract_token(f"  {token} "), token)
        for wrong in (f"https://evil.ru/blank.html#access_token={token}",
                      f"http://oauth.vk.ru/blank.html#access_token={token}",
                      f"https://oauth.vk.ru/other#access_token={token}",
                      f"смотри https://oauth.vk.ru/blank.html#access_token={token}"):
            self.assertIsNone(extract_token(wrong), wrong)
        self.assertIsNone(extract_token("hello"))
        self.assertIsNone(extract_token(""))


if __name__ == "__main__":
    unittest.main()
