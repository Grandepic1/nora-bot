import io
import unittest

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


class FakeBot:
    def __init__(self, debug_log=None):
        self.http = FakeHttp()
        self.waha_url = "https://waha.example"
        self.api_key = "secret"
        self.debug_log = debug_log or DebugLogger(False)


class ContextTests(unittest.IsolatedAsyncioTestCase):
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
