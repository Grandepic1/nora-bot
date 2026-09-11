import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types

from app.debug_logging import DebugLogger
from app.tools.sheets import build_sheet_tools

load_dotenv()

AsyncCallback = Callable[[], Awaitable[None]]
ResponseCallback = Callable[[str], Awaitable[None]]
InactivityCallback = Callable[[], Awaitable[bool]]


@dataclass
class QueuedMessage:
    text: str
    respond: ResponseCallback
    start_typing: AsyncCallback
    stop_typing: AsyncCallback
    deactivate: InactivityCallback


@dataclass
class ChatState:
    sheet_session_id: int
    spreadsheet_id: str | None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    chat: Any | None = None
    chat_spreadsheet_id: str | None = None
    messages: list[QueuedMessage] = field(default_factory=list)
    last_queued_at: float = 0.0
    last_activity_at: float = 0.0
    worker: asyncio.Task[None] | None = None
    inactivity_task: asyncio.Task[None] | None = None
    inactivity_generation: int = 0
    deactivate: InactivityCallback | None = None
    closed: bool = False


class GeminiService:
    def __init__(
        self,
        inactivity_seconds: float = 300.0,
        debug_log: DebugLogger | None = None,
    ):
        api_key = os.getenv("GEMINI_KEY")

        if not api_key:
            raise RuntimeError("GEMINI_KEY is not configured")

        self.client = genai.Client(api_key=api_key)
        self.model = os.environ["GEMINI_MODEL"]
        self.states: dict[int, ChatState] = {}
        self.debounce_seconds = 2.0
        self.inactivity_seconds = inactivity_seconds
        self.debug_log = debug_log or DebugLogger(False)
        self.closing = False

    def _create_chat(self, spreadsheet_id: str | None):
        tool_providers = [
            (
                spreadsheet_id,
                lambda feature: build_sheet_tools(feature, self.debug_log),
            ),
        ]
        tools = [
            tool
            for feature, build_tools in tool_providers
            if feature is not None
            for tool in build_tools(feature)
        ]
        self.debug_log.event(
            "gemini.chat.created",
            has_spreadsheet=spreadsheet_id is not None,
            tool_count=len(tools),
        )

        return self.client.aio.chats.create(
            model=self.model,
            config=types.GenerateContentConfig(
                system_instruction=(
                    "You are NORA, a helpful AI assistant. "
                    "Use the tools available to you when they are relevant. "
                    "A spreadsheet is optional. If the user asks you to read "
                    "or modify a spreadsheet and no spreadsheet tools are "
                    "available, ask them to set one first with "
                    "`/spreadsheet <Google Sheets URL>`. Never claim access "
                    "to unavailable tools or invent spreadsheet data."
                ),
                tools=tools,
            ),
        )

    async def queue_message(
        self,
        sheet_session_id: int,
        spreadsheet_id: str | None,
        message: str,
        callback: ResponseCallback,
        start_typing: AsyncCallback,
        stop_typing: AsyncCallback,
        deactivate: InactivityCallback,
    ) -> asyncio.Task[None]:
        if self.closing:
            self.debug_log.event(
                "gemini.queue.rejected",
                sheet_session_id=sheet_session_id,
                reason="service_closing",
            )
            raise RuntimeError("Gemini service is shutting down")

        state = self.states.get(sheet_session_id)

        if state is None:
            state = ChatState(sheet_session_id, spreadsheet_id)
            self.states[sheet_session_id] = state
            self.debug_log.event(
                "gemini.state.created",
                sheet_session_id=sheet_session_id,
            )
        elif state.closed:
            self.debug_log.event(
                "gemini.queue.rejected",
                sheet_session_id=sheet_session_id,
                reason="state_closed",
            )
            raise RuntimeError("Gemini chat is closing")

        await self._cancel_inactivity(state)
        state.spreadsheet_id = spreadsheet_id
        state.deactivate = deactivate
        state.messages.append(
            QueuedMessage(
                text=message,
                respond=callback,
                start_typing=start_typing,
                stop_typing=stop_typing,
                deactivate=deactivate,
            )
        )
        state.last_queued_at = asyncio.get_running_loop().time()
        self.debug_log.event(
            "gemini.message.queued",
            sheet_session_id=sheet_session_id,
            queue_depth=len(state.messages),
            has_spreadsheet=spreadsheet_id is not None,
        )

        if state.worker is None or state.worker.done():
            state.worker = asyncio.create_task(self._run_worker(state))
            self.debug_log.event(
                "gemini.worker.started",
                sheet_session_id=sheet_session_id,
            )

        return state.worker

    async def _run_worker(self, state: ChatState) -> None:
        current_task = asyncio.current_task()

        try:
            while state.messages and not state.closed:
                loop = asyncio.get_running_loop()

                while True:
                    delay = (
                        state.last_queued_at
                        + self.debounce_seconds
                        - loop.time()
                    )
                    if delay <= 0:
                        break
                    self.debug_log.event(
                        "gemini.debounce.waiting",
                        sheet_session_id=state.sheet_session_id,
                        delay_ms=round(delay * 1000),
                        queue_depth=len(state.messages),
                    )
                    await asyncio.sleep(delay)

                if state.closed:
                    break

                batch = list(state.messages)
                state.messages.clear()
                queued = batch[-1]
                combined_message = "\n".join(item.text for item in batch)
                processing_started_at = loop.time()
                self.debug_log.event(
                    "gemini.processing.started",
                    sheet_session_id=state.sheet_session_id,
                    batch_size=len(batch),
                )

                try:
                    try:
                        await queued.start_typing()
                    except Exception as error:
                        self.debug_log.failure(
                            "gemini.typing_start.failed",
                            error,
                            sheet_session_id=state.sheet_session_id,
                        )

                    async with state.lock:
                        if state.closed:
                            response_text = None
                        else:
                            if (
                                state.chat is None
                                or state.chat_spreadsheet_id
                                != state.spreadsheet_id
                            ):
                                state.chat = self._create_chat(
                                    state.spreadsheet_id
                                )
                                state.chat_spreadsheet_id = (
                                    state.spreadsheet_id
                                )

                            response = await state.chat.send_message(
                                combined_message
                            )

                            response_text = response.text or (
                                "NORA tidak dapat menghasilkan respons."
                            )
                except Exception as error:
                    self.debug_log.failure(
                        "gemini.processing.failed",
                        error,
                        sheet_session_id=state.sheet_session_id,
                        batch_size=len(batch),
                        duration_ms=round(
                            (loop.time() - processing_started_at) * 1000
                        ),
                    )
                    response_text = (
                        "NORA mengalami kesalahan saat memproses pesan."
                    )

                try:
                    if response_text is not None:
                        await queued.respond(response_text)
                except Exception as error:
                    self.debug_log.failure(
                        "gemini.response_delivery.failed",
                        error,
                        sheet_session_id=state.sheet_session_id,
                    )
                finally:
                    try:
                        await queued.stop_typing()
                    except Exception as error:
                        self.debug_log.failure(
                            "gemini.typing_stop.failed",
                            error,
                            sheet_session_id=state.sheet_session_id,
                        )

                state.last_activity_at = loop.time()
                state.deactivate = queued.deactivate
                self.debug_log.event(
                    "gemini.processing.completed",
                    sheet_session_id=state.sheet_session_id,
                    batch_size=len(batch),
                    duration_ms=round(
                        (loop.time() - processing_started_at) * 1000
                    ),
                    remaining_queue_depth=len(state.messages),
                )
        finally:
            if state.worker is current_task:
                state.worker = None

            if (
                not self.closing
                and not state.closed
                and not state.messages
                and state.last_activity_at > 0
            ):
                self._start_inactivity_timer(state)

    async def _cancel_inactivity(self, state: ChatState) -> None:
        state.inactivity_generation += 1
        task = state.inactivity_task
        state.inactivity_task = None
        had_active_timer = task is not None and not task.done()

        if (
            task is not None
            and task is not asyncio.current_task()
            and not task.done()
        ):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        if had_active_timer:
            self.debug_log.event(
                "gemini.inactivity.cancelled",
                sheet_session_id=state.sheet_session_id,
                generation=state.inactivity_generation,
            )

    def _start_inactivity_timer(self, state: ChatState) -> None:
        state.inactivity_generation += 1
        generation = state.inactivity_generation
        state.inactivity_task = asyncio.create_task(
            self._wait_for_inactivity(state, generation)
        )
        self.debug_log.event(
            "gemini.inactivity.scheduled",
            sheet_session_id=state.sheet_session_id,
            generation=generation,
            timeout_seconds=self.inactivity_seconds,
        )

    async def _wait_for_inactivity(
        self,
        state: ChatState,
        generation: int,
    ) -> None:
        current_task = asyncio.current_task()

        try:
            await asyncio.sleep(self.inactivity_seconds)
            self.debug_log.event(
                "gemini.inactivity.timer_fired",
                sheet_session_id=state.sheet_session_id,
                generation=generation,
            )

            if (
                self.closing
                or state.closed
                or self.states.get(state.sheet_session_id) is not state
                or state.inactivity_generation != generation
                or state.worker is not None
                or state.messages
                or state.deactivate is None
            ):
                self.debug_log.event(
                    "gemini.inactivity.skipped",
                    sheet_session_id=state.sheet_session_id,
                    generation=generation,
                )
                return

            deactivated = await state.deactivate()
            state.closed = True
            self.debug_log.event(
                "gemini.inactivity.completed",
                sheet_session_id=state.sheet_session_id,
                generation=generation,
                database_deactivated=deactivated,
            )

            if self.states.get(state.sheet_session_id) is state:
                self.states.pop(state.sheet_session_id, None)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.debug_log.failure(
                "gemini.inactivity.failed",
                error,
                sheet_session_id=state.sheet_session_id,
                generation=generation,
            )

            if (
                not self.closing
                and not state.closed
                and self.states.get(state.sheet_session_id) is state
            ):
                self._start_inactivity_timer(state)
        finally:
            if state.inactivity_task is current_task:
                state.inactivity_task = None

    async def remove_chat(self, sheet_session_id: int) -> None:
        state = self.states.get(sheet_session_id)

        if state is None:
            self.debug_log.event(
                "gemini.state.remove_skipped",
                sheet_session_id=sheet_session_id,
                reason="not_found",
            )
            return

        self.debug_log.event(
            "gemini.state.removing",
            sheet_session_id=sheet_session_id,
        )
        state.closed = True
        await self._cancel_inactivity(state)

        if state.worker is not None:
            await state.worker

        if self.states.get(sheet_session_id) is state:
            self.states.pop(sheet_session_id, None)
        self.debug_log.event(
            "gemini.state.removed",
            sheet_session_id=sheet_session_id,
        )

    async def close(self) -> None:
        self.closing = True
        self.debug_log.event(
            "gemini.shutdown.started",
            state_count=len(self.states),
        )

        inactivity_tasks = [
            state.inactivity_task
            for state in self.states.values()
            if state.inactivity_task is not None
            and not state.inactivity_task.done()
        ]

        for task in inactivity_tasks:
            task.cancel()

        if inactivity_tasks:
            await asyncio.gather(
                *inactivity_tasks,
                return_exceptions=True,
            )

        workers = [
            state.worker
            for state in self.states.values()
            if state.worker is not None and not state.worker.done()
        ]

        if workers:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*workers, return_exceptions=True),
                    timeout=30,
                )
            except asyncio.TimeoutError:
                self.debug_log.event(
                    "gemini.shutdown.worker_timeout",
                    worker_count=len(workers),
                )
                for worker in workers:
                    worker.cancel()

                await asyncio.gather(
                    *workers,
                    return_exceptions=True,
                )

        self.states.clear()

        await self.client.aio.aclose()
        self.debug_log.event("gemini.shutdown.completed")
