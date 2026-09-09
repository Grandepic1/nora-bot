"""
Waha Framework Handler with Discord Based Inspired
"""

import inspect
import logging

from aiohttp import web
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.waha_handler.context import Context


class WahaBot:
    def __init__(
        self,
        waha_url: str,
        api_key: str,
        database_engine: AsyncEngine,
        session_factory: async_sessionmaker[AsyncSession],
        prefix: str = "/",
        debug: bool = False,
    ):
        self.waha_url = waha_url.rstrip("/")
        self.api_key = api_key
        self.database_engine = database_engine
        self.session_factory = session_factory
        self.prefix = prefix
        self.commands = {}
        self.debug = debug

        self.app = web.Application()
        self.app.cleanup_ctx.append(self._database_context)

        self.app.router.add_post(
            "/webhook/waha",
            self._handle_webhook,
        )

    async def _database_context(self, app: web.Application):
        try:
            async with self.database_engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            yield
        finally:
            await self.database_engine.dispose()

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
            try:
                async with self.session_factory() as db:
                    ctx = Context(self, data, db)

                    try:
                        result = command(ctx, *args)

                        if inspect.isawaitable(result):
                            await result

                        await db.commit()
                    except Exception:
                        await db.rollback()
                        raise
            except Exception:
                logging.exception("Command error")

        return web.json_response({"ok": True})

    def run(self, host="127.0.0.1", port=8000):
        if self.debug:
            logging.basicConfig(level=logging.DEBUG)
        web.run_app(
            self.app,
            host=host,
            port=port
        )
