import asyncio
import unittest

from app.services.gemini import GeminiService


class FakeResponse:
    text = "response"


class FakeChat:
    def __init__(self, started=None, release=None):
        self.started = started
        self.release = release

    async def send_message(self, message):
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            await self.release.wait()
        return FakeResponse()


class GeminiInactivityTests(unittest.IsolatedAsyncioTestCase):
    def make_service(self, chat, inactivity_seconds=0.05):
        service = object.__new__(GeminiService)
        service.states = {}
        service.debounce_seconds = 0
        service.inactivity_seconds = inactivity_seconds
        service.closing = False
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


if __name__ == "__main__":
    unittest.main()
