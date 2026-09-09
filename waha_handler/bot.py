"""
Waha Framework Handler with Discord Based Inspired
"""

import inspect
import logging

from aiohttp import web

from waha_handler.context import Context


class WahaBot:
    def __init__(
        self,
        waha_url: str,
        api_key: str,
        prefix: str = "/",
        debug:bool = False
    ):
        self.waha_url = waha_url.rstrip("/")
        self.api_key = api_key
        self.prefix = prefix
        self.commands = {}
        self.debug = debug

        self.app = web.Application()

        self.app.router.add_post(
            "/webhook/waha",
            self._handle_webhook,
        )

    def command(self, name=None):
        def decorator(func):
            command_name = name or func.__name__

            self.commands[command_name.lower()] = func

            return func

        return decorator

    async def _handle_webhook(self, request: web.Request):
        try:
            data = await request.json()
        except Exception:
            data = {}

        if data.get("event") != "message":
            return web.json_response({"ok": True})

        payload = data.get("payload", {})

        if payload.get("fromMe"):
            return web.json_response({"ok": True})

        body = (payload.get("body") or "").strip()

        if not body.startswith(self.prefix):
            return web.json_response({"ok": True})

        parts = body[len(self.prefix) :].split()

        if not parts:
            return web.json_response({"ok": True})

        command_name = parts[0].lower()
        args = parts[1:]

        command = self.commands.get(command_name)

        if command:
            ctx: Context = Context(self, data)

            try:
                result = command(ctx, *args)

                if inspect.isawaitable(result):
                    await result

            except Exception as e:
                print(f"Command error: {e}")

        return web.json_response({"ok": True})

    def run(self, host="127.0.0.1", port=8000):
        if self.debug:
            logging.basicConfig(level=logging.DEBUG)
        web.run_app(
            self.app,
            host=host,
            port=port
        )
