import asyncio

from sqlalchemy.ext.asyncio import AsyncSession


class Context:
    def __init__(
        self,
        bot,
        event: dict,
        db: AsyncSession | None,
    ):
        self.bot = bot
        self.event = event
        self._db = db

        payload: dict = event.get("payload", {})

        self.payload = payload
        self.message = payload.get("body", "")
        self.chat_id = payload.get("from")
        self.sender = payload.get("from")
        self.session = event.get("session")
        self.message_id = payload.get("id")

    @property
    def db(self) -> AsyncSession:
        if self._db is None:
            raise RuntimeError("Database is not available on a delivery context")

        return self._db

    def for_delivery(self) -> "Context":
        return type(self)(
            self.bot,
            {
                "session": self.session,
                "payload": {
                    "from": self.chat_id,
                    "id": self.message_id,
                },
            },
            None,
        )

    async def _post(self, endpoint: str, operation: str, payload: dict):
        if self.bot.http is None:
            raise RuntimeError("HTTP client is not available")

        loop = asyncio.get_running_loop()
        started_at = loop.time()
        status = None
        self.bot.debug_log.event(
            "waha.request.started",
            operation=operation,
            message_id=self.message_id,
        )

        try:
            async with self.bot.http.post(
                f"{self.bot.waha_url}{endpoint}",
                headers={
                    "X-Api-Key": self.bot.api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
            ) as response:
                status = response.status
                response.raise_for_status()
                result = (
                    await response.json()
                    if operation == "send_text"
                    else None
                )
        except Exception as error:
            self.bot.debug_log.failure(
                "waha.request.failed",
                error,
                operation=operation,
                message_id=self.message_id,
                status=status,
                duration_ms=round((loop.time() - started_at) * 1000),
            )
            raise

        self.bot.debug_log.event(
            "waha.request.completed",
            operation=operation,
            message_id=self.message_id,
            status=status,
            duration_ms=round((loop.time() - started_at) * 1000),
        )
        return result

    async def send(self, message: str):
        return await self._post(
            "/api/sendText",
            "send_text",
            {
                "session": self.session,
                "chatId": self.chat_id,
                "text": message,
            },
        )

    async def start_typing(self):
        await self._post(
            "/api/startTyping",
            "start_typing",
            {
                "session": self.session,
                "chatId": self.chat_id,
            },
        )

    async def stop_typing(self):
        await self._post(
            "/api/stopTyping",
            "stop_typing",
            {
                "session": self.session,
                "chatId": self.chat_id,
            },
        )
