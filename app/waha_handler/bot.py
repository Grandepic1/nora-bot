"""
Waha Framework Handler with Discord Based Inspired
"""

import asyncio
import importlib
import inspect
import logging

from aiohttp import web
import aiohttp
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.services.gemini import GeminiService
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
        self.listeners = {}
        self.conversation_locks: dict[
            tuple[str | None, str | None],
            asyncio.Lock,
        ] = {}
        self.conversation_tasks: dict[
            tuple[str | None, str | None],
            asyncio.Task[None],
        ] = {}
        self.debug = debug
        self.extensions = []
        self.http: aiohttp.ClientSession | None = None

        self.gemini = GeminiService()
        self.app = web.Application()
        self.app.cleanup_ctx.append(self._database_context)
        self.app.cleanup_ctx.append(self._http_context)
        self.app.cleanup_ctx.append(self._gemini_context)

        self.app.router.add_post(
            "/webhook/waha",
            self._handle_webhook,
        )

        self.app.on_startup.append(
            self._setup_extensions
        )

    async def _http_context(self, app):
        timeout = aiohttp.ClientTimeout(total=10)

        self.http = aiohttp.ClientSession(
            timeout=timeout
        )

        yield

        await self.http.close()

    async def _database_context(self, app: web.Application):
        try:
            async with self.database_engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            yield
        finally:
            await self.database_engine.dispose()

    async def _gemini_context(self, app):
        yield

        await self.gemini.close()
        self.conversation_locks.clear()
        self.conversation_tasks.clear()

    def _get_conversation_lock(
        self,
        session: str | None,
        chat_id: str | None,
    ) -> asyncio.Lock:
        key = (session, chat_id)
        lock = self.conversation_locks.get(key)

        if lock is None:
            lock = asyncio.Lock()
            self.conversation_locks[key] = lock

        return lock

    def track_conversation_task(
        self,
        session: str | None,
        chat_id: str | None,
        task: asyncio.Task[None],
    ) -> None:
        key = (session, chat_id)
        self.conversation_tasks[key] = task

        def clear_finished(finished: asyncio.Task[None]) -> None:
            if self.conversation_tasks.get(key) is finished:
                self.conversation_tasks.pop(key, None)

        task.add_done_callback(clear_finished)

    async def _wait_for_conversation_task(
        self,
        session: str | None,
        chat_id: str | None,
    ) -> None:
        task = self.conversation_tasks.get((session, chat_id))

        if task is not None:
            await task

    async def _dispatch(self, event: str, ctx: Context):
        listeners = self.listeners.get(event, [])

        for listener in listeners:
            await listener(ctx)

    def command(
        self,
        name=None,
        description: str | None = None,
    ):
        def decorator(func):
            command_name = name or func.__name__

            self.commands[command_name.lower()] = {
                "func": func,
                "description": description or "",
            }

            return func

        return decorator

    def listener(self, event: str):
        def decorator(func):
            self.listeners.setdefault(event, []).append(func)
            return func
        return decorator

    async def _process_command(
        self,
        ctx: Context,
        body: str,
    ):
        parts = body[len(self.prefix):].split()

        if not parts:
            return

        command_name = parts[0].lower()
        args = parts[1:]

        command_data = self.commands.get(command_name)

        if command_data is None:
            return

        command = command_data["func"]
        await command(ctx, *args)

    async def _handle_webhook(self, request: web.Request):
        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"error": "Invalid JSON"},
                status=400,
            )

        if data.get("event") != "message":
            return web.json_response({"ok": True})

        payload = data.get("payload", {})

        if payload.get("fromMe"):
            return web.json_response({"ok": True})

        body = (payload.get("body") or "").strip()

        if not body:
            return web.json_response({"ok": True})

        conversation_lock = self._get_conversation_lock(
            data.get("session"),
            payload.get("from"),
        )

        try:
            async with conversation_lock:
                async with self.session_factory() as db:
                    ctx = Context(self, data, db)

                    try:
                        if body.startswith(self.prefix):
                            await self._wait_for_conversation_task(
                                ctx.session,
                                ctx.chat_id,
                            )
                            await self._process_command(
                                ctx,
                                body,
                            )
                        else:
                            await self._dispatch(
                                "message",
                                ctx,
                            )

                        await db.commit()

                    except Exception:
                        await db.rollback()
                        raise

        except Exception:
            logging.exception("Webhook handling error")

        return web.json_response({"ok": True})

    def load_extension(self, name: str):
        self.extensions.append(name)

    async def _setup_extensions(self, app):
        for name in self.extensions:
            module = importlib.import_module(name)

            setup = getattr(module, "setup", None)

            if setup is None:
                raise RuntimeError(
                    f"Extension '{name}' has no setup(bot)"
                )

            result = setup(self)

            if inspect.isawaitable(result):
                await result

    def run(self, host="127.0.0.1", port=8000):
        if self.debug:
            logging.basicConfig(level=logging.DEBUG)
        web.run_app(
            self.app,
            host=host,
            port=port
        )
