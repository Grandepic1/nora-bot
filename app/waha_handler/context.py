import requests
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
        response = requests.post(
            f"{self.bot.waha_url}/api/sendText",
            headers={
                "X-Api-Key": self.bot.api_key,
                "Content-Type": "application/json",
            },
            json={"session": self.session, "chatId": self.chat_id, "text": message},
            timeout=10,
        )

        response.raise_for_status()
        return response.json()
