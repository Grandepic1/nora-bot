"""
Waha Framework Handler with Discord Based Inspired
"""

import asyncio
import importlib
import inspect
from collections.abc import Awaitable, Callable

from aiohttp import web
import aiohttp
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.debug_logging import DebugLogger
from app.models.active_sheet_session import ActiveSheetSession
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
        self.debug_log = DebugLogger(debug)
        self.extensions = []
        self.http: aiohttp.ClientSession | None = None

        self.gemini = GeminiService(debug_log=self.debug_log)
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

        self.debug_log.event("http.client.opening")
        self.http = aiohttp.ClientSession(
            timeout=timeout
        )
        self.debug_log.event("http.client.opened")

        yield

        await self.http.close()
        self.debug_log.event("http.client.closed")

    async def _database_context(self, app: web.Application):
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        self.debug_log.event("database.healthcheck.started")

        try:
            try:
                async with self.database_engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
            except Exception as error:
                self.debug_log.failure(
                    "database.healthcheck.failed",
                    error,
                    duration_ms=round((loop.time() - started_at) * 1000),
                )
                raise

            self.debug_log.event(
                "database.healthcheck.completed",
                duration_ms=round((loop.time() - started_at) * 1000),
            )
            yield
        finally:
            await self.database_engine.dispose()
            self.debug_log.event("database.engine.disposed")

    async def _gemini_context(self, app):
        self.debug_log.event("gemini.lifecycle.started")
        yield

        self.debug_log.event("gemini.lifecycle.closing")
        await self.gemini.close()
        self.conversation_locks.clear()
        self.conversation_tasks.clear()
        self.debug_log.event("gemini.lifecycle.closed")

    async def deactivate_inactive_session(
        self,
        user_id: str,
        sheet_session_id: int,
        session_name: str,
        session: str | None,
        chat_id: str | None,
        notify: Callable[[str], Awaitable[None]],
    ) -> bool:
        conversation_lock = self._get_conversation_lock(session, chat_id)
        self.debug_log.event(
            "session.inactivity.deactivation_started",
            sheet_session_id=sheet_session_id,
        )

        async with conversation_lock:
            async with self.session_factory() as db:
                result = await db.execute(
                    delete(ActiveSheetSession)
                    .where(
                        ActiveSheetSession.user_id == user_id,
                        ActiveSheetSession.sheet_session_id == sheet_session_id,
                    )
                    .returning(ActiveSheetSession.user_id)
                )
                deactivated = result.scalar_one_or_none() is not None
                await db.commit()

            if not deactivated:
                self.debug_log.event(
                    "session.inactivity.deactivation_skipped",
                    sheet_session_id=sheet_session_id,
                    reason="active_session_changed",
                )
                return False

            try:
                await notify(
                    "Session dinonaktifkan karena tidak ada aktivitas "
                    "selama 5 menit.\n"
                    f"Gunakan `/start {session_name}` untuk memulai lagi.",
                )
            except Exception as error:
                self.debug_log.failure(
                    "session.inactivity.notification_failed",
                    error,
                    sheet_session_id=sheet_session_id,
                )

            self.debug_log.event(
                "session.inactivity.deactivated",
                sheet_session_id=sheet_session_id,
            )

            return True

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
            self.debug_log.event("conversation.lock.created")

        return lock

    def track_conversation_task(
        self,
        session: str | None,
        chat_id: str | None,
        task: asyncio.Task[None],
    ) -> None:
        key = (session, chat_id)
        self.conversation_tasks[key] = task
        self.debug_log.event("conversation.task.tracked")

        def clear_finished(finished: asyncio.Task[None]) -> None:
            if finished.cancelled():
                status = "cancelled"
            elif finished.exception() is not None:
                status = "failed"
            else:
                status = "completed"

            self.debug_log.event(
                "conversation.task.finished",
                status=status,
            )

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
            self.debug_log.event("conversation.task.waiting")
            await task
            self.debug_log.event("conversation.task.wait_completed")

    async def _dispatch(self, event: str, ctx: Context):
        listeners = self.listeners.get(event, [])

        for index, listener in enumerate(listeners):
            loop = asyncio.get_running_loop()
            started_at = loop.time()
            self.debug_log.event(
                "listener.dispatch.started",
                event_name=event,
                listener_index=index,
                listener_count=len(listeners),
            )
            try:
                await listener(ctx)
            except Exception as error:
                self.debug_log.failure(
                    "listener.dispatch.failed",
                    error,
                    event_name=event,
                    listener_index=index,
                    duration_ms=round((loop.time() - started_at) * 1000),
                )
                raise
            self.debug_log.event(
                "listener.dispatch.completed",
                event_name=event,
                listener_index=index,
                duration_ms=round((loop.time() - started_at) * 1000),
            )

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
            self.debug_log.event("command.unrecognized")
            return

        command = command_data["func"]
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        self.debug_log.event(
            "command.dispatch.started",
            command=command_name,
        )
        try:
            await command(ctx, *args)
        except Exception as error:
            self.debug_log.failure(
                "command.dispatch.failed",
                error,
                command=command_name,
                duration_ms=round((loop.time() - started_at) * 1000),
            )
            raise
        self.debug_log.event(
            "command.dispatch.completed",
            command=command_name,
            duration_ms=round((loop.time() - started_at) * 1000),
        )

    async def _handle_webhook(self, request: web.Request):
        try:
            data = await request.json()
        except Exception as error:
            self.debug_log.failure("webhook.invalid_json", error)
            return web.json_response(
                {"error": "Invalid JSON"},
                status=400,
            )

        if data.get("event") != "message":
            self.debug_log.event("webhook.ignored", reason="non_message_event")
            return web.json_response({"ok": True})

        payload = data.get("payload", {})

        if payload.get("fromMe"):
            self.debug_log.event("webhook.ignored", reason="message_from_bot")
            return web.json_response({"ok": True})

        body = (payload.get("body") or "").strip()

        if not body:
            self.debug_log.event("webhook.ignored", reason="empty_body")
            return web.json_response({"ok": True})

        message_id = payload.get("id")
        route = "command" if body.startswith(self.prefix) else "listener"
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        self.debug_log.event(
            "webhook.processing.started",
            message_id=message_id,
            route=route,
        )

        conversation_lock = self._get_conversation_lock(
            data.get("session"),
            payload.get("from"),
        )

        try:
            lock_started_at = loop.time()
            async with conversation_lock:
                self.debug_log.event(
                    "conversation.lock.acquired",
                    message_id=message_id,
                    wait_ms=round((loop.time() - lock_started_at) * 1000),
                )
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
                        self.debug_log.event(
                            "database.transaction.committed",
                            message_id=message_id,
                        )

                    except Exception as error:
                        await db.rollback()
                        self.debug_log.failure(
                            "database.transaction.rolled_back",
                            error,
                            message_id=message_id,
                        )
                        raise

        except Exception as error:
            self.debug_log.failure(
                "webhook.processing.failed",
                error,
                message_id=message_id,
                duration_ms=round((loop.time() - started_at) * 1000),
            )
        else:
            self.debug_log.event(
                "webhook.processing.completed",
                message_id=message_id,
                duration_ms=round((loop.time() - started_at) * 1000),
            )

        return web.json_response({"ok": True})

    def load_extension(self, name: str):
        self.extensions.append(name)

    async def _setup_extensions(self, app):
        for name in self.extensions:
            loop = asyncio.get_running_loop()
            started_at = loop.time()
            self.debug_log.event("extension.loading", extension=name)

            try:
                module = importlib.import_module(name)

                setup = getattr(module, "setup", None)

                if setup is None:
                    raise RuntimeError(
                        f"Extension '{name}' has no setup(bot)"
                    )

                result = setup(self)

                if inspect.isawaitable(result):
                    await result
            except Exception as error:
                self.debug_log.failure(
                    "extension.failed",
                    error,
                    extension=name,
                )
                raise

            self.debug_log.event(
                "extension.loaded",
                extension=name,
                duration_ms=round((loop.time() - started_at) * 1000),
            )

    def run(self, host="127.0.0.1", port=8000):
        self.debug_log.event("bot.run", host=host, port=port)
        web.run_app(
            self.app,
            host=host,
            port=port,
            access_log=None,
            print=None,
        )
