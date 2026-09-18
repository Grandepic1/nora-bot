import asyncio
import unittest

from app.debug_logging import DebugLogger
from app.models.incoming_media import MediaAttachment
from app.services.gemini import GeminiService, SYSTEM_INSTRUCTION


class FakeResponse:
    text = "response"


class FakeChat:
    def __init__(self, started=None, release=None):
        self.started = started
        self.release = release
        self.messages = []

    async def send_message(self, message):
        self.messages.append(message)
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            await self.release.wait()
        return FakeResponse()


class GeminiInactivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_and_caption_reach_gemini_together(self):
        chat = FakeChat()
        service = self.make_service(chat)

        async def no_op(*args):
            return None

        worker = await service.queue_message(
            sheet_session_id=1,
            spreadsheet_id=None,
            message="Add this list to my spreadsheet",
            image=MediaAttachment(b"image-bytes", "image/jpeg", None),
            callback=no_op,
            start_typing=no_op,
            stop_typing=no_op,
            deactivate=no_op,
        )
        await worker

        parts = chat.messages[0]
        self.assertEqual(parts[0].text, "Add this list to my spreadsheet")
        self.assertEqual(parts[1].inline_data.data, b"image-bytes")
        self.assertEqual(parts[1].inline_data.mime_type, "image/jpeg")
        await service.remove_chat(1)

    async def test_image_without_caption_asks_for_instruction(self):
        chat = FakeChat()
        service = self.make_service(chat)

        async def no_op(*args):
            return None

        worker = await service.queue_message(
            sheet_session_id=1,
            spreadsheet_id=None,
            message="",
            image=MediaAttachment(b"image-bytes", "image/png", None),
            callback=no_op,
            start_typing=no_op,
            stop_typing=no_op,
            deactivate=no_op,
        )
        await worker

        parts = chat.messages[0]
        self.assertIn("ask me what to do", parts[0].text)
        self.assertEqual(parts[1].inline_data.data, b"image-bytes")
        await service.remove_chat(1)

    def make_service(self, chat, inactivity_seconds=0.05):
        service = object.__new__(GeminiService)
        service.states = {}
        service.debounce_seconds = 0
        service.inactivity_seconds = inactivity_seconds
        service.closing = False
        service.debug_log = DebugLogger(False)
        service._create_chat = lambda spreadsheet_id: chat
        return service

    async def queue(self, service, deactivate):
        async def no_op():
            return None

        async def respond(text):
            return None

        return await service.queue_message(
            sheet_session_id=1,
            spreadsheet_id=None,
            message="hello",
            callback=respond,
            start_typing=no_op,
            stop_typing=no_op,
            deactivate=deactivate,
        )

    async def test_inactivity_starts_after_response_finishes(self):
        started = asyncio.Event()
        release = asyncio.Event()
        deactivated = asyncio.Event()
        service = self.make_service(FakeChat(started, release))

        async def deactivate():
            deactivated.set()
            return True

        worker = await self.queue(service, deactivate)
        await started.wait()
        await asyncio.sleep(service.inactivity_seconds * 1.5)
        self.assertFalse(deactivated.is_set())

        release.set()
        await worker
        await asyncio.wait_for(deactivated.wait(), timeout=0.2)
        self.assertNotIn(1, service.states)

    async def test_new_message_replaces_previous_inactivity_timer(self):
        deactivated = asyncio.Event()
        service = self.make_service(FakeChat(), inactivity_seconds=0.1)

        async def deactivate():
            deactivated.set()
            return True

        first_worker = await self.queue(service, deactivate)
        await first_worker
        state = service.states[1]
        first_timer = state.inactivity_task

        second_worker = await self.queue(service, deactivate)
        await second_worker

        self.assertTrue(first_timer.cancelled())
        self.assertIsNot(state.inactivity_task, first_timer)
        await asyncio.wait_for(deactivated.wait(), timeout=0.3)

    async def test_remove_chat_cancels_inactivity_timer(self):
        deactivation_count = 0
        service = self.make_service(FakeChat())

        async def deactivate():
            nonlocal deactivation_count
            deactivation_count += 1
            return True

        worker = await self.queue(service, deactivate)
        await worker
        await service.remove_chat(1)
        await asyncio.sleep(service.inactivity_seconds * 1.5)

        self.assertEqual(deactivation_count, 0)
        self.assertNotIn(1, service.states)

    def test_system_instruction_targets_whatsapp_output(self):
        self.assertIn("WhatsApp assistant", SYSTEM_INSTRUCTION)
        self.assertIn("full URLs on their own line", SYSTEM_INSTRUCTION)
        self.assertIn("confirmation codes and action IDs private", SYSTEM_INSTRUCTION)


if __name__ == "__main__":
    unittest.main()
