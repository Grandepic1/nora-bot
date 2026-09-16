import hashlib
import hmac
import unittest

from app.debug_logging import DebugLogger
from app.waha_handler.bot import WahaBot, verify_webhook_hmac


class FakeRequest:
    def __init__(self, body, headers=None):
        self.body = body
        self.headers = headers or {}

    async def read(self):
        return self.body


class WebhookHmacTests(unittest.TestCase):
    def setUp(self):
        self.body = (
            b'{"event":"message","session":"default","engine":"WEBJS"}'
        )
        self.key = "my-secret-key"
        self.signature = hmac.new(
            self.key.encode(),
            self.body,
            hashlib.sha512,
        ).hexdigest()

    def test_accepts_valid_waha_signature(self):
        self.assertTrue(
            verify_webhook_hmac(
                self.body,
                self.key,
                "sha512",
                self.signature,
            )
        )

    def test_matches_waha_documentation_example(self):
        documented_signature = (
            "208f8a55dde9e05519e898b10b89bf0d0b3b0fdf11fdbf09b6b90476301b98d8"
            "097c462b2b17a6ce93b6b47a136cf2e78a33a63f6752c2c1631777076153fa89"
        )
        self.assertTrue(
            verify_webhook_hmac(
                self.body,
                self.key,
                "sha512",
                documented_signature,
            )
        )

    def test_rejects_tampered_body(self):
        self.assertFalse(
            verify_webhook_hmac(
                self.body + b" ",
                self.key,
                "sha512",
                self.signature,
            )
        )

    def test_rejects_missing_headers(self):
        self.assertFalse(
            verify_webhook_hmac(self.body, self.key, None, None)
        )

    def test_rejects_unsupported_algorithm(self):
        self.assertFalse(
            verify_webhook_hmac(
                self.body,
                self.key,
                "sha256",
                self.signature,
            )
        )


class WebhookHmacHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = object.__new__(WahaBot)
        self.bot.hmac_key = "my-secret-key"
        self.bot.debug_log = DebugLogger(False)
        self.body = b'{"event":"session.status"}'

    async def test_handler_rejects_unsigned_request(self):
        response = await self.bot._handle_webhook(FakeRequest(self.body))

        self.assertEqual(response.status, 401)

    async def test_handler_accepts_valid_signature(self):
        signature = hmac.new(
            self.bot.hmac_key.encode(),
            self.body,
            hashlib.sha512,
        ).hexdigest()
        request = FakeRequest(
            self.body,
            {
                "X-Webhook-Hmac-Algorithm": "sha512",
                "X-Webhook-Hmac": signature,
            },
        )

        response = await self.bot._handle_webhook(request)

        self.assertEqual(response.status, 200)


if __name__ == "__main__":
    unittest.main()
