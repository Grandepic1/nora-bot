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


@dataclass
class QueuedMessage:
    text: str
    respond: ResponseCallback
    start_typing: AsyncCallback
    stop_typing: AsyncCallback


@dataclass
class ChatState:
    sheet_session_id: int
    spreadsheet_id: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    chat: Any | None = None
    chat_spreadsheet_id: str | None = None
    messages: list[QueuedMessage] = field(default_factory=list)
    last_queued_at: float = 0.0
    worker: asyncio.Task[None] | None = None
    closed: bool = False


class GeminiService:
    def __init__(self):
        api_key = os.getenv("GEMINI_KEY")

        if not api_key:
            raise RuntimeError("GEMINI_KEY is not configured")

        self.client = genai.Client(api_key=api_key)
        self.model = os.environ["GEMINI_MODEL"]
        self.states: dict[int, ChatState] = {}
        self.debounce_seconds = 2.0
        self.closing = False

    def _create_chat(self, spreadsheet_id: str):
        return self.client.aio.chats.create(
            model=self.model,
            config=types.GenerateContentConfig(
                system_instruction=(
                    "You are operating on the user's active spreadsheet. "
                    "Use only the supplied spreadsheet tools. "
                    "Never ask for or invent a spreadsheet ID."
                ),
                tools=build_sheet_tools(spreadsheet_id),
            ),
        )

    async def queue_message(
        self,
        sheet_session_id: int,
        spreadsheet_id: str,
        message: str,
        callback: ResponseCallback,
        start_typing: AsyncCallback,
        stop_typing: AsyncCallback,
    ) -> asyncio.Task[None]:
        if self.closing:
            raise RuntimeError("Gemini service is shutting down")

        state = self.states.get(sheet_session_id)

        if state is None:
            state = ChatState(sheet_session_id, spreadsheet_id)
            self.states[sheet_session_id] = state
        elif state.closed:
            raise RuntimeError("Gemini chat is closing")

        state.spreadsheet_id = spreadsheet_id
        state.messages.append(
            QueuedMessage(
                text=message,
                respond=callback,
                start_typing=start_typing,
                stop_typing=stop_typing,
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

                batch = state.messages
                state.messages = []
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
        finally:
            if state.worker is current_task:
                state.worker = None

    async def remove_chat(self, sheet_session_id: int) -> None:
        state = self.states.get(sheet_session_id)

        if state is None:
            return

        if state.worker is not None:
            await state.worker

        state.closed = True

        if self.states.get(sheet_session_id) is state:
            self.states.pop(sheet_session_id, None)

    async def close(self) -> None:
        self.closing = True
        workers = [
            state.worker
            for state in self.states.values()
            if state.worker is not None
        ]

        if workers:
            await asyncio.gather(*workers, return_exceptions=True)

        self.states.clear()
        await self.client.aio.aclose()
