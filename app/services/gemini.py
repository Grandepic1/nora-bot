import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types

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
    def __init__(self, inactivity_seconds: float = 300.0):
        api_key = os.getenv("GEMINI_KEY")

        if not api_key:
            raise RuntimeError("GEMINI_KEY is not configured")

        self.client = genai.Client(api_key=api_key)
        self.model = os.environ["GEMINI_MODEL"]
        self.states: dict[int, ChatState] = {}
        self.debounce_seconds = 2.0
        self.inactivity_seconds = inactivity_seconds
        self.closing = False

    def _create_chat(self, spreadsheet_id: str | None):
        tool_providers = [
            (spreadsheet_id, build_sheet_tools),
        ]
        tools = [
            tool
            for feature, build_tools in tool_providers
            if feature is not None
            for tool in build_tools(feature)
        ]

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
            raise RuntimeError("Gemini service is shutting down")

        state = self.states.get(sheet_session_id)

        if state is None:
            state = ChatState(sheet_session_id, spreadsheet_id)
            self.states[sheet_session_id] = state
        elif state.closed:
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

        if state.worker is None or state.worker.done():
            state.worker = asyncio.create_task(self._run_worker(state))

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
                    await asyncio.sleep(delay)

                if state.closed:
                    break

                batch = list(state.messages)
                state.messages.clear()
                queued = batch[-1]
                combined_message = "\n".join(item.text for item in batch)

                try:
                    try:
                        await queued.start_typing()
                    except Exception:
                        logging.exception("Failed to start typing indicator")

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
                except Exception:
                    logging.exception("Gemini message processing failed")
                    response_text = (
                        "NORA mengalami kesalahan saat memproses pesan."
                    )

                try:
                    if response_text is not None:
                        await queued.respond(response_text)
                except Exception:
                    logging.exception("Failed to send Gemini response")
                finally:
                    try:
                        await queued.stop_typing()
                    except Exception:
                        logging.exception("Failed to stop typing indicator")

                state.last_activity_at = loop.time()
                state.deactivate = queued.deactivate
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

        if (
            task is not None
            and task is not asyncio.current_task()
            and not task.done()
        ):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _start_inactivity_timer(self, state: ChatState) -> None:
        state.inactivity_generation += 1
        generation = state.inactivity_generation
        state.inactivity_task = asyncio.create_task(
            self._wait_for_inactivity(state, generation)
        )

    async def _wait_for_inactivity(
        self,
        state: ChatState,
        generation: int,
    ) -> None:
        current_task = asyncio.current_task()

        try:
            await asyncio.sleep(self.inactivity_seconds)

            if (
                self.closing
                or state.closed
                or self.states.get(state.sheet_session_id) is not state
                or state.inactivity_generation != generation
                or state.worker is not None
                or state.messages
                or state.deactivate is None
            ):
                return

            await state.deactivate()
            state.closed = True

            if self.states.get(state.sheet_session_id) is state:
                self.states.pop(state.sheet_session_id, None)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("Failed to deactivate inactive Gemini session")

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
            return

        state.closed = True
        await self._cancel_inactivity(state)

        if state.worker is not None:
            await state.worker

        if self.states.get(sheet_session_id) is state:
            self.states.pop(sheet_session_id, None)

    async def close(self) -> None:
        self.closing = True

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
                for worker in workers:
                    worker.cancel()

                await asyncio.gather(
                    *workers,
                    return_exceptions=True,
                )

        self.states.clear()

        await self.client.aio.aclose()
