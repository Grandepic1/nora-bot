import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from google.genai import types
from google.genai.chats import AsyncChats

from app.services.gemini import GeminiService
from app.services.pending_sheet_actions import ActionOrigin


def model_response(part):
    return types.GenerateContentResponse(candidates=[
        types.Candidate(
            content=types.Content(role="model", parts=[part]),
            finish_reason="STOP",
        ),
    ])


class GeminiToolFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_queued_messages_prepare_preview_and_confirm_through_sdk(self):
        generate = AsyncMock(side_effect=[
            model_response(types.Part.from_function_call(
                name="create_sheet", args={"title": "Archive"},
            )),
            model_response(types.Part.from_function_call(
                name="resolve_sheet_action",
                args={"choice": "preview", "confirmation_code": "abc123"},
            )),
            model_response(types.Part.from_text(text="Here is your preview.")),
            model_response(types.Part.from_function_call(
                name="resolve_sheet_action",
                args={"choice": "confirm", "confirmation_code": "abc123"},
            )),
            model_response(types.Part.from_text(text="Applied.")),
        ])
        # Only the network boundary is stubbed; AsyncChat runs the SDK's actual
        # history and automatic function-calling loop.
        modules = SimpleNamespace(
            generate_content=generate,
            _api_client=SimpleNamespace(vertexai=False),
        )
        client = SimpleNamespace(aio=SimpleNamespace(
            chats=AsyncChats(modules), aclose=AsyncMock(),
        ))
        prepare = AsyncMock(return_value={
            "status": "pending_confirmation", "confirmation_code": "abc123",
        })
        manage = AsyncMock(side_effect=[
            {"status": "pending", "message": "https://example.com/preview/test"},
            {"status": "succeeded", "message": "Applied."},
        ])
        with (
            patch.dict(os.environ, {"GEMINI_KEY": "test", "GEMINI_MODEL": "test"}),
            patch("app.services.gemini.genai.Client", return_value=client),
            patch("app.tools.sheets._get_sheets", return_value=object()),
        ):
            service = GeminiService(
                create_pending_action=prepare, manage_pending_action=manage,
            )
            service.debounce_seconds = 0
            self.addAsyncCleanup(service.close)
            respond = AsyncMock()
            origins = [
                ActionOrigin("user", "default", "chat", message_id, 1)
                for message_id in ("message-1", "message-2")
            ]
            for message, origin in zip(
                ("Create Archive and show a preview", "Confirm"), origins,
            ):
                worker = await service.queue_message(
                    sheet_session_id=1, spreadsheet_id="spreadsheet-id",
                    message=message, callback=respond,
                    start_typing=AsyncMock(), stop_typing=AsyncMock(),
                    deactivate=AsyncMock(return_value=True), origin=origin,
                )
                await worker

        prepare.assert_awaited_once_with(
            origins[0], "spreadsheet-id", "create_sheet", {"title": "Archive"},
        )
        self.assertEqual(
            [call.args for call in manage.await_args_list],
            [(origins[0], "preview", "abc123"), (origins[1], "confirm", "abc123")],
        )
        self.assertEqual(
            [call.args[0] for call in respond.await_args_list],
            ["Here is your preview.", "Applied."],
        )
        self.assertIsNone(service.current_action_origin.get())
        history = service.states[1].chat.get_history()
        responses = [
            part.function_response.response
            for content in history for part in content.parts
            if part.function_response is not None
        ]
        self.assertEqual(len(responses), 3)
        self.assertTrue(all("result" in response for response in responses))
