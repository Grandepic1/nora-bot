import asyncio
import base64
import binascii
from urllib.parse import unquote, urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.incoming_media import MediaAttachment


ALLOWED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}


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
        self.attachments: MediaAttachment | None = None
        self.chat_id = payload.get("from")
        self.sender = payload.get("from")
        self.session = event.get("session")
        self.message_id = payload.get("id")

    async def load_image(self) -> MediaAttachment | None:
        """Load one WAHA image, if present, without trusting a webhook URL host."""
        if not self.payload.get("hasMedia"):
            return None

        media = self.payload.get("media") or {}
        raw_data = self.payload.get("_data") or {}
        mime_type = media.get("mimetype") or raw_data.get("mimetype")
        if mime_type not in ALLOWED_IMAGE_MIME_TYPES:
            if isinstance(mime_type, str) and mime_type.startswith("image/"):
                raise ValueError("Unsupported image format")
            return None

        media_url = media.get("url")
        if media_url:
            parsed = urlsplit(media_url)
            path = unquote(parsed.path)
            if (
                not path.startswith("/api/files/")
                or ".." in path.split("/")
                or parsed.fragment
            ):
                raise ValueError("Unsupported WAHA media URL")
            if self.bot.http is None:
                raise RuntimeError("HTTP client is not available")

            # WAHA may advertise a public hostname that is unreachable here.
            # Fetch only its media path from the configured WAHA server.
            url = f"{self.bot.waha_url}{parsed.path}"
            if parsed.query:
                url += f"?{parsed.query}"
            async with self.bot.http.get(
                url,
                headers={"X-Api-Key": self.bot.api_key},
                allow_redirects=False,
            ) as response:
                response.raise_for_status()
                image_bytes = await response.read()
        else:
            encoded_body = raw_data.get("body")
            if raw_data.get("type") != "image" or not encoded_body:
                raise ValueError("WAHA did not provide downloadable image data")
            try:
                image_bytes = base64.b64decode(encoded_body, validate=True)
            except (binascii.Error, ValueError) as error:
                raise ValueError("Invalid image data") from error

        if not image_bytes:
            raise ValueError("Image is empty")

        self.attachments = MediaAttachment(
            data=image_bytes,
            mime_type=mime_type,
            filename=media.get("filename"),
        )
        return self.attachments

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
