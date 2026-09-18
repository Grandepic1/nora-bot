import asyncio
import io
import unittest

from aiohttp import ClientSession, web

from app.debug_logging import DebugLogger
from app.waha_handler.bot import WahaBot
from app.waha_handler.context import Context


class FakeResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def raise_for_status(self):
        return None

    async def json(self):
        return {"ok": True}


class FakeHttp:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse()


class FakeImageResponse(FakeResponse):
    async def read(self):
        return b"image-bytes"


class FakeImageHttp(FakeHttp):
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeImageResponse()


class FakeBot:
    def __init__(self, debug_log=None):
        self.http = FakeHttp()
        self.waha_url = "https://waha.example"
        self.api_key = "secret"
        self.debug_log = debug_log or DebugLogger(False)


class ContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_load_image_waits_for_all_streamed_bytes(self):
        async def stream_image(request):
            response = web.StreamResponse(headers={"Content-Type": "image/jpeg"})
            await response.prepare(request)
            await response.write(b"first-chunk")
            await asyncio.sleep(0.05)
            await response.write(b"second-chunk")
            await response.write_eof()
            return response

        app = web.Application()
        app.router.add_get("/api/files/photo.jpg", stream_image)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]

        try:
            async with ClientSession() as http:
                bot = FakeBot()
                bot.http = http
                bot.waha_url = f"http://127.0.0.1:{port}"
                context = Context(bot, {
                    "payload": {
                        "hasMedia": True,
                        "media": {
                            "url": "http://waha.example/api/files/photo.jpg",
                            "mimetype": "image/jpeg",
                        },
                    },
                }, object())

                image = await context.load_image()

            self.assertEqual(image.data, b"first-chunksecond-chunk")
        finally:
            await runner.cleanup()

    async def test_loads_waha_image_from_configured_server(self):
        bot = FakeBot()
        bot.http = FakeImageHttp()
        context = Context(bot, {
            "payload": {
                "hasMedia": True,
                "media": {
                    "url": "http://localhost:3000/api/files/photo.jpg",
                    "mimetype": "image/jpeg",
                    "filename": "photo.jpg",
                },
            },
        }, object())

        image = await context.load_image()

        self.assertEqual(image.data, b"image-bytes")
        self.assertEqual(image.mime_type, "image/jpeg")
        url, kwargs = bot.http.calls[0]
        self.assertEqual(url, "https://waha.example/api/files/photo.jpg")
        self.assertEqual(kwargs["headers"]["X-Api-Key"], "secret")
        self.assertFalse(kwargs["allow_redirects"])

    async def test_rejects_non_waha_media_path(self):
        bot = FakeBot()
        bot.http = FakeImageHttp()
        context = Context(bot, {
            "payload": {
                "hasMedia": True,
                "media": {
                    "url": "http://localhost:3000/admin",
                    "mimetype": "image/png",
                },
            },
        }, object())

        with self.assertRaises(ValueError):
            await context.load_image()
        self.assertEqual(bot.http.calls, [])

    async def test_send_text_is_owned_by_context(self):
        bot = FakeBot()
        context = Context(
            bot,
            {
                "session": "default",
                "payload": {
                    "from": "chat-id",
                    "body": "private message",
                    "id": "message-id",
                },
            },
            object(),
        )

        result = await context.send("response")

        self.assertEqual(result, {"ok": True})
        url, request = bot.http.calls[0]
        self.assertEqual(url, "https://waha.example/api/sendText")
        self.assertEqual(request["json"]["text"], "response")
        self.assertFalse(hasattr(WahaBot, "send_text"))

    async def test_debug_log_does_not_include_message_or_api_key(self):
        output = io.StringIO()
        bot = FakeBot(DebugLogger(True, stream=output))
        context = Context(
            bot,
            {
                "session": "default",
                "payload": {
                    "from": "chat-id",
                    "body": "private-input-canary",
                    "id": "message-id",
                },
            },
            object(),
        )

        await context.send("private-output-canary")

        rendered = output.getvalue()
        self.assertIn("event=waha.request.started", rendered)
        self.assertIn("event=waha.request.completed", rendered)
        self.assertNotIn("private-input-canary", rendered)
        self.assertNotIn("private-output-canary", rendered)
        self.assertNotIn("secret", rendered)

    async def test_delivery_context_has_no_database_or_message_body(self):
        bot = FakeBot()
        context = Context(
            bot,
            {
                "session": "default",
                "payload": {
                    "from": "chat-id",
                    "body": "private message",
                    "id": "message-id",
                },
            },
            object(),
        )

        delivery = context.for_delivery()

        self.assertEqual(delivery.message, "")
        with self.assertRaises(RuntimeError):
            _ = delivery.db

        await delivery.send("delayed response")
        self.assertEqual(len(bot.http.calls), 1)


if __name__ == "__main__":
    unittest.main()
