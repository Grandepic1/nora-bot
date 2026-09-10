import aiohttp
from sqlalchemy.ext.asyncio import AsyncSession


class Context:
    def __init__(self, bot, event: dict, db: AsyncSession):
        self.bot = bot
        self.event = event
        self.db = db

        payload: dict = event.get("payload", {})

        self.payload = payload
        self.message = payload.get("body", "")
        self.chat_id = payload.get("from")
        self.sender = payload.get("from")
        self.session = event.get("session")
        self.message_id = payload.get("id")

    async def send(self, message: str):
        async with self.bot.http.post(
            f"{self.bot.waha_url}/api/sendText",
            headers={
                "X-Api-Key": self.bot.api_key,
                "Content-Type": "application/json",
            },
            json={
                "session": self.session,
                "chatId": self.chat_id,
                "text": message,
            }
        ) as response:
            response.raise_for_status()
            return await response.json()

    async def start_typing(self):
        async with self.bot.http.post(
            f"{self.bot.waha_url}/api/startTyping",
            headers={
                "X-Api-Key": self.bot.api_key,
                "Content-Type": "application/json",
            },
            json={
                "session": self.session,
                "chatId": self.chat_id,
            },
        ) as response:
            response.raise_for_status()


    async def stop_typing(self):
        async with self.bot.http.post(
            f"{self.bot.waha_url}/api/stopTyping",
            headers={
                "X-Api-Key": self.bot.api_key,
                "Content-Type": "application/json",
            },
            json={
                "session": self.session,
                "chatId": self.chat_id,
            },
        ) as response:
            response.raise_for_status()
